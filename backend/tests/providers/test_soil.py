"""The ISRIC SoilGrids adapter.

Two things are being defended here.

**Units.** SoilGrids stores integers to keep its rasters compact — pH is multiplied by
ten, carbon is decigrams per kilogram, particle sizes are grams per kilogram. Getting a
divisor wrong does not raise; it produces a soil at pH 64 or a clay at 0.2% carbon that
then drives real fertiliser advice. Every conversion is therefore asserted against a
known value.

**Provenance.** Live, cached, unavailable and simulated must stay distinguishable all
the way to the response. A simulated soil relabelled as live is the failure this whole
architecture exists to prevent.

Everything is offline: respx mocks the ISRIC host, so no test reaches the network.
"""

import httpx
import pytest
import respx

from app.core.config import settings
from app.providers import soil as soil_provider
from app.providers.base import SoilObservation
from app.providers.cache import get_cache
from app.providers.soil import SOILGRIDS_SOURCE, SOILGRIDS_URL, parse_soilgrids
from app.schemas.common import DataMode
from app.schemas.enums import SoilTexture


@pytest.fixture(autouse=True)
def _use_soilgrids(monkeypatch):
    """Opt this module into the real provider. conftest pins the suite to `simulated`."""
    monkeypatch.setattr(settings, "SOIL_PROVIDER", "soilgrids")
    get_cache("soil", settings.CACHE_TTL_SOIL_S).clear()
    yield
    get_cache("soil", settings.CACHE_TTL_SOIL_S).clear()


def layer(name: str, mean: object, d_factor: float | None = None) -> dict:
    body: dict = {
        "name": name,
        "depths": [{"label": "0-30cm", "values": {"mean": mean}}],
    }
    if d_factor is not None:
        body["unit_measures"] = {"d_factor": d_factor}
    return body


def payload(*layers: dict) -> dict:
    return {"properties": {"layers": list(layers)}}


#: A realistic loam: pH 6.4, 1.8% carbon, 40/40/20 sand/silt/clay.
LOAM_PAYLOAD = payload(
    layer("phh2o", 64, 10),
    layer("soc", 180, 100),
    layer("nitrogen", 150, 100),
    layer("cec", 152, 10),
    layer("bdod", 132, 100),
    layer("sand", 400, 10),
    layer("silt", 400, 10),
    layer("clay", 200, 10),
)


# --------------------------------------------------------------------------
# Parsing and units
# --------------------------------------------------------------------------


def test_units_are_converted_from_soilgrids_integers() -> None:
    observation = parse_soilgrids(LOAM_PAYLOAD)

    assert observation.ph == pytest.approx(6.4)
    assert observation.organic_carbon_pct == pytest.approx(1.8)
    assert observation.nitrogen_g_kg == pytest.approx(1.5)
    assert observation.cec_cmol_kg == pytest.approx(15.2)
    assert observation.bulk_density_kg_dm3 == pytest.approx(1.32)


def test_particle_sizes_become_percentages() -> None:
    observation = parse_soilgrids(LOAM_PAYLOAD)

    assert observation.sand_pct == pytest.approx(40.0)
    assert observation.silt_pct == pytest.approx(40.0)
    assert observation.clay_pct == pytest.approx(20.0)


def test_texture_and_water_capacity_are_derived_from_the_fractions() -> None:
    """SoilGrids publishes fractions, not a class. Both derived values follow from them
    by the same functions the simulator uses."""
    observation = parse_soilgrids(LOAM_PAYLOAD)

    assert observation.texture_class == SoilTexture.loam
    assert observation.water_holding_capacity_mm == pytest.approx(51.0)


def test_the_layers_own_divisor_is_preferred_over_the_default() -> None:
    """SoilGrids publishes the conversion factor per layer. Trusting it means a change
    upstream is followed rather than silently mis-scaled."""
    observation = parse_soilgrids(payload(layer("phh2o", 640, 100)))

    assert observation.ph == pytest.approx(6.4)


