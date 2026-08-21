"""Global location readiness: the real-provider path works anywhere on Earth.

The audit that opened P1 asked one question — does anything in this stack quietly
assume India, or Bengaluru, or the northern hemisphere? These tests answer it by
driving twelve real places, spanning both hemispheres, latitudes from 3°N to 69°N and
eleven distinct IANA timezones, through the **real** Open-Meteo code path.

Scope is deliberately narrow: coordinates, timezones, provider selection and the
end-to-end path. Whether the numbers are climatically right for an arid or polar site
belongs to the simulator/climate milestone — asserting it here would fail for reasons
that have nothing to do with global provider readiness.

Everything is offline. `settings` is monkeypatched to select Open-Meteo and respx
mocks both Open-Meteo hosts, so no test reaches the network.
"""

import pytest
import respx
from httpx import AsyncClient

from app.core.config import settings
from tests.fixtures.open_meteo import (
    ASWAN,
    BLOEMFONTEIN,
    KRASNODAR,
    MURMANSK,
    RIYADH,
    SITES,
    Site,
    geocoding_route,
    matrix_weather_route,
)

# A farm named after a city it is nowhere near. Every location in the matrix is
# registered under this name, so any test that still reaches the right weather is
# proving the coordinates carried the request — not the name.
DECOY_NAME = "Bengaluru Farm"

# Requesting the matrix by `key` keeps pytest's case ids readable and stable.
SITE_PARAMS = [pytest.param(site, id=site.key) for site in SITES]


@pytest.fixture(autouse=True)
def use_open_meteo(monkeypatch):
    """Opt this module into the real providers.

    conftest pins the process environment to `simulated` so the suite is offline by
    default; selection reads `settings` at call time, so patching the live object here
    routes these tests down the Open-Meteo branch without unpinning anything else.
    """
    monkeypatch.setattr(settings, "WEATHER_PROVIDER", "open_meteo")
    monkeypatch.setattr(settings, "GEOCODING_PROVIDER", "open_meteo")


async def register_farm(client: AsyncClient, api_prefix: str, site: Site) -> str:
    """Register a farm at `site`'s real coordinates under the decoy name."""
    response = await client.post(
        f"{api_prefix}/farms",
        json={"name": DECOY_NAME, "latitude": site.latitude, "longitude": site.longitude},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def plant_maize(client: AsyncClient, api_prefix: str, farm_id: str, crop_id: str) -> None:
    response = await client.post(
        f"{api_prefix}/farms/{farm_id}/crops",
        json={
            "crop_id": crop_id,
            "growth_stage": "flowering",
            "is_primary": True,
            "planting_date": "2026-04-01",
            "expected_harvest_date": "2026-09-01",
            "status": "growing",
        },
    )
    assert response.status_code == 201, response.text


# --------------------------------------------------------------------------
# Matrix 1 — geocoding resolves each place to its own real coordinates
# --------------------------------------------------------------------------


@pytest.mark.parametrize("site", SITE_PARAMS)
@respx.mock
async def test_location_geocodes_to_its_own_coordinates(
    client: AsyncClient, api_prefix: str, site: Site
) -> None:
    """Whatever the provider returns is what the picker offers — unrounded, unadjusted,
    and attributed to the right country."""
    geocoding_route(site)

    response = await client.get(f"{api_prefix}/reference/locations", params={"q": site.name})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["mode"] in {"live", "cached"}
    top = body["items"][0]
    assert top["latitude"] == pytest.approx(site.latitude, abs=1e-4)
    assert top["longitude"] == pytest.approx(site.longitude, abs=1e-4)
    assert top["country_code"] == site.country_code


# --------------------------------------------------------------------------
# Matrix 2 — the farm's own coordinates are what reach the provider
# --------------------------------------------------------------------------


@pytest.mark.parametrize("site", SITE_PARAMS)
@respx.mock
async def test_weather_requests_the_farms_exact_coordinates(
    client: AsyncClient, api_prefix: str, site: Site
) -> None:
    """The hidden-assumption test, run everywhere.

    Each farm is named "Bengaluru Farm" but sits somewhere else entirely. Only the
    stored coordinates may leave the process; if any layer re-derived a position from
    the name, the outbound request would carry Bengaluru's coordinates or the name
    itself.
    """
    route = matrix_weather_route()
    farm_id = await register_farm(client, api_prefix, site)

    response = await client.get(f"{api_prefix}/farms/{farm_id}/weather")
    assert response.status_code == 200, response.text

    assert route.called, "no request reached the weather provider"
    for call in route.calls:
        url = call.request.url
        assert float(url.params["latitude"]) == site.latitude
        assert float(url.params["longitude"]) == site.longitude
        assert "Bengaluru" not in str(url)
        assert site.name not in str(url)


# --------------------------------------------------------------------------
# Matrix 3 — each location reports its own timezone
# --------------------------------------------------------------------------


@pytest.mark.parametrize("site", SITE_PARAMS)
@respx.mock
async def test_weather_reports_the_locations_own_timezone(
    client: AsyncClient, api_prefix: str, site: Site
) -> None:
    """A single global default — UTC, or the developer's own zone — would show here as
    every location reporting the same string."""
    matrix_weather_route()
    farm_id = await register_farm(client, api_prefix, site)

    bundle = (await client.get(f"{api_prefix}/farms/{farm_id}/weather")).json()

    assert bundle["timezone"] == site.timezone


# --------------------------------------------------------------------------
# End to end — four representative cases through analysis and the dashboard
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "site",
    [
        pytest.param(KRASNODAR, id="mid_latitude"),
        pytest.param(BLOEMFONTEIN, id="southern_hemisphere"),
        pytest.param(MURMANSK, id="extreme_latitude"),
        pytest.param(RIYADH, id="arid"),
    ],
)
@respx.mock
async def test_analysis_and_dashboard_complete_at_the_location(
    client: AsyncClient, api_prefix: str, maize_crop: dict, site: Site
) -> None:
    """The whole path — register, plant, analyse, render — at four places chosen to
    break different assumptions: a northern mid-latitude, a southern-hemisphere site, a
    farm above 68°N, and a desert.

    This asserts the path completes and stays live, not what the scores ought to be.
    """
    # The coordinate-resolving route, so a request carrying the wrong position fails
    # at the mock rather than being served this site's weather anyway.
    matrix_weather_route()
    farm_id = await register_farm(client, api_prefix, site)
    await plant_maize(client, api_prefix, farm_id, maize_crop["id"])

    analysis = await client.post(f"{api_prefix}/farms/{farm_id}/analysis")
    assert analysis.status_code == 200, analysis.text
    run = analysis.json()

    assert run["status"] in {"complete", "partial"}
    assert 0 <= run["overall_health_score"] <= 100
    assert run["factors"], "no factors were scored"

    # The real provider supplied the weather; nothing fell back silently.
    modes = {s["source"]: s["mode"] for s in run["sources"]}
    assert modes["open-meteo"] in {"live", "cached"}
    assert run["degraded_sources"] == []

    dashboard = await client.get(f"{api_prefix}/farms/{farm_id}/dashboard")
    assert dashboard.status_code == 200, dashboard.text
    board = dashboard.json()

    assert board["has_analysis"] is True
    assert board["current_weather"] is not None
    assert board["farm"]["latitude"] == pytest.approx(site.latitude)


