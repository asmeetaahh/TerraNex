"""Shared async HTTP client for outbound provider calls.

One pooled `httpx.AsyncClient` is reused for the process lifetime; creating a client
per call would discard connection pooling and TLS session reuse.

Everything here is async. A blocking call inside a request handler would stall the
whole event loop for every other user, which on a single-worker deployment means one
slow upstream freezes the entire API.
"""

import asyncio
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.base import ProviderBadResponse, ProviderTimeout

logger = get_logger(__name__)

_client: httpx.AsyncClient | None = None

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
    """GET a JSON document, retrying transient failures with exponential backoff.

    Raises `ProviderTimeout` or `ProviderBadResponse`; callers in the provider layer
    convert those into a `ProviderResult` so services never see an exception.
    """
    retries = settings.PROVIDER_MAX_RETRIES if max_retries is None else max_retries
    client = get_client()
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            response = await client.get(url, params=params)

            if _retryable_status(response.status_code):
                last_error = ProviderBadResponse(source, f"HTTP {response.status_code} from {url}")
            elif response.status_code >= 400:
                # Not retryable — fail immediately rather than burning the budget.
                raise ProviderBadResponse(source, f"HTTP {response.status_code} from {url}")
            else:
                try:
                    return response.json()
                except ValueError as exc:
                    raise ProviderBadResponse(source, f"response was not JSON: {exc}") from exc

        except (TimeoutError, httpx.TimeoutException) as exc:
            last_error = ProviderTimeout(
                source, f"no response within {settings.PROVIDER_TIMEOUT_S}s"
            )
            logger.warning(
                "provider_timeout",
                extra={"source": source, "url": url, "attempt": attempt, "error": str(exc)},
            )
        except httpx.HTTPError as exc:
            last_error = ProviderBadResponse(source, f"transport error: {exc}")
            logger.warning(
                "provider_transport_error",
                extra={"source": source, "url": url, "attempt": attempt, "error": str(exc)},
            )

        if attempt < retries:
            await asyncio.sleep(min(2**attempt * 0.25, 2.0))

    raise last_error or ProviderBadResponse(source, "request failed for an unknown reason")