def test_a_missing_divisor_falls_back_to_the_documented_default() -> None:
    observation = parse_soilgrids(payload(layer("phh2o", 64)))

    assert observation.ph == pytest.approx(6.4)


def test_fractions_are_normalised_when_they_do_not_sum_to_100() -> None:
    """The three fractions are predicted independently and need not agree."""
    observation = parse_soilgrids(
        payload(layer("sand", 420, 10), layer("silt", 420, 10), layer("clay", 210, 10))
    )

    total = observation.sand_pct + observation.silt_pct + observation.clay_pct

    assert total == pytest.approx(100.0, abs=0.2)


# --------------------------------------------------------------------------
# Malformed and partial responses
# --------------------------------------------------------------------------


def test_an_empty_response_yields_no_measurements() -> None:
    assert parse_soilgrids({}) == SoilObservation()


@pytest.mark.parametrize(
    "body",
    [
        {"properties": None},
        {"properties": {"layers": None}},
        {"properties": {"layers": ["not a layer"]}},
        {"properties": {"layers": [{"name": "phh2o"}]}},
        {"properties": {"layers": [{"name": "phh2o", "depths": []}]}},
    ],
)
def test_a_malformed_response_never_raises(body: dict) -> None:
    """Forgiving about shape, strict about values — a provider changing its envelope
    must degrade rather than crash an analysis."""
    assert parse_soilgrids(body) == SoilObservation()


@pytest.mark.parametrize("junk", ["6.4", None, True, {}, []])
def test_a_non_numeric_value_is_discarded(junk: object) -> None:
    """`"6.4"` is not a measurement. `True` is excluded too: it subclasses `int`, so an
    unguarded check would read it as pH 0.1."""
    assert parse_soilgrids(payload(layer("phh2o", junk, 10))).ph is None


@pytest.mark.parametrize(("value", "divisor"), [(9999, 10), (-500, 10)])
def test_an_implausible_value_is_discarded(value: int, divisor: float) -> None:
    """A pH of 999 is a unit error, not a remarkable soil."""
    assert parse_soilgrids(payload(layer("phh2o", value, divisor))).ph is None


def test_an_unknown_layer_is_ignored() -> None:
    observation = parse_soilgrids(payload(layer("phh2o", 64, 10), layer("wv0010", 300, 10)))

    assert observation.ph == pytest.approx(6.4)


def test_a_partial_profile_keeps_what_it_has() -> None:
    """Coverage is genuinely uneven. A profile with pH but no carbon is ordinary."""
    observation = parse_soilgrids(payload(layer("phh2o", 64, 10)))

    assert observation.ph == pytest.approx(6.4)
    assert observation.organic_carbon_pct is None
    assert observation.texture_class is None
    assert observation.water_holding_capacity_mm is None


def test_a_wrong_depth_interval_is_not_borrowed() -> None:
    """Topsoil advice must not silently come from a subsoil measurement."""
    deep = {
        "properties": {
            "layers": [{"name": "phh2o", "depths": [{"label": "60-100cm", "values": {"mean": 80}}]}]
        }
    }

    assert parse_soilgrids(deep).ph is None


# --------------------------------------------------------------------------
# Provenance: live, cached, unavailable, simulated
# --------------------------------------------------------------------------


@respx.mock
async def test_a_successful_fetch_is_live() -> None:
    respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM_PAYLOAD))

    result = await soil_provider.get_soil(-21.1775, -47.8103)

    assert result.ok
    assert result.meta.mode is DataMode.live
    assert result.meta.source == SOILGRIDS_SOURCE
    assert result.data.ph == pytest.approx(6.4)


@respx.mock
async def test_a_repeat_request_is_served_from_cache() -> None:
    route = respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM_PAYLOAD))

    first = await soil_provider.get_soil(-21.1775, -47.8103)
    second = await soil_provider.get_soil(-21.1775, -47.8103)

    assert first.meta.mode is DataMode.live
    assert second.meta.mode is DataMode.cached
    assert route.call_count == 1, "soil does not change; a second call is waste"
    assert second.data == first.data


