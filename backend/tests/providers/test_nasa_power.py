"""NASA POWER: parsing, unit conversion, and the fill value that must never escape.

Everything here is offline — `respx` mocks the POWER host, so no test reaches the
network and none depends on NASA being up.

**The fixture is a real response, trimmed.** The values below were captured from
`power.larc.nasa.gov` for Nairobi and kept verbatim, including the `-999.0` runs the live
API actually returned for the three most recent days. Inventing a payload would have
meant guessing the shape, the key format and the fill value; measuring it meant the
parser is written against what POWER sends rather than what its documentation implies.

The load-bearing test is `test_the_fill_value_never_escapes_as_a_measurement`. `-999` is
a *number*: it survives every `is None` check downstream and would arrive at the FAO-56
water balance as a metre of negative rainfall, producing confident irrigation advice from
a drought that never happened.
"""

import httpx
import pytest
import respx

from app.core.config import settings
from app.providers.cache import clear_all_caches
from app.providers.nasa_power import (
    FILL_VALUE_THRESHOLD,
    MS_TO_KMH,
    NASA_POWER_SOURCE,
    NASA_POWER_URL,
    PARAMETERS,
    parse_power,
    power_observations,
)
from app.schemas.common import DataMode

# Captured live from NASA POWER (Nairobi, -1.29/36.82). Three complete days, one day
# where only radiation is absent, and the trailing unpublished days as `-999.0`.
REAL_PAYLOAD = {
    "geometry": {"type": "Point", "coordinates": [36.82, -1.29, 1642.2]},
    "properties": {
        "parameter": {
            "T2M_MIN": {
                "20260818": 15.39,
                "20260819": 14.12,
                "20260820": 13.83,
                "20260821": -999.0,
                "20260822": -999.0,
            },
            "T2M_MAX": {
                "20260818": 23.84,
                "20260819": 24.95,
                "20260820": 25.45,
                "20260821": -999.0,
                "20260822": -999.0,
            },
            "T2M": {
                "20260818": 18.55,
                "20260819": 18.83,
                "20260820": 18.83,
                "20260821": -999.0,
                "20260822": -999.0,
            },
            "PRECTOTCORR": {
                "20260818": 2.46,
                "20260819": 2.28,
                "20260820": 0.28,
                "20260821": -999.0,
                "20260822": -999.0,
            },
            "RH2M": {
                "20260818": 74.36,
                "20260819": 70.93,
                "20260820": 65.01,
                "20260821": -999.0,
                "20260822": -999.0,
            },
            "WS2M": {
                "20260818": 2.28,
                "20260819": 2.38,
                "20260820": 1.83,
                "20260821": -999.0,
                "20260822": -999.0,
            },
            "ALLSKY_SFC_SW_DWN": {
                "20260818": 13.48,
                "20260819": -999.0,
                "20260820": -999.0,
                "20260821": -999.0,
                "20260822": -999.0,
            },
        }
    },
}


@pytest.fixture(autouse=True)
def _use_nasa_power(monkeypatch):
    """Opt this module into the provider; conftest pins the suite to `simulated`."""
    monkeypatch.setattr(settings, "WEATHER_PROVIDER", "nasa_power")
    clear_all_caches()
    yield
    clear_all_caches()


def day(observations, stamp: str):
    from datetime import datetime

    wanted = datetime.strptime(stamp, "%Y%m%d").date()
    return next(o for o in observations if o.date == wanted)


# --------------------------------------------------------------------------
# The fill value
# --------------------------------------------------------------------------


def test_the_fill_value_never_escapes_as_a_measurement() -> None:
    """`-999` is a number, so nothing downstream would catch it. It must die here."""
    observations = parse_power(REAL_PAYLOAD)

    for observation in observations:
        for value in (
            observation.temp_min_c,
            observation.temp_max_c,
            observation.temp_mean_c,
            observation.humidity_pct,
            observation.precipitation_mm,
            observation.wind_kmh,
            observation.radiation_mj_m2,
        ):
            assert value is None or value > FILL_VALUE_THRESHOLD, value


def test_a_partially_filled_day_keeps_its_real_measurements() -> None:
    """19 Aug had real temperature and rain but no radiation. Dropping the whole day
    would discard measurements POWER actually published."""
    observation = day(parse_power(REAL_PAYLOAD), "20260819")

    assert observation.temp_min_c == 14.12
    assert observation.precipitation_mm == 2.28
    assert observation.radiation_mj_m2 is None, "absent radiation is absent, not -999"