# --------------------------------------------------------------------------
# Provider selection, timezone diversity, cache isolation
# --------------------------------------------------------------------------


def test_default_provider_is_open_meteo() -> None:
    """A fresh clone with no configuration must resolve real places anywhere.

    The *declared* default, not a resolved instance: pydantic-settings still reads the
    process environment, which this suite pins to `simulated`.
    """
    from app.core.config import Settings

    assert Settings.model_fields["WEATHER_PROVIDER"].default == "open_meteo"
    assert Settings.model_fields["GEOCODING_PROVIDER"].default == "open_meteo"


@respx.mock
async def test_locations_do_not_share_a_timezone(client: AsyncClient, api_prefix: str) -> None:
    """Driven through the app rather than read off the fixture table.

    Twelve farms, eleven distinct zones. Any layer collapsing timezones to one value —
    a hardcoded "UTC", the host's zone, the first farm's — cannot satisfy this.
    """
    matrix_weather_route()

    reported = []
    for site in SITES:
        farm_id = await register_farm(client, api_prefix, site)
        bundle = (await client.get(f"{api_prefix}/farms/{farm_id}/weather")).json()
        reported.append(bundle["timezone"])

    assert len(reported) == len(SITES)
    assert len(set(reported)) >= 8, f"timezones collapsed: {sorted(set(reported))}"


@respx.mock
async def test_locations_do_not_share_a_cache_entry(client: AsyncClient, api_prefix: str) -> None:
    """Cache keys round coordinates to 3 dp (~110 m), so two farms on opposite sides of
    the world must never be served each other's weather."""
    route = matrix_weather_route()

    murmansk_id = await register_farm(client, api_prefix, MURMANSK)
    aswan_id = await register_farm(client, api_prefix, ASWAN)

    murmansk = (await client.get(f"{api_prefix}/farms/{murmansk_id}/weather")).json()
    aswan = (await client.get(f"{api_prefix}/farms/{aswan_id}/weather")).json()

    # Two documents (daily + hourly) fetched for each location, not shared.
    assert route.call_count == 4
    assert murmansk["timezone"] == MURMANSK.timezone
    assert aswan["timezone"] == ASWAN.timezone
    assert murmansk["latitude"] == pytest.approx(MURMANSK.latitude)
    assert aswan["latitude"] == pytest.approx(ASWAN.latitude)
