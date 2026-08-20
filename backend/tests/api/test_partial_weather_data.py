"""Analysis must survive partial provider data.

Open-Meteo omits variables it cannot supply for a location rather than sending
nulls. The parser correctly represents those as `None`; before this change the
scoring functions compared `None` against floats and returned HTTP 500 from
`POST /api/v1/farms/{id}/analysis` — the product's headline action.

The rule these tests enforce: **missing means unknown, never zero.** An absent
rainfall reading must not become "no rain", and an absent temperature must not
become a threshold comparison against `None`.

Everything here is offline: transport is mocked with respx.
"""

from datetime import date, timedelta

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from app.db.seed import demo_id, seed_demo_farms
from app.providers.cache import clear_all_caches
from app.providers.weather import FORECAST_URL
from app.services.analysis_service import INSUFFICIENT

PAST, FUTURE = 92, 16
TOTAL = PAST + FUTURE


def daily(**override):
    """A complete daily block, with individual variables removed or replaced.

    Pass `field=...` (Ellipsis) to drop it entirely — the way Open-Meteo omits a
    variable it cannot supply — or `field=[...]` to substitute values.
    """
    first = date.today() - timedelta(days=PAST)
    block = {
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
    }
    for key, value in override.items():
        if value is Ellipsis:
            block.pop(key, None)
        else:
            block[key] = value
    return {
        "latitude": -0.3031,
        "longitude": 36.08,
        "timezone": "UTC",
        "utc_offset_seconds": 0,
        "daily": block,
    }


