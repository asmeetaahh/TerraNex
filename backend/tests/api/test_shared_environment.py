"""The shared environment snapshot.

The guarantee under test: `/weather`, `/soil`, `/vegetation`, `POST /analysis` and the
dashboard all describe **one** environment. Before this phase, analysis called the
simulator directly, so a real weather provider would have produced a dashboard showing
genuine conditions beside risk scores derived from invented ones — with provenance
claiming both were the same thing.

These tests make that state unrepresentable rather than merely unlikely.
"""

import inspect
from datetime import date, timedelta

import httpx
import pytest
import respx
from httpx import AsyncClient

from app.providers.weather import FORECAST_URL
from app.services import analysis_service
from tests.fixtures.open_meteo import daily_payload, hourly_payload

# --------------------------------------------------------------------------
# Structural: analysis cannot reach the simulator on its own
# --------------------------------------------------------------------------


def test_analysis_service_does_not_import_the_simulator_directly() -> None:
    """A regression guard on the architecture, not just the behaviour.

    `seeded_rng` is allowed — it seeds narrative jitter, not environmental data.
    """
    source = inspect.getsource(analysis_service)

    for forbidden in ("simulate_days", "simulate_day(", "simulate_soil", "simulate_ndvi"):
        assert forbidden not in source, (
            f"analysis_service calls {forbidden} directly; environmental data must "
            "come from the shared snapshot so analysis and /weather cannot diverge."
        )


def test_analysis_sections_take_the_snapshot() -> None:
    """Every scoring function must receive the snapshot rather than a farm record it
    could re-derive conditions from."""
    for name in (
        "_weather_risk",
        "_water_risk",
        "_disease_risk",
        "_soil_assessment",
        "_crop_health",
        "_crop_recommendations",
        "_regenerative_recommendations",
    ):
        signature = inspect.signature(getattr(analysis_service, name))
        assert "env" in signature.parameters, f"{name} does not take the shared snapshot"


# --------------------------------------------------------------------------
# Behavioural: the numbers agree
# --------------------------------------------------------------------------


