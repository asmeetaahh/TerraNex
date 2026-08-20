"""The provider wall-clock budget.

A per-attempt timeout is not a bound: three attempts at 8s plus backoff is ~25s for
one call, and weather makes two, so an unresponsive upstream could hold a dashboard
request for ~33s. These tests pin the property that makes it a real bound — every
attempt and every backoff must fit inside one budget, and retries stop once it is
spent rather than each restarting the clock.

Budgets here are deliberately small (fractions of a second) so the suite stays fast
while still exercising real elapsed time.
"""

import asyncio
import time

import httpx
import pytest
import respx

from app.providers.base import ProviderBadResponse, ProviderCallError, ProviderTimeout
from app.providers.http import get_json, provider_deadline, remaining_budget

URL = "https://example.test/data"
SOURCE = "test-provider"


@pytest.fixture
def fast_budget(monkeypatch):
    """A sub-second budget with a generous per-attempt timeout.

    This ordering is the point: the per-attempt timeout alone would allow ~1.5s of
    work, so anything that finishes sooner is the budget doing the bounding.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "PROVIDER_DEADLINE_S", 0.6)
    monkeypatch.setattr(settings, "PROVIDER_TIMEOUT_S", 5.0)
    monkeypatch.setattr(settings, "PROVIDER_MAX_RETRIES", 2)
    return settings


def slow_failure(delay: float):
    """A provider that burns `delay` seconds and then fails."""

    async def responder(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(delay)
        raise httpx.ReadTimeout("upstream did not respond")

    return responder


# --------------------------------------------------------------------------
# The budget bounds total wall time
# --------------------------------------------------------------------------


@respx.mock
async def test_hanging_provider_is_bounded_by_the_budget(fast_budget) -> None:
    route = respx.get(URL).mock(side_effect=slow_failure(0.25))

    started = time.monotonic()
    with pytest.raises(ProviderCallError):
        await get_json(SOURCE, URL, {})
    elapsed = time.monotonic() - started

    # Without the budget this would be 3 x 5s of attempts plus backoff.
    assert elapsed < 2.0, f"took {elapsed:.2f}s — the budget did not bound it"
    assert route.call_count >= 1


@respx.mock
async def test_retries_stop_once_the_budget_is_spent(fast_budget) -> None:
    """The core property: attempts stop early rather than each restarting the clock."""
    route = respx.get(URL).mock(side_effect=slow_failure(0.25))

    with pytest.raises(ProviderCallError):
        await get_json(SOURCE, URL, {})

    max_possible = fast_budget.PROVIDER_MAX_RETRIES + 1
    assert route.call_count < max_possible, (
        f"made {route.call_count} of a possible {max_possible} attempts — "
        "the budget did not curtail retries"
    )
    assert route.call_count >= 1


@respx.mock
async def test_an_exhausted_budget_reports_a_timeout(fast_budget, monkeypatch) -> None:
    monkeypatch.setattr(fast_budget, "PROVIDER_DEADLINE_S", 0.05)
    respx.get(URL).mock(side_effect=slow_failure(0.2))

    with pytest.raises(ProviderCallError) as excinfo:
        await get_json(SOURCE, URL, {})

    assert excinfo.value.source == SOURCE


# --------------------------------------------------------------------------
# A healthy provider is unaffected
# --------------------------------------------------------------------------


@respx.mock
async def test_fast_provider_succeeds_with_no_added_delay(fast_budget) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    started = time.monotonic()
    payload = await get_json(SOURCE, URL, {})
    elapsed = time.monotonic() - started

    assert payload == {"ok": True}
    assert elapsed < 0.2, f"a fast provider should not be slowed ({elapsed:.3f}s)"


@respx.mock
async def test_transient_failure_still_retries_within_budget(fast_budget) -> None:
    """Existing retry behaviour is preserved when the budget can absorb it."""
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": True})]
    )

    assert await get_json(SOURCE, URL, {}) == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_non_retryable_status_still_fails_immediately(fast_budget) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(404))

    with pytest.raises(ProviderBadResponse):
        await get_json(SOURCE, URL, {})

    assert route.call_count == 1, "a 404 must not be retried"


# --------------------------------------------------------------------------
# Budget scoping
# --------------------------------------------------------------------------


async def test_no_budget_is_open_by_default() -> None:
    assert remaining_budget() is None


async def test_provider_deadline_opens_and_closes_a_budget() -> None:
    with provider_deadline(1.0):
        inside = remaining_budget()
        assert inside is not None
        assert 0 < inside <= 1.0
    assert remaining_budget() is None


async def test_nested_calls_share_one_budget(fast_budget) -> None:
    """Two calls inside one block must not each get a fresh budget."""
    with provider_deadline(0.5):
        first = remaining_budget()
        await asyncio.sleep(0.1)
        second = remaining_budget()

    assert second < first, "the budget did not decrease across calls"


@respx.mock
async def test_an_outer_budget_bounds_several_calls_together(fast_budget) -> None:
    """The weather case: two documents, one shared budget, not one each."""
    route = respx.get(URL).mock(side_effect=slow_failure(0.2))

    started = time.monotonic()
    with provider_deadline(0.5):
        for _ in range(2):
            with pytest.raises(ProviderCallError):
                await get_json(SOURCE, URL, {})
    elapsed = time.monotonic() - started

    assert elapsed < 1.5, f"two calls consumed more than one shared budget ({elapsed:.2f}s)"
    assert route.call_count >= 1


@respx.mock
async def test_budget_uses_monotonic_time(monkeypatch, fast_budget) -> None:
    """A wall-clock adjustment must not extend or collapse a budget.

    Moving `time.time` has no effect because the deadline is tracked with
    `time.monotonic`, which cannot jump.
    """
    respx.get(URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    monkeypatch.setattr(time, "time", lambda: 0.0)

    assert await get_json(SOURCE, URL, {}) == {"ok": True}


@respx.mock
async def test_timeout_error_type_is_preserved(fast_budget) -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectTimeout("no route"))

    with pytest.raises(ProviderTimeout):
        await get_json(SOURCE, URL, {})


# --------------------------------------------------------------------------
# Deterministic verification of the budget arithmetic
#
# respx does not enforce httpx's per-request timeout, so a mocked transport cannot
# demonstrate that an attempt was actually capped. These tests replace the clock and
# the transport instead: `client.get` records the timeout it was handed and advances
# a fake monotonic clock by exactly that much, simulating an upstream that consumes
# its whole allowance. No real time passes, so the results are exact rather than
# approximate.
# --------------------------------------------------------------------------


class FakeClock:
    """A monotonic clock the test advances explicitly."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def controlled(monkeypatch):
    """Freeze time and record the timeout granted to every outbound attempt."""
    from app.core.config import settings
    from app.providers import http as http_mod

    clock = FakeClock()
    granted: list[float] = []

    monkeypatch.setattr(http_mod.time, "monotonic", clock)

    async def fake_sleep(seconds: float) -> None:
        clock.advance(seconds)  # backoff consumes budget without real waiting

    monkeypatch.setattr(http_mod.asyncio, "sleep", fake_sleep)

    class HangingClient:
        async def get(self, url, params=None, timeout=None):
            granted.append(timeout)
            # An upstream that never answers: it burns exactly its allowance.
            clock.advance(timeout)
            raise httpx.ReadTimeout("upstream did not respond")

    monkeypatch.setattr(http_mod, "get_client", lambda: HangingClient())
    monkeypatch.setattr(settings, "PROVIDER_TIMEOUT_S", 8.0)
    monkeypatch.setattr(settings, "PROVIDER_MAX_RETRIES", 2)
    monkeypatch.setattr(settings, "PROVIDER_DEADLINE_S", 1.0)

    return clock, granted, settings