HOURLY = {
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


@pytest.fixture
def farm_id():
    seed_demo_farms()
    return str(demo_id("farm", "nakuru-maize-field"))


@pytest.fixture
def live_client(app, monkeypatch):
    """A client with the real weather provider selected and fallback disabled, so
    degraded provider data reaches the engine instead of being papered over."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "WEATHER_PROVIDER", "open_meteo")
    monkeypatch.setattr(settings, "WEATHER_FALLBACK_TO_SIMULATION", False)
    clear_all_caches()
    # raise_app_exceptions=False mirrors what uvicorn returns to a browser.
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )


def route(daily_payload):
    def responder(request: httpx.Request) -> httpx.Response:
        if "daily" in request.url.params:
            return httpx.Response(200, json=daily_payload)
        return httpx.Response(200, json=HOURLY)

    return respx.get(FORECAST_URL).mock(side_effect=responder)


async def run_analysis(client, api_prefix, farm_id, daily_payload):
    with respx.mock:
        route(daily_payload)
        async with client as c:
            return await c.post(f"{api_prefix}/farms/{farm_id}/analysis")


def factor(run: dict, key: str) -> dict:
    return next(f for f in run["factors"] if f["key"] == key)


# --------------------------------------------------------------------------
# Every missing field must produce a usable response, not a 500
# --------------------------------------------------------------------------

MISSING_CASES = [
    ("temperature_2m_max absent", {"temperature_2m_max": ...}),
    ("temperature_2m_max all null", {"temperature_2m_max": [None] * TOTAL}),
    ("temperature_2m_max partly null", {"temperature_2m_max": [30.0] * 50 + [None] * (TOTAL - 50)}),
    ("temperature_2m_max non-numeric", {"temperature_2m_max": ["hot"] * TOTAL}),
    ("temperature_2m_min absent", {"temperature_2m_min": ...}),
    ("temperature_2m_mean absent", {"temperature_2m_mean": ...}),
    ("temperature_2m_mean non-numeric", {"temperature_2m_mean": ["warm"] * TOTAL}),
    ("humidity absent", {"relative_humidity_2m_mean": ...}),
    ("wind absent", {"wind_speed_10m_max": ...}),
    ("et0 absent", {"et0_fao_evapotranspiration": ...}),
    ("precipitation absent", {"precipitation_sum": ...}),
    ("precipitation all null", {"precipitation_sum": [None] * TOTAL}),
]


@pytest.mark.parametrize(("label", "override"), MISSING_CASES, ids=[c[0] for c in MISSING_CASES])
async def test_missing_field_never_returns_5xx(
    live_client, api_prefix, farm_id, label, override
) -> None:
    resp = await run_analysis(live_client, api_prefix, farm_id, daily(**override))

    assert resp.status_code < 500, f"{label} produced {resp.status_code}"
    assert resp.status_code == 200


@pytest.mark.parametrize(("label", "override"), MISSING_CASES, ids=[c[0] for c in MISSING_CASES])
async def test_missing_field_returns_contract_valid_run(
    live_client, api_prefix, farm_id, label, override
) -> None:
    """Every degraded response must still satisfy the frozen AnalysisRun shape."""
    run = (await run_analysis(live_client, api_prefix, farm_id, daily(**override))).json()

    for required in (
        "id",
        "farm_id",
        "status",
        "created_at",
        "duration_ms",
        "prompt_version",
        "ai_mode",
        "overall_health_score",
        "overall_band",
        "summary",
        "factors",
        "weather_risk",
        "water_risk",
        "disease_risk",
        "crop_health",
        "soil_assessment",
    ):
        assert required in run, f"{label} missing {required}"
    assert 0 <= run["overall_health_score"] <= 100
    assert run["status"] in {"complete", "partial", "failed"}


# --------------------------------------------------------------------------
# The specific fields, and what each should degrade
# --------------------------------------------------------------------------


async def test_missing_temp_max_marks_heat_stress_unassessed(
    live_client, api_prefix, farm_id
) -> None:
    run = (
        await run_analysis(live_client, api_prefix, farm_id, daily(temperature_2m_max=...))
    ).json()

    assert run["status"] == "partial"
    assert run["degraded_sources"], "the affected provider must be named"

    weather = run["weather_risk"]
    # Nullable in the contract: this is what distinguishes "no hot days" from
    # "no temperature data".
    assert weather["max_temp_c"] is None
    heat = next(f for f in weather["factors"] if f["key"] == "heat_stress")
    assert heat["weight"] == 0.0
    assert INSUFFICIENT in heat["explanation"]
    assert "maximum temperature" in heat["explanation"]

    top = factor(run, "weather_risk")
    assert top["weight"] == 0.0


async def test_nulls_inside_temp_max_do_not_crash_and_use_known_days(
    live_client, api_prefix, farm_id
) -> None:
    """A partly-populated column is still usable: score from what was reported.

    Nulls are placed in the historical portion so the forecast window — the slice
    weather risk actually reads — keeps its readings.
    """
    run = (
        await run_analysis(
            live_client,
            api_prefix,
            farm_id,
            daily(temperature_2m_max=[None] * 50 + [36.0] * (TOTAL - 50)),
        )
    ).json()

    assert run["status"] == "complete", "the forecast window had readings"
    assert run["weather_risk"]["max_temp_c"] == 36.0
    assert factor(run, "weather_risk")["weight"] > 0


async def test_nulls_across_the_forecast_window_degrade(live_client, api_prefix, farm_id) -> None:
    """The mirror case: readings only in the past leave the forecast unassessable."""
    run = (
        await run_analysis(
            live_client,
            api_prefix,
            farm_id,
            daily(temperature_2m_max=[36.0] * 50 + [None] * (TOTAL - 50)),
        )
    ).json()

    assert run["status"] == "partial"
    assert run["weather_risk"]["max_temp_c"] is None


async def test_missing_temp_min_nulls_the_measurement(live_client, api_prefix, farm_id) -> None:
    run = (
        await run_analysis(live_client, api_prefix, farm_id, daily(temperature_2m_min=...))
    ).json()

    assert run["weather_risk"]["min_temp_c"] is None
    assert run["weather_risk"]["frost_risk_days"] == 0


async def test_missing_wind_does_not_crash(live_client, api_prefix, farm_id) -> None:
    run = (
        await run_analysis(live_client, api_prefix, farm_id, daily(wind_speed_10m_max=...))
    ).json()

    assert run["weather_risk"]["high_wind_days"] == 0


async def test_missing_humidity_degrades_disease_risk(live_client, api_prefix, farm_id) -> None:
    run = (
        await run_analysis(live_client, api_prefix, farm_id, daily(relative_humidity_2m_mean=...))
    ).json()

    assert run["status"] == "partial"
    disease = run["disease_risk"]
    assert INSUFFICIENT in disease["conditions_summary"]
    assert INSUFFICIENT in disease["explanation"]
    humidity = next(f for f in disease["factors"] if f["key"] == "humidity_hours")
    assert humidity["weight"] == 0.0
    assert "humidity" in humidity["explanation"]


async def test_missing_et0_degrades_water_risk(live_client, api_prefix, farm_id) -> None:
    """Without ET₀ there is no crop demand, so there is no water balance to report."""
    run = (
        await run_analysis(live_client, api_prefix, farm_id, daily(et0_fao_evapotranspiration=...))
    ).json()

    assert run["status"] == "partial"
    water = run["water_risk"]
    # Nullable measurements stay null rather than reporting a false zero.
    assert water["total_crop_water_demand_mm"] is None
    assert water["soil_moisture_pct"] is None
    assert water["days_until_stress"] is None
    assert INSUFFICIENT in water["explanation"]
    balance = next(f for f in water["factors"] if f["key"] == "water_balance")
    assert balance["weight"] == 0.0


async def test_missing_precipitation_is_not_treated_as_zero_rain(
    live_client, api_prefix, farm_id
) -> None:
    """The silent-corruption case: absent rainfall must not become a drought."""
    run = (
        await run_analysis(live_client, api_prefix, farm_id, daily(precipitation_sum=...))
    ).json()

    weather = run["weather_risk"]
    assert weather["total_precipitation_mm"] is None, "must not report 0 mm we never measured"
    assert weather["longest_dry_spell_days"] == 0, "unmeasured days are not dry days"

    water = run["water_risk"]
    assert water["total_precipitation_mm"] is None
    assert water["deficit_mm"] == 0.0, "must not invent a deficit from absent rainfall"
    assert INSUFFICIENT in water["explanation"]


async def test_explicitly_null_precipitation_behaves_like_absent(
    live_client, api_prefix, farm_id
) -> None:
    run = (
        await run_analysis(
            live_client, api_prefix, farm_id, daily(precipitation_sum=[None] * TOTAL)
        )
    ).json()

    assert run["weather_risk"]["total_precipitation_mm"] is None
    assert run["weather_risk"]["longest_dry_spell_days"] == 0


# --------------------------------------------------------------------------
# Empty forecast — previously ValueError from max()/min()
# --------------------------------------------------------------------------


async def test_empty_daily_series_with_fallback_disabled(live_client, api_prefix, farm_id) -> None:
    """Provider down and no fallback: a degraded run, not a 500."""
    with respx.mock:
        respx.get(FORECAST_URL).mock(return_value=httpx.Response(503))
        async with live_client as c:
            resp = await c.post(f"{api_prefix}/farms/{farm_id}/analysis")

    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "partial"
    assert run["degraded_sources"] == ["open-meteo"]

    weather = run["weather_risk"]
    assert weather["max_temp_c"] is None
    assert weather["min_temp_c"] is None
    assert weather["total_precipitation_mm"] is None
    assert INSUFFICIENT in weather["explanation"]

    # The composite is still a legal score despite most factors carrying no weight.
    assert 0 <= run["overall_health_score"] <= 100


async def test_empty_series_keeps_every_factor_entry(live_client, api_prefix, farm_id) -> None:
    """The frontend iterates factors; entries must not disappear when unassessed."""
    with respx.mock:
        respx.get(FORECAST_URL).mock(return_value=httpx.Response(503))
        async with live_client as c:
            run = (await c.post(f"{api_prefix}/farms/{farm_id}/analysis")).json()

    keys = {f["key"] for f in run["factors"]}
    assert keys == {"weather_risk", "water_risk", "disease_risk", "soil_suitability", "crop_health"}
    # Soil is simulated and always available, so it must still carry weight —
    # proving the composite denominator never reaches zero here.
    assert factor(run, "soil_suitability")["weight"] > 0


# --------------------------------------------------------------------------
# Complete data is unaffected
# --------------------------------------------------------------------------


async def test_complete_data_still_produces_a_complete_run(
    live_client, api_prefix, farm_id
) -> None:
    run = (await run_analysis(live_client, api_prefix, farm_id, daily())).json()

    assert run["status"] == "complete"
    assert run["degraded_sources"] == []
    assert all(f["weight"] > 0 for f in run["factors"])
    assert INSUFFICIENT not in run["summary"]

    weather = run["weather_risk"]
    assert weather["max_temp_c"] == 30.0
    assert weather["min_temp_c"] == 20.0
    assert weather["total_precipitation_mm"] is not None
    assert run["water_risk"]["total_crop_water_demand_mm"] is not None
    assert run["crop_health"]["current_ndvi"] is not None


async def test_simulated_provider_is_unchanged(client, api_prefix, farm_id) -> None:
    """The deterministic default path must be entirely unaffected by this work."""
    resp = await client.post(f"{api_prefix}/farms/{farm_id}/analysis")

    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "complete"
    assert run["degraded_sources"] == []
    assert all(f["weight"] > 0 for f in run["factors"])


# --------------------------------------------------------------------------
# Parser-level: precipitation nullability
# --------------------------------------------------------------------------


def test_parser_preserves_missing_precipitation_as_none() -> None:
    from app.providers.weather import _parse_daily

    parsed = _parse_daily(daily(precipitation_sum=...))
    assert all(d.precipitation_mm is None for d in parsed)


def test_parser_preserves_explicit_null_precipitation() -> None:
    from app.providers.weather import _parse_daily

    parsed = _parse_daily(daily(precipitation_sum=[None] * TOTAL))
    assert all(d.precipitation_mm is None for d in parsed)


def test_parser_keeps_real_precipitation_values() -> None:
    from app.providers.weather import _parse_daily

    parsed = _parse_daily(daily(precipitation_sum=[7.5] * TOTAL))
    assert all(d.precipitation_mm == 7.5 for d in parsed)


# --------------------------------------------------------------------------
# Vegetation
# --------------------------------------------------------------------------


async def test_absent_vegetation_is_not_reported_as_bare_ground(
    client, api_prefix, farm_id, monkeypatch
) -> None:
    """NDVI 0.0 means bare rock. With no series we must report null, not zero."""
    from app.services import environment_service

    original = environment_service.gather_environment

    async def empty_vegetation(record):
        env = await original(record)
        env.vegetation = []
        return env

    monkeypatch.setattr(environment_service, "gather_environment", empty_vegetation)

    resp = await client.post(f"{api_prefix}/farms/{farm_id}/analysis")

    assert resp.status_code == 200
    run = resp.json()
    health = run["crop_health"]
    assert health["current_ndvi"] is None
    assert health["ndvi_trend"] is None
    assert INSUFFICIENT in health["explanation"]
    assert run["status"] == "partial"

    vigour = next(f for f in health["factors"] if f["key"] == "canopy_vigour")
    assert vigour["weight"] == 0.0
    assert "vegetation index" in vigour["explanation"]
