"""A slow provider must not hold an API request open.

Before the budget existed, three attempts at 8s plus backoff was ~25s per provider
call, and weather makes two — so an unresponsive Open-Meteo could hold
`GET /farms/{id}/dashboard` for roughly 33 seconds, longer than most browser and
proxy timeouts, with no feedback to the user.

These tests assert the two halves of the fix: requests come back inside the budget,
and a timeout degrades through the *existing* failure path with honest provenance.
A timed-out call is never reported as `live` or `cached`.

Budgets are sub-second here so the suite stays fast while still measuring real
elapsed time. Everything is offline via respx.
"""

import asyncio
import time
from datetime import date, timedelta

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from app.db.seed import demo_id, seed_demo_farms
from app.providers.cache import clear_all_caches
from app.providers.weather import FORECAST_URL

LIVE_MODES = {"live", "cached"}
TOTAL = 108


def daily_payload():
    first = date.today() - timedelta(days=92)
    return {
        "latitude": -0.3031,
        "longitude": 36.08,
        "timezone": "UTC",
        "utc_offset_seconds": 0,
        "daily": {
            "time": [(first + timedelta(days=i)).isoformat() for i in range(TOTAL)],
            "temperature_2m_max": [30.0] * TOTAL,
            "temperature_2m_min": [20.0] * TOTAL,
            "temperature_2m_mean": [25.0] * TOTAL,
            "relative_humidity_2m_mean": [60.0] * TOTAL,
            "precipitation_sum": [1.0] * TOTAL,
            "precipitation_hours": [2.0] * TOTAL,
            "wind_speed_10m_max": [10.0] * TOTAL,
            "et0_fao_evapotranspiration": [4.0] * TOTAL,
            "shortwave_radiation_sum": [20.0] * TOTAL,
            "cloud_cover_mean": [30.0] * TOTAL,
            "weather_code": [1] * TOTAL,
        },
    }


HOURLY_PAYLOAD = {
    "latitude": -0.3031,
    "longitude": 36.08,
    "timezone": "UTC",
    "utc_offset_seconds": 0,
    "current": {
        "time": f"{date.today().isoformat()}T09:00",
        "temperature_2m": 24.0,
        "weather_code": 1,
    },
    "hourly": {
        "time": [f"{date.today().isoformat()}T{h:02d}:00" for h in range(24)],
        "temperature_2m": [24.0] * 24,
    },
}