def test_a_wholly_unpublished_day_is_dropped() -> None:
    """POWER lags: the newest days come back entirely `-999`. Publishing them would read
    as a measured calm, dry, freezing day."""
    stamps = {o.date.strftime("%Y%m%d") for o in parse_power(REAL_PAYLOAD)}

    assert "20260821" not in stamps
    assert "20260822" not in stamps
    assert stamps == {"20260818", "20260819", "20260820"}


@pytest.mark.parametrize("sentinel", [-999, -999.0, -9999.0, -1000.0])
def test_every_fill_variant_is_rejected(sentinel: float) -> None:
    payload = {"properties": {"parameter": {"T2M": {"20260818": sentinel}}}}

    assert parse_power(payload) == []


def test_a_legitimate_negative_temperature_survives() -> None:
    """The threshold must not swallow real cold. Murmansk and Svalbard are supported."""
    payload = {"properties": {"parameter": {"T2M_MIN": {"20260818": -34.5}}}}

    assert parse_power(payload)[0].temp_min_c == -34.5


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------


def test_wind_is_converted_from_metres_per_second_to_kmh() -> None:
    """POWER reports `WS2M` in m/s; every normalised field is km/h. A missed conversion
    would understate wind by 3.6× and silently lower every wind-risk score."""
    observation = day(parse_power(REAL_PAYLOAD), "20260818")

    assert observation.wind_kmh == pytest.approx(2.28 * MS_TO_KMH, abs=0.01)
    assert observation.wind_kmh == pytest.approx(8.21, abs=0.01)


def test_temperature_rain_humidity_and_radiation_are_passed_through_unconverted() -> None:
    """POWER already publishes these in TerraNex's units — converting would corrupt them."""
    observation = day(parse_power(REAL_PAYLOAD), "20260818")

    assert observation.temp_min_c == 15.39
    assert observation.temp_max_c == 23.84
    assert observation.temp_mean_c == 18.55
    assert observation.precipitation_mm == 2.46
    assert observation.humidity_pct == 74.36
    assert observation.radiation_mj_m2 == 13.48


def test_et0_is_left_empty_for_hargreaves() -> None:
    """POWER's evapotranspiration is not FAO-56 reference ET₀. Leaving it empty gets a
    correct value from the engine's Hargreaves fallback rather than a plausible wrong one."""
    assert all(o.et0_mm is None for o in parse_power(REAL_PAYLOAD))


def test_precipitation_hours_is_not_inferred_from_depth() -> None:
    """Rain depth says nothing about duration, which is what the disease rules read."""
    assert all(o.precipitation_hours is None for o in parse_power(REAL_PAYLOAD))


# --------------------------------------------------------------------------
# Malformed and incomplete responses
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"properties": {}},
        {"properties": {"parameter": {}}},
        {"properties": {"parameter": None}},
        {"properties": {"parameter": {"T2M": None}}},
        {"properties": {"parameter": {"T2M": "not-a-block"}}},
    ],
)
def test_a_malformed_body_yields_no_days_rather_than_raising(payload: dict) -> None:
    assert parse_power(payload) == []


def test_an_unparseable_date_key_drops_only_that_day() -> None:
    payload = {"properties": {"parameter": {"T2M": {"not-a-date": 20.0, "20260818": 21.0}}}}

    observations = parse_power(payload)

    assert len(observations) == 1
    assert observations[0].temp_mean_c == 21.0


def test_a_missing_parameter_block_nulls_only_its_field() -> None:
    """A response without humidity is incomplete, not invalid."""
    payload = {"properties": {"parameter": {"T2M": {"20260818": 20.0}}}}

    observation = parse_power(payload)[0]

    assert observation.temp_mean_c == 20.0
    assert observation.humidity_pct is None
    assert observation.wind_kmh is None


def test_a_non_numeric_value_is_treated_as_absent() -> None:
    payload = {"properties": {"parameter": {"T2M": {"20260818": "warm"}, "RH2M": {"20260818": 60}}}}

    observation = parse_power(payload)[0]

    assert observation.temp_mean_c is None
    assert observation.humidity_pct == 60