@respx.mock
async def test_a_failure_is_unavailable_and_carries_no_data() -> None:
    """Never a fabricated soil. An empty result is what makes the engine report the
    profile unassessed rather than scoring an invented one."""
    respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(500))

    result = await soil_provider.get_soil(-21.1775, -47.8103)

    assert not result.ok
    assert result.data is None
    assert result.meta.mode is DataMode.unavailable
    assert result.meta.source == SOILGRIDS_SOURCE


@respx.mock
async def test_a_failure_is_not_cached() -> None:
    """A provider down for one request must be retried on the next, not remembered as
    broken for the whole thirty-day TTL.

    A 400 rather than a 500: 5xx is retryable, so `get_json` would consume the second
    response inside the first call and the cache would never be exercised.
    """
    route = respx.get(SOILGRIDS_URL).mock(
        side_effect=[httpx.Response(400), httpx.Response(200, json=LOAM_PAYLOAD)]
    )

    first = await soil_provider.get_soil(-21.1775, -47.8103)
    second = await soil_provider.get_soil(-21.1775, -47.8103)

    assert not first.ok
    assert second.ok
    assert second.meta.mode is DataMode.live
    assert route.call_count == 2


@respx.mock
async def test_a_transport_error_degrades_rather_than_raising() -> None:
    respx.get(SOILGRIDS_URL).mock(side_effect=httpx.ConnectError("no route to host"))

    result = await soil_provider.get_soil(-21.1775, -47.8103)

    assert not result.ok
    assert result.meta.mode is DataMode.unavailable


def test_the_simulator_is_labelled_simulated_never_live() -> None:
    result = soil_provider.simulated_soil(-21.1775, -47.8103)

    assert result.ok
    assert result.meta.mode is DataMode.simulated
    assert result.meta.source != SOILGRIDS_SOURCE
    assert "not a soil survey" in (result.meta.note or "").lower()


async def test_the_configured_simulator_is_used_without_a_network_call(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SOIL_PROVIDER", "simulated")

    result = await soil_provider.get_soil(-21.1775, -47.8103)

    assert result.meta.mode is DataMode.simulated


def test_the_simulator_is_stable_for_a_coordinate() -> None:
    first = soil_provider.simulated_soil(19.997, 73.791)
    second = soil_provider.simulated_soil(19.997, 73.791)

    assert first.data == second.data


# --------------------------------------------------------------------------
# Global coverage
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (-21.1775, -47.8103),
        (45.0453, 38.9818),
        (68.9678, 33.0992),
        (19.997, 73.791),
        (34.7578, 113.6486),
        (-29.1211, 26.2140),
        (24.0908, 32.8994),
        (11.5936, 37.3908),
        (29.6103, 52.5311),
        (24.1917, 55.7606),
        (3.5833, 98.6667),
        (24.6877, 46.7219),
    ],
)
async def test_every_brics_location_resolves(latitude: float, longitude: float) -> None:
    """The adapter takes coordinates and nothing else — no country, no region, no
    whitelist — so every site in the matrix goes down one code path."""
    respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM_PAYLOAD))

    result = await soil_provider.get_soil(latitude, longitude)

    assert result.ok
    assert result.data.texture_class == SoilTexture.loam


@respx.mock
async def test_the_request_carries_the_farms_own_coordinates() -> None:
    """Registered under a decoy so a passing assertion proves the coordinates drove the
    request rather than any name attached to it."""
    route = respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM_PAYLOAD))

    await soil_provider.get_soil(68.9678, 33.0992)

    request = route.calls[0].request
    assert request.url.params["lat"] == "68.9678"
    assert request.url.params["lon"] == "33.0992"


@respx.mock
async def test_every_configured_property_is_requested() -> None:
    route = respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM_PAYLOAD))

    await soil_provider.get_soil(-21.1775, -47.8103)

    requested = set(route.calls[0].request.url.params.get_list("property"))
    assert requested == set(soil_provider.PROPERTY_FIELDS)
