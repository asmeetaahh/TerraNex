"""Shared async HTTP client for outbound provider calls.

One pooled `httpx.AsyncClient` is reused for the process lifetime; creating a client
per call would discard connection pooling and TLS session reuse.

Everything here is async. A blocking call inside a request handler would stall the
whole event loop for every other user, which on a single-worker deployment means one
slow upstream freezes the entire API.

**Provider operations run against a wall-clock budget.** A per-attempt timeout alone
is not a bound: three attempts at 8s plus backoff is ~25s for one call, and weather
makes two, so an unresponsive upstream could hold a dashboard request for ~33s.
:func:`provider_deadline` opens a budget that every nested call shares, and retries
stop once it is spent rather than each restarting the clock.

The budget is tracked with `time.monotonic()`, which cannot jump backwards when the
system clock is adjusted.
"""

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.base import ProviderBadResponse, ProviderTimeout

logger = get_logger(__name__)

_client: httpx.AsyncClient | None = None

# Absolute monotonic instant the current provider operation must finish by. A
# ContextVar rather than a parameter so nested calls inherit one budget without
# every provider threading it through its own signature.
_deadline: ContextVar[float | None] = ContextVar("provider_deadline", default=None)

USER_AGENT = f"TerraNex/{settings.APP_VERSION} (agricultural intelligence platform)"


def get_client() -> httpx.AsyncClient:
    """The shared pooled client, created lazily."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.PROVIDER_TIMEOUT_S),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def close_client() -> None:
    """Close the pooled client on shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


@contextmanager
def provider_deadline(seconds: float | None = None) -> Iterator[float]:
    """Open a wall-clock budget shared by every provider call inside the block.

    Weather fetches a daily and an hourly document; both belong to one logical
    operation, so they share one budget rather than getting one each.
    """
    budget = settings.PROVIDER_DEADLINE_S if seconds is None else seconds
    expires_at = time.monotonic() + budget
    token = _deadline.set(expires_at)
    try:
        yield expires_at
    finally:
        _deadline.reset(token)


def remaining_budget() -> float | None:
    """Seconds left in the active budget, or None when no budget is open."""
    expires_at = _deadline.get()
    return None if expires_at is None else expires_at - time.monotonic()


def _retryable_status(status_code: int) -> bool:
    """Retry only what a retry can plausibly fix.

    A 400 or 404 means the request itself is wrong; repeating it wastes the caller's
    latency budget and hammers the provider.
    """
    return status_code >= 500 or status_code == 429


async def get_json(
    source: str,
    url: str,
    params: dict[str, Any],
    *,
    max_retries: int | None = None,
) -> dict[str, Any]:
    """GET a JSON document, retrying transient failures inside a wall-clock budget.

    If a budget is already open (see :func:`provider_deadline`) this call shares it;
    otherwise it opens its own for the duration, so a standalone provider call is
    bounded too.

    Each attempt is capped at the smaller of `PROVIDER_TIMEOUT_S` and whatever is
    left, and a retry is only started when the budget can still accommodate the
    backoff. That is what makes the deadline a real bound rather than a third
    per-attempt timeout.

    Raises `ProviderTimeout` or `ProviderBadResponse`; callers in the provider layer
    convert those into a `ProviderResult` so services never see an exception.
    """
    retries = settings.PROVIDER_MAX_RETRIES if max_retries is None else max_retries
    client = get_client()
    last_error: Exception | None = None

    expires_at = _deadline.get()
    owns_budget = expires_at is None
    if expires_at is None:
        expires_at = time.monotonic() + settings.PROVIDER_DEADLINE_S
        token = _deadline.set(expires_at)

    try:
        for attempt in range(retries + 1):
            remaining = expires_at - time.monotonic()
            if remaining <= 0:
                last_error = last_error or ProviderTimeout(
                    source, f"budget of {settings.PROVIDER_DEADLINE_S}s exhausted before responding"
                )
                break

            # Never let one attempt outlive the budget it belongs to.
            attempt_timeout = min(settings.PROVIDER_TIMEOUT_S, remaining)

            try:
                response = await client.get(url, params=params, timeout=attempt_timeout)

                if _retryable_status(response.status_code):
                    last_error = ProviderBadResponse(
                        source, f"HTTP {response.status_code} from {url}"
                    )
                elif response.status_code >= 400:
                    # Not retryable — fail immediately rather than burning the budget.
                    raise ProviderBadResponse(source, f"HTTP {response.status_code} from {url}")
                else:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise ProviderBadResponse(source, f"response was not JSON: {exc}") from exc

            except (TimeoutError, httpx.TimeoutException) as exc:
                last_error = ProviderTimeout(source, f"no response within {attempt_timeout:.1f}s")
                logger.warning(
                    "provider_timeout",
                    extra={
                        "source": source,
                        "url": url,
                        "attempt": attempt,
                        "attempt_timeout_s": round(attempt_timeout, 2),
                        "error": str(exc),
                    },
                )
            except httpx.HTTPError as exc:
                last_error = ProviderBadResponse(source, f"transport error: {exc}")
                logger.warning(
                    "provider_transport_error",
                    extra={"source": source, "url": url, "attempt": attempt, "error": str(exc)},
                )

            if attempt >= retries:
                break

            # Back off only if the budget can absorb the wait and still leave time
            # for the attempt it precedes. Otherwise stop now rather than sleeping
            # into an expiry.
            backoff = min(2**attempt * 0.25, 2.0)
            if expires_at - time.monotonic() <= backoff:
                logger.warning(
                    "provider_budget_exhausted",
                    extra={"source": source, "url": url, "attempts": attempt + 1},
                )
                break
            await asyncio.sleep(backoff)

        raise last_error or ProviderBadResponse(source, "request failed for an unknown reason")
    finally:
        if owns_budget:
            _deadline.reset(token)