def test_booleans_are_not_accepted_as_measurements() -> None:
    """`bool` subclasses `int`, so `True` would otherwise pass as 1.0."""
    payload = {"properties": {"parameter": {"T2M": {"20260818": True}}}}

    assert parse_power(payload) == []


# --------------------------------------------------------------------------
# The provider call
# --------------------------------------------------------------------------


@respx.mock
async def test_a_successful_fetch_returns_daily_only() -> None:
    """POWER is a daily record: no hourly series, and no current observation to invent."""
    respx.get(NASA_POWER_URL).mock(return_value=httpx.Response(200, json=REAL_PAYLOAD))

    result = await power_observations(-1.29, 36.82)

    assert result.ok
    assert result.meta.source == NASA_POWER_SOURCE
    assert result.meta.mode is DataMode.live
    assert len(result.data.daily) == 3
    assert result.data.hourly == []
    assert result.data.current is None
    assert result.data.timezone is None


@respx.mock
async def test_the_request_asks_for_exactly_the_mapped_parameters() -> None:
    """Every requested parameter has a consumer; an unused one is payload for nothing."""
    route = respx.get(NASA_POWER_URL).mock(return_value=httpx.Response(200, json=REAL_PAYLOAD))

    await power_observations(-1.29, 36.82)

    requested = set(route.calls[0].request.url.params["parameters"].split(","))
    assert requested == set(PARAMETERS)


@respx.mock
async def test_an_http_error_is_unavailable_not_an_exception() -> None:
    respx.get(NASA_POWER_URL).mock(return_value=httpx.Response(500))

    result = await power_observations(-1.29, 36.82)

    assert result.ok is False
    assert result.data is None


@respx.mock
async def test_a_client_error_is_unavailable() -> None:
    respx.get(NASA_POWER_URL).mock(return_value=httpx.Response(422))

    assert (await power_observations(-1.29, 36.82)).ok is False


@respx.mock
async def test_a_timeout_is_unavailable() -> None:
    respx.get(NASA_POWER_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))

    result = await power_observations(-1.29, 36.82)

    assert result.ok is False
    assert result.data is None


@respx.mock
async def test_a_transport_failure_is_unavailable() -> None:
    respx.get(NASA_POWER_URL).mock(side_effect=httpx.ConnectError("dns failure"))

    assert (await power_observations(-1.29, 36.82)).ok is False


@respx.mock
async def test_a_200_with_no_usable_day_is_unavailable_not_an_empty_success() -> None:
    """Reporting this as success would publish an empty history as though the weather had
    been measured and found to be nothing."""
    empty = {"properties": {"parameter": {k: {"20260821": -999.0} for k in PARAMETERS}}}
    respx.get(NASA_POWER_URL).mock(return_value=httpx.Response(200, json=empty))

    result = await power_observations(-1.29, 36.82)

    assert result.ok is False
    assert "no usable days" in (result.meta.note or "")


@respx.mock
async def test_repeat_requests_for_one_field_are_cached() -> None:
    """Reuses the shared TTL cache, so a second request for the same field and day makes
    no second call."""
    route = respx.get(NASA_POWER_URL).mock(return_value=httpx.Response(200, json=REAL_PAYLOAD))

    await power_observations(-1.29, 36.82)
    await power_observations(-1.29, 36.82)

    assert route.call_count == 1


# --------------------------------------------------------------------------
# Global coverage
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize(
    ("name", "latitude", "longitude"),
    [
        ("nairobi", -1.29, 36.82),
        ("ribeirao_preto", -21.18, -47.81),
        ("murmansk", 68.97, 33.10),
        ("nashik", 19.997, 73.791),
        ("zhengzhou", 34.76, 113.65),
        ("aswan", 24.09, 32.90),
        ("ushuaia", -54.80, -68.30),
        ("svalbard", 78.22, 15.65),
    ],
)
async def test_any_global_coordinate_is_requested_verbatim(
    name: str, latitude: float, longitude: float
) -> None:
    """All eight answered HTTP 200 with complete data when probed live. Nothing here may
    assume a region — the coordinates are passed through exactly as given."""
    route = respx.get(NASA_POWER_URL).mock(return_value=httpx.Response(200, json=REAL_PAYLOAD))

    result = await power_observations(latitude, longitude)

    assert result.ok, name
    params = route.calls[0].request.url.params
    assert float(params["latitude"]) == latitude
    assert float(params["longitude"]) == longitude