def hanging_provider(delay: float = 0.25):
    """An upstream that burns time and then fails to respond."""

    async def responder(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(delay)
        raise httpx.ReadTimeout("upstream did not respond")

    return respx.get(FORECAST_URL).mock(side_effect=responder)


def healthy_provider():
    def responder(request: httpx.Request) -> httpx.Response:
        if "daily" in request.url.params:
            return httpx.Response(200, json=daily_payload())
        return httpx.Response(200, json=HOURLY_PAYLOAD)

    return respx.get(FORECAST_URL).mock(side_effect=responder)


@pytest.fixture
def farm_id():
    seed_demo_farms()
    return str(demo_id("farm", "nakuru-maize-field"))


@pytest.fixture
def live_settings(monkeypatch):
    """Real provider selected, with a sub-second budget and a generous per-attempt
    timeout — so anything that returns quickly was bounded by the budget."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "WEATHER_PROVIDER", "open_meteo")
    monkeypatch.setattr(settings, "PROVIDER_DEADLINE_S", 0.6)
    monkeypatch.setattr(settings, "PROVIDER_TIMEOUT_S", 5.0)
    monkeypatch.setattr(settings, "PROVIDER_MAX_RETRIES", 2)
    clear_all_caches()
    return settings


def client_for(app):
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )


# --------------------------------------------------------------------------
# 1. A hanging provider does not hold the request open
# --------------------------------------------------------------------------


@respx.mock
async def test_hanging_provider_returns_within_the_budget(
    app, api_prefix, farm_id, live_settings
) -> None:
    hanging_provider()

    started = time.monotonic()
    async with client_for(app) as c:
        resp = await c.get(f"{api_prefix}/farms/{farm_id}/weather")
    elapsed = time.monotonic() - started

    assert resp.status_code == 200
    # The old behaviour was ~33s. The bound here is deliberately loose so the test
    # measures the budget rather than machine speed.
    assert elapsed < 3.0, f"request took {elapsed:.2f}s — not bounded by the budget"


@respx.mock
async def test_dashboard_is_bounded_too(app, api_prefix, farm_id, live_settings) -> None:
    """The endpoint the frontend depends on most."""
    hanging_provider()

    started = time.monotonic()
    async with client_for(app) as c:
        resp = await c.get(f"{api_prefix}/farms/{farm_id}/dashboard")
    elapsed = time.monotonic() - started

    assert resp.status_code == 200
    assert elapsed < 3.0, f"dashboard took {elapsed:.2f}s"


@respx.mock
async def test_analysis_is_bounded_too(app, api_prefix, farm_id, live_settings) -> None:
    hanging_provider()

    started = time.monotonic()
    async with client_for(app) as c:
        resp = await c.post(f"{api_prefix}/farms/{farm_id}/analysis")
    elapsed = time.monotonic() - started

    assert resp.status_code == 200
    assert elapsed < 3.0, f"analysis took {elapsed:.2f}s"


# --------------------------------------------------------------------------
# 2. Fallback enabled — simulated, and labelled so
# --------------------------------------------------------------------------


@respx.mock
async def test_timeout_with_fallback_reports_simulated(
    app, api_prefix, farm_id, live_settings, monkeypatch
) -> None:
    monkeypatch.setattr(live_settings, "WEATHER_FALLBACK_TO_SIMULATION", True)
    hanging_provider()

    async with client_for(app) as c:
        body = (await c.get(f"{api_prefix}/farms/{farm_id}/weather")).json()

    assert body["meta"]["mode"] == "simulated"
    assert body["meta"]["mode"] not in LIVE_MODES, "a timed-out call must never read live"
    assert body["daily"], "the product stays usable"
    assert "unavailable" in body["meta"]["note"].lower()


@respx.mock
async def test_timeout_with_fallback_names_the_provider_as_degraded(
    app, api_prefix, farm_id, live_settings, monkeypatch
) -> None:
    monkeypatch.setattr(live_settings, "WEATHER_FALLBACK_TO_SIMULATION", True)
    hanging_provider()

    async with client_for(app) as c:
        run = (await c.post(f"{api_prefix}/farms/{farm_id}/analysis")).json()

    assert "open-meteo" in run["degraded_sources"]
    assert all(s["mode"] != "live" for s in run["sources"])
    assert all(s["mode"] != "cached" for s in run["sources"])


# --------------------------------------------------------------------------
# 3. Fallback disabled — unavailable, and labelled so
# --------------------------------------------------------------------------


@respx.mock
async def test_timeout_without_fallback_reports_unavailable(
    app, api_prefix, farm_id, live_settings, monkeypatch
) -> None:
    monkeypatch.setattr(live_settings, "WEATHER_FALLBACK_TO_SIMULATION", False)
    hanging_provider()

    async with client_for(app) as c:
        resp = await c.get(f"{api_prefix}/farms/{farm_id}/weather")

    assert resp.status_code == 200, "no provider exception may escape as a 500"
    body = resp.json()
    assert body["meta"]["mode"] == "unavailable"
    assert body["meta"]["mode"] not in LIVE_MODES
    assert body["daily"] == []
    assert body["current"] is None


@respx.mock
async def test_timeout_without_fallback_still_produces_an_analysis(
    app, api_prefix, farm_id, live_settings, monkeypatch
) -> None:
    """Degradation flows through the P0-1 partial path, not a 500."""
    monkeypatch.setattr(live_settings, "WEATHER_FALLBACK_TO_SIMULATION", False)
    hanging_provider()

    async with client_for(app) as c:
        resp = await c.post(f"{api_prefix}/farms/{farm_id}/analysis")

    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "partial"
    assert "open-meteo" in run["degraded_sources"]
    assert 0 <= run["overall_health_score"] <= 100


# --------------------------------------------------------------------------
# 4. Retry budget
# --------------------------------------------------------------------------


@respx.mock
async def test_retries_stop_once_the_budget_is_spent(
    app, api_prefix, farm_id, live_settings
) -> None:
    route = hanging_provider(delay=0.25)

    async with client_for(app) as c:
        await c.get(f"{api_prefix}/farms/{farm_id}/weather")

    # Weather would otherwise make 2 documents x 3 attempts = 6 upstream calls.
    max_possible = (live_settings.PROVIDER_MAX_RETRIES + 1) * 2
    assert route.call_count < max_possible, (
        f"{route.call_count} of a possible {max_possible} attempts — budget not enforced"
    )
    assert route.call_count >= 1


# --------------------------------------------------------------------------
# 5. A healthy provider is untouched
# --------------------------------------------------------------------------


@respx.mock
async def test_fast_provider_still_reports_live(app, api_prefix, farm_id, live_settings) -> None:
    healthy_provider()

    started = time.monotonic()
    async with client_for(app) as c:
        body = (await c.get(f"{api_prefix}/farms/{farm_id}/weather")).json()
    elapsed = time.monotonic() - started

    assert body["meta"]["mode"] == "live"
    assert body["meta"]["source"] == "open-meteo"
    assert body["timezone"] == "UTC"
    assert len(body["daily"]) == 7
    assert elapsed < 1.0, f"a healthy provider was slowed ({elapsed:.2f}s)"


@respx.mock
async def test_caching_behaviour_is_unchanged(app, api_prefix, farm_id, live_settings) -> None:
    """A successful call still populates the cache; the next read is `cached`."""
    route = healthy_provider()

    async with client_for(app) as c:
        first = (await c.get(f"{api_prefix}/farms/{farm_id}/weather")).json()
        second = (await c.get(f"{api_prefix}/farms/{farm_id}/weather")).json()

    assert first["meta"]["mode"] == "live"
    assert second["meta"]["mode"] == "cached"
    assert route.call_count == 2, "two documents fetched once each, then served from cache"


@respx.mock
async def test_timeouts_are_not_cached(app, api_prefix, farm_id, live_settings) -> None:
    """A provider down for one request must be retried on the next, not remembered."""

    def responder(request: httpx.Request) -> httpx.Response:
        if "daily" in request.url.params:
            return httpx.Response(200, json=daily_payload())
        return httpx.Response(200, json=HOURLY_PAYLOAD)

    hanging_provider()
    async with client_for(app) as c:
        failed = (await c.get(f"{api_prefix}/farms/{farm_id}/weather")).json()
    assert failed["meta"]["mode"] not in LIVE_MODES

    respx.get(FORECAST_URL).mock(side_effect=responder)
    async with client_for(app) as c:
        recovered = (await c.get(f"{api_prefix}/farms/{farm_id}/weather")).json()

    assert recovered["meta"]["mode"] == "live", "the failure was cached"


# --------------------------------------------------------------------------
# 6. The simulated path never engages the deadline machinery
# --------------------------------------------------------------------------


async def test_simulated_provider_opens_no_budget(monkeypatch) -> None:
    from app.core.config import settings
    from app.providers import weather
    from app.providers.http import remaining_budget

    monkeypatch.setattr(settings, "WEATHER_PROVIDER", "simulated")
    observed: list[float | None] = []

    original = weather.simulated_observations

    def spy(*args, **kwargs):
        observed.append(remaining_budget())
        return original(*args, **kwargs)

    monkeypatch.setattr(weather, "simulated_observations", spy)

    result = await weather.get_observations(-0.3031, 36.08)

    assert result.meta.mode.value == "simulated"
    assert observed == [None], "the simulated path must not run inside a provider budget"


async def test_simulated_provider_output_is_unchanged(client, api_prefix, farm_id) -> None:
    """The deterministic default path is untouched by P0-2."""
    resp = await client.post(f"{api_prefix}/farms/{farm_id}/analysis")

    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "complete"
    assert run["degraded_sources"] == []
    assert all(s["mode"] == "simulated" for s in run["sources"])