async def test_weather_and_analysis_agree_on_the_forecast(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """The weather panel and the weather-risk panel must describe the same days."""
    farm_id = planted_farm["id"]

    bundle = (await client.get(f"{api_prefix}/farms/{farm_id}/weather")).json()
    run = (await client.post(f"{api_prefix}/farms/{farm_id}/analysis")).json()

    forecast_max = max(d["temp_max_c"] for d in bundle["daily"])
    forecast_min = min(d["temp_min_c"] for d in bundle["daily"])
    forecast_rain = round(sum(d["precipitation_mm"] for d in bundle["daily"]), 1)

    weather_risk = run["weather_risk"]
    assert weather_risk["max_temp_c"] == forecast_max
    assert weather_risk["min_temp_c"] == forecast_min
    assert weather_risk["total_precipitation_mm"] == pytest.approx(forecast_rain, abs=0.15)


async def test_soil_endpoint_and_analysis_agree(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    farm_id = planted_farm["id"]

    soil = (await client.get(f"{api_prefix}/farms/{farm_id}/soil")).json()
    run = (await client.post(f"{api_prefix}/farms/{farm_id}/analysis")).json()

    assert run["soil_assessment"]["texture_class"] == soil["texture_class"]
    assert run["water_risk"]["water_holding_capacity_mm"] == soil["water_holding_capacity_mm"]


async def test_vegetation_endpoint_and_crop_health_agree(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    farm_id = planted_farm["id"]

    vegetation = (await client.get(f"{api_prefix}/farms/{farm_id}/vegetation")).json()
    run = (await client.post(f"{api_prefix}/farms/{farm_id}/analysis")).json()

    assert run["crop_health"]["current_ndvi"] == vegetation["current_ndvi"]


async def test_water_balance_matches_the_reported_history(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """The 30-day water balance must be built from the same 30 days `/weather` reports."""
    farm_id = planted_farm["id"]

    bundle = (await client.get(f"{api_prefix}/farms/{farm_id}/weather")).json()
    run = (await client.post(f"{api_prefix}/farms/{farm_id}/analysis")).json()

    history_rain = bundle["history"]["total_precipitation_mm"]
    forecast_rain = sum(d["precipitation_mm"] for d in bundle["daily"])
    total = run["water_risk"]["total_precipitation_mm"]

    # Analysis uses 30 days of history plus 7 forecast days; the bundle reports 30
    # history days and 7 forecast days by default, so the totals must line up.
    assert total == pytest.approx(history_rain + forecast_rain, abs=0.15)


async def test_dashboard_weather_matches_the_weather_endpoint(
    client: AsyncClient, api_prefix: str, analyzed_farm: dict
) -> None:
    farm_id = analyzed_farm["id"]

    bundle = (await client.get(f"{api_prefix}/farms/{farm_id}/weather")).json()
    dashboard = (await client.get(f"{api_prefix}/farms/{farm_id}/dashboard")).json()

    assert dashboard["current_weather"]["temperature_c"] == bundle["current"]["temperature_c"]
    assert dashboard["current_weather"]["humidity_pct"] == bundle["current"]["humidity_pct"]


# --------------------------------------------------------------------------
# Provenance flows through
# --------------------------------------------------------------------------


async def test_analysis_provenance_matches_its_inputs(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """With the simulator configured, every source must say simulated."""
    farm_id = planted_farm["id"]

    weather = (await client.get(f"{api_prefix}/farms/{farm_id}/weather")).json()
    run = (await client.post(f"{api_prefix}/farms/{farm_id}/analysis")).json()

    assert weather["meta"]["mode"] == "simulated"
    assert [s["mode"] for s in run["sources"]] == ["simulated"] * 3
    assert "simulated" in run["summary"].lower()


async def test_dashboard_freshness_comes_from_the_snapshot(
    client: AsyncClient, api_prefix: str, analyzed_farm: dict
) -> None:
    dashboard = (await client.get(f"{api_prefix}/farms/{analyzed_farm['id']}/dashboard")).json()

    assert dashboard["data_freshness"]
    assert all(m["mode"] == "simulated" for m in dashboard["data_freshness"])


# --------------------------------------------------------------------------
# With a live provider, provenance flips everywhere at once
# --------------------------------------------------------------------------


def _open_meteo_route():
    def responder(request: httpx.Request) -> httpx.Response:
        if "daily" in request.url.params:
            return httpx.Response(200, json=daily_payload())
        return httpx.Response(200, json=hourly_payload())

    return respx.get(FORECAST_URL).mock(side_effect=responder)


@respx.mock
async def test_live_weather_propagates_into_analysis(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    """The end-to-end point of this phase: real weather reaches the risk engine, and
    the run says so instead of claiming everything was simulated."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "WEATHER_PROVIDER", "open_meteo")
    _open_meteo_route()

    farm_id = planted_farm["id"]
    bundle = (await client.get(f"{api_prefix}/farms/{farm_id}/weather")).json()
    run = (await client.post(f"{api_prefix}/farms/{farm_id}/analysis")).json()

    assert bundle["meta"]["mode"] in {"live", "cached"}
    assert bundle["meta"]["source"] == "open-meteo"
    assert bundle["timezone"] == "Asia/Kolkata"

    modes = {s["source"]: s["mode"] for s in run["sources"]}
    assert modes["open-meteo"] in {"live", "cached"}
    assert modes["simulated"] == "simulated"  # soil and vegetation are still generated
    assert "real data from open-meteo" in run["summary"].lower()

    # The risk engine consumed the provider's numbers, not the simulator's.
    assert run["weather_risk"]["max_temp_c"] == max(d["temp_max_c"] for d in bundle["daily"])


@respx.mock
async def test_provider_failure_falls_back_but_says_simulated(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    """Degradation must never be silent: substituted data is labelled simulated."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "WEATHER_PROVIDER", "open_meteo")
    monkeypatch.setattr(settings, "WEATHER_FALLBACK_TO_SIMULATION", True)
    respx.get(FORECAST_URL).mock(return_value=httpx.Response(503))

    bundle = (await client.get(f"{api_prefix}/farms/{planted_farm['id']}/weather")).json()

    assert bundle["meta"]["mode"] == "simulated"
    assert bundle["meta"]["mode"] not in {"live", "cached"}
    assert "unavailable" in bundle["meta"]["note"].lower()
    assert bundle["daily"], "the product stays usable"


@respx.mock
async def test_fallback_can_be_disabled_to_report_unavailable(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "WEATHER_PROVIDER", "open_meteo")
    monkeypatch.setattr(settings, "WEATHER_FALLBACK_TO_SIMULATION", False)
    respx.get(FORECAST_URL).mock(return_value=httpx.Response(503))

    bundle = (await client.get(f"{api_prefix}/farms/{planted_farm['id']}/weather")).json()

    assert bundle["meta"]["mode"] == "unavailable"
    assert bundle["daily"] == []
    assert bundle["current"] is None


# --------------------------------------------------------------------------
# degraded_sources reports faults, not intent
# --------------------------------------------------------------------------


async def test_degraded_sources_is_empty_when_nothing_failed(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """Simulated soil and vegetation are a stated capability of this phase, not a
    degradation. Listing them would make a genuine outage indistinguishable from
    normal operation."""
    run = (await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")).json()

    assert run["degraded_sources"] == []
    # Provenance is still reported in full, per input.
    assert [s["mode"] for s in run["sources"]] == ["simulated"] * 3


@respx.mock
async def test_degraded_sources_names_the_failed_provider(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "WEATHER_PROVIDER", "open_meteo")
    monkeypatch.setattr(settings, "WEATHER_FALLBACK_TO_SIMULATION", True)
    respx.get(FORECAST_URL).mock(return_value=httpx.Response(503))

    run = (await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")).json()

    assert run["degraded_sources"] == ["open-meteo"]
    assert run["status"] in {"complete", "partial"}


@respx.mock
async def test_degraded_sources_is_deduplicated(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    """One failing provider must appear once, however many calls it broke."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "WEATHER_PROVIDER", "open_meteo")
    respx.get(FORECAST_URL).mock(return_value=httpx.Response(500))

    run = (await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")).json()

    assert run["degraded_sources"] == sorted(set(run["degraded_sources"]))
    assert len(run["degraded_sources"]) == len(set(run["degraded_sources"]))


@respx.mock
async def test_healthy_live_provider_reports_no_degradation(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "WEATHER_PROVIDER", "open_meteo")
    _open_meteo_route()

    run = (await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")).json()

    assert run["degraded_sources"] == []


# --------------------------------------------------------------------------
# Exact coordinates
# --------------------------------------------------------------------------


@respx.mock
async def test_selected_coordinates_are_used_verbatim(
    client: AsyncClient, api_prefix: str, monkeypatch
) -> None:
    """The hard requirement: once a user selects a location, every provider request
    uses those exact coordinates. Nothing infers another place from the farm name."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "WEATHER_PROVIDER", "open_meteo")
    route = _open_meteo_route()

    # A farm named after one city but positioned at another's coordinates: only the
    # coordinates may reach the provider.
    created = await client.post(
        f"{api_prefix}/farms",
        json={"name": "Mumbai Farm", "latitude": 19.99727, "longitude": 73.79096},
    )
    farm_id = created.json()["id"]

    await client.get(f"{api_prefix}/farms/{farm_id}/weather")

    daily_call = next(c for c in route.calls if "daily" in c.request.url.params)
    assert float(daily_call.request.url.params["latitude"]) == 19.99727
    assert float(daily_call.request.url.params["longitude"]) == 73.79096
    assert "Mumbai" not in str(daily_call.request.url)


# --------------------------------------------------------------------------
# Window coherence
# --------------------------------------------------------------------------


async def test_overlapping_windows_report_identical_days(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    """A 7-day panel and a 14-day panel must agree on the days they share — they are
    slices of one snapshot, not two independent generations."""
    farm_id = farm["id"]

    narrow = (
        await client.get(f"{api_prefix}/farms/{farm_id}/weather", params={"forecast_days": 7})
    ).json()
    wide = (
        await client.get(f"{api_prefix}/farms/{farm_id}/weather", params={"forecast_days": 14})
    ).json()

    by_date = {d["date"]: d for d in wide["daily"]}
    for day in narrow["daily"]:
        assert by_date[day["date"]] == day


async def test_history_windows_are_consistent(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    farm_id = farm["id"]
    today = date.today()

    thirty = (
        await client.get(f"{api_prefix}/farms/{farm_id}/weather", params={"history_days": 30})
    ).json()["history"]
    ninety = (
        await client.get(f"{api_prefix}/farms/{farm_id}/weather", params={"history_days": 90})
    ).json()["history"]

    assert thirty["window_days"] == 30
    assert ninety["window_days"] == 90
    assert date.fromisoformat(thirty["start_date"]) == today - timedelta(days=30)
    # A longer window can only accumulate more rain.
    assert ninety["total_precipitation_mm"] >= thirty["total_precipitation_mm"]