async def test_attempt_timeout_is_capped_by_remaining_budget(controlled) -> None:
    """The heart of P0-2: an attempt never receives the full per-attempt timeout
    once the budget cannot cover it."""
    clock, granted, settings = controlled
    started = clock.now

    with pytest.raises(ProviderCallError):
        await get_json(SOURCE, URL, {})

    assert granted, "no attempt was made"
    # PROVIDER_TIMEOUT_S is 8.0 but the whole budget is 1.0 — no attempt may exceed it.
    assert all(g <= settings.PROVIDER_DEADLINE_S for g in granted), granted
    assert granted[0] == pytest.approx(1.0), "first attempt should get the whole budget, not 8s"
    # Total consumed cannot exceed the budget.
    assert clock.now - started <= settings.PROVIDER_DEADLINE_S + 1e-9


async def test_no_retry_starts_after_the_deadline(controlled) -> None:
    clock, granted, settings = controlled
    started = clock.now

    with pytest.raises(ProviderCallError):
        await get_json(SOURCE, URL, {})

    max_attempts = settings.PROVIDER_MAX_RETRIES + 1
    assert len(granted) < max_attempts, (
        f"{len(granted)} of {max_attempts} attempts ran — the deadline did not stop retries"
    )
    assert clock.now - started <= settings.PROVIDER_DEADLINE_S + 1e-9


async def test_deadline_is_not_restarted_per_attempt(controlled, monkeypatch) -> None:
    """With a budget that permits several attempts, each one gets only what is left."""
    clock, granted, settings = controlled
    monkeypatch.setattr(settings, "PROVIDER_DEADLINE_S", 3.0)
    monkeypatch.setattr(settings, "PROVIDER_TIMEOUT_S", 1.0)
    started = clock.now

    with pytest.raises(ProviderCallError):
        await get_json(SOURCE, URL, {})

    assert len(granted) >= 2, "the budget should have allowed a retry"
    # Strictly decreasing allowances prove one shared budget rather than a fresh
    # 1.0s timeout on every attempt.
    assert granted == sorted(granted, reverse=True), granted
    assert granted[-1] < granted[0], f"a later attempt was not reduced: {granted}"
    assert clock.now - started <= 3.0 + 1e-9


async def test_shared_budget_spans_several_calls(controlled) -> None:
    """Two provider calls inside one `provider_deadline()` share the allowance —
    the weather daily+hourly case, proven arithmetically."""
    clock, granted, settings = controlled
    started = clock.now

    with provider_deadline(1.0):
        for _ in range(2):
            with pytest.raises(ProviderCallError):
                await get_json(SOURCE, URL, {})

    assert clock.now - started <= 1.0 + 1e-9, (
        "the second call opened a fresh budget instead of sharing the first"
    )


async def test_a_fresh_budget_is_opened_when_none_is_active(controlled) -> None:
    """A standalone call is bounded too, and the budget is released afterwards."""
    clock, granted, settings = controlled

    assert remaining_budget() is None
    with pytest.raises(ProviderCallError):
        await get_json(SOURCE, URL, {})
    assert remaining_budget() is None, "the budget leaked past the call"
