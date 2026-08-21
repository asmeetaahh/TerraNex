"""Climate realism, end to end through the API.

P1-2 proved a farm anywhere reaches the right provider with the right coordinates. It
deliberately stopped short of asking whether the *numbers* were right, because at the
time they were not: the simulator gave Aswan 762 mm of rain a year against a real ~1 mm,
so a desert farm was told it had adequate rainfall. These are the assertions that were
deferred, now that the model can carry them.

Everything here runs on the **simulated** provider, which conftest pins for the whole
suite, so nothing touches the network. That is also the path under test: simulated
weather is what a farm sees when Open-Meteo is unreachable, and it is what offline
development and demos run on.

Assertions are comparative or bounded wherever a seasonal window could move them. The
analysis window is thirty days of history plus seven of forecast, so an assertion like
"this place is not water-stressed" would hold in one month and fail in another; "this
desert gets far less rain than this rainforest" holds in every month.
"""

import pytest
from httpx import AsyncClient

from app.services import climate
from tests.fixtures.open_meteo import SITES, Site

# Split by what the model says, not by a list written out here: every site the climate
# model calls a desert must behave like one, and the classification cannot drift out of
# step with the model it is testing.
#
# The threshold separates desert from steppe rather than dry from wet. Between about
# 0.7 and 0.9 the model is describing semi-arid grassland — Bloemfontein's Free State,
# Bahir Dar on the edge of the Ethiopian Highlands — which receives real if unreliable
# rain. Asserting those places get "almost no rain" would be claiming something the
# model does not say and the world does not do.
DESERT_ARIDITY = 0.9

ARID_SITES = [s for s in SITES if climate.aridity_index(s.latitude, s.longitude) >= DESERT_ARIDITY]
HUMID_SITES = [s for s in SITES if climate.aridity_index(s.latitude, s.longitude) == 0.0]


async def register_and_plant(client: AsyncClient, api_prefix: str, site: Site, crop_id: str) -> str:
    """A farm at `site` with a primary maize planting."""
    created = await client.post(
        f"{api_prefix}/farms",
        json={"name": f"{site.name} Field", "latitude": site.latitude, "longitude": site.longitude},
    )
    assert created.status_code == 201, created.text
    farm_id = created.json()["id"]

    planted = await client.post(
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
    assert planted.status_code == 201, planted.text
    return farm_id


def test_the_matrix_contains_both_kinds_of_place() -> None:
    """A guard on the two lists above: if the model stopped distinguishing deserts from
    rainforests, every test below would pass vacuously on an empty list."""
    assert len(ARID_SITES) >= 3, "no arid validation sites — the model lost its deserts"
    assert len(HUMID_SITES) >= 3, "no humid validation sites"


# --------------------------------------------------------------------------
# Rainfall reaches the water balance
# --------------------------------------------------------------------------


@pytest.mark.parametrize("site", [pytest.param(s, id=s.key) for s in ARID_SITES])
async def test_arid_locations_receive_almost_no_rain(
    client: AsyncClient, api_prefix: str, site: Site
) -> None:
    """Over a thirty-day window a desert should record a few millimetres at most. This
    is the number that used to be ~60 mm here and drove everything downstream wrong."""
    created = await client.post(
        f"{api_prefix}/farms",
        json={"name": site.name, "latitude": site.latitude, "longitude": site.longitude},
    )
    farm_id = created.json()["id"]

    history = (await client.get(f"{api_prefix}/farms/{farm_id}/weather")).json()["history"]

    assert history["total_precipitation_mm"] < 30, (
        f"{site.name} recorded {history['total_precipitation_mm']} mm in "
        f"{history['window_days']} days"
    )


async def test_rainforests_receive_far_more_rain_than_deserts(
    client: AsyncClient, api_prefix: str, maize_crop: dict
) -> None:
    """Comparative, so it holds in every season rather than only this one."""
    wettest = max(
        HUMID_SITES, key=lambda s: climate.annual_precipitation_mm(s.latitude, s.longitude)
    )
    driest = min(ARID_SITES, key=lambda s: climate.annual_precipitation_mm(s.latitude, s.longitude))

    totals = {}
    for site in (wettest, driest):
        created = await client.post(
            f"{api_prefix}/farms",
            json={"name": site.name, "latitude": site.latitude, "longitude": site.longitude},
        )
        body = (await client.get(f"{api_prefix}/farms/{created.json()['id']}/weather")).json()
        totals[site.name] = body["history"]["total_precipitation_mm"]

    assert totals[wettest.name] > totals[driest.name] * 5, totals


# --------------------------------------------------------------------------
# Water risk — the assertion deferred from P1-2
# --------------------------------------------------------------------------


@pytest.mark.parametrize("site", [pytest.param(s, id=s.key) for s in ARID_SITES])
async def test_arid_locations_report_water_stress(
    client: AsyncClient, api_prefix: str, maize_crop: dict, site: Site
) -> None:
    """The point of the whole milestone.

    A maize crop in a desert, with rainfall near zero against a real evaporative
    demand, must come back as water-stressed. Before P2-1b the simulator invented
    enough rain to cover the demand and this reported `low`.
    """
    farm_id = await register_and_plant(client, api_prefix, site, maize_crop["id"])

    run = (await client.post(f"{api_prefix}/farms/{farm_id}/analysis")).json()
    water = run["water_risk"]

    assert water["level"] in {"high", "severe"}, f"{site.name} reported {water['level']}"
    assert water["deficit_mm"] > 0
    assert water["water_balance_mm"] < 0
    assert water["recommended_irrigation_mm"] > 0


async def test_a_desert_is_more_water_stressed_than_a_rainforest(
    client: AsyncClient, api_prefix: str, maize_crop: dict
) -> None:
    wettest = max(
        HUMID_SITES, key=lambda s: climate.annual_precipitation_mm(s.latitude, s.longitude)
    )
    driest = min(ARID_SITES, key=lambda s: climate.annual_precipitation_mm(s.latitude, s.longitude))

    deficits = {}
    for site in (wettest, driest):
        farm_id = await register_and_plant(client, api_prefix, site, maize_crop["id"])
        run = (await client.post(f"{api_prefix}/farms/{farm_id}/analysis")).json()
        deficits[site.name] = run["water_risk"]["deficit_mm"]

    assert deficits[driest.name] > deficits[wettest.name], deficits


# --------------------------------------------------------------------------
# Soil chemistry reaches the API
# --------------------------------------------------------------------------


@pytest.mark.parametrize("site", [pytest.param(s, id=s.key) for s in ARID_SITES])
async def test_desert_soil_is_reported_as_alkaline(
    client: AsyncClient, api_prefix: str, site: Site
) -> None:
    """Carbonates accumulate where evaporation beats rainfall. The old model had this
    backwards and reported desert soils as acidic."""
    created = await client.post(
        f"{api_prefix}/farms",
        json={"name": site.name, "latitude": site.latitude, "longitude": site.longitude},
    )

    soil = (await client.get(f"{api_prefix}/farms/{created.json()['id']}/soil")).json()

    assert soil["ph"] > 7.0, f"{site.name} soil came back at pH {soil['ph']}"


@pytest.mark.parametrize("site", [pytest.param(s, id=s.key) for s in HUMID_SITES])
async def test_humid_soil_is_reported_as_acidic(
    client: AsyncClient, api_prefix: str, site: Site
) -> None:
    created = await client.post(
        f"{api_prefix}/farms",
        json={"name": site.name, "latitude": site.latitude, "longitude": site.longitude},
    )

    soil = (await client.get(f"{api_prefix}/farms/{created.json()['id']}/soil")).json()

    assert soil["ph"] < 7.0, f"{site.name} soil came back at pH {soil['ph']}"


# --------------------------------------------------------------------------
# Nothing broke anywhere
# --------------------------------------------------------------------------


@pytest.mark.parametrize("site", [pytest.param(s, id=s.key) for s in SITES])
async def test_analysis_completes_at_every_location(
    client: AsyncClient, api_prefix: str, maize_crop: dict, site: Site
) -> None:
    """Realistic climate must not make the analysis engine fall over — least of all at
    the extremes, where rainfall is now near zero and radiation runs to a polar night."""
    farm_id = await register_and_plant(client, api_prefix, site, maize_crop["id"])

    response = await client.post(f"{api_prefix}/farms/{farm_id}/analysis")
    assert response.status_code == 200, response.text
    run = response.json()

    assert run["status"] in {"complete", "partial"}
    assert 0 <= run["overall_health_score"] <= 100
    assert run["factors"]

    dashboard = await client.get(f"{api_prefix}/farms/{farm_id}/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["has_analysis"] is True
