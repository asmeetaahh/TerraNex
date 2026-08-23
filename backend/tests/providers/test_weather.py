"""Open-Meteo weather: parsing, coordinate fidelity, provenance and degradation.

All transport is mocked with respx; nothing here touches the network.
"""

from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from app.providers import weather
from app.providers.weather import FORECAST_URL
from app.schemas.common import DataMode
from tests.fixtures.open_meteo import NASHIK_LAT, NASHIK_LON
from tests.fixtures.open_meteo import daily_payload as _daily_payload
from tests.fixtures.open_meteo import hourly_payload as _hourly_payload


def _route():
    """Open-Meteo serves daily and hourly from the same path; dispatch on the query."""

    def responder(request: httpx.Request) -> httpx.Response:
        if "daily" in request.url.params:
            return httpx.Response(200, json=_daily_payload())
        return httpx.Response(200, json=_hourly_payload())

    return respx.get(FORECAST_URL).mock(side_effect=responder)


@pytest.fixture(autouse=True)
def use_open_meteo(monkeypatch):
    monkeypatch.setattr(weather.settings, "WEATHER_PROVIDER", "open_meteo")


# --------------------------------------------------------------------------
# Coordinate fidelity
# --------------------------------------------------------------------------


@respx.mock
async def test_exact_coordinates_are_sent_to_the_provider() -> None:
    """Once a user picks a location, those exact coordinates are what is queried —
    nothing re-derives a position from a place name."""
    route = _route()

    await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    daily_call = next(c for c in route.calls if "daily" in c.request.url.params)
    assert float(daily_call.request.url.params["latitude"]) == NASHIK_LAT
    assert float(daily_call.request.url.params["longitude"]) == NASHIK_LON


@respx.mock
async def test_canonical_window_is_requested() -> None:
    route = _route()

    await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    params = next(c for c in route.calls if "daily" in c.request.url.params).request.url.params
    assert params["past_days"] == str(weather.CANONICAL_PAST_DAYS)
    assert params["forecast_days"] == str(weather.CANONICAL_FORECAST_DAYS)
    assert params["timezone"] == "auto"


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


@respx.mock
async def test_daily_series_is_parsed() -> None:
    _route()

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert result.ok
    daily = result.data.daily
    assert len(daily) == 108
    first = daily[0]
    assert first.temp_max_c == 33.4
    assert first.temp_min_c == 21.1
    assert first.temp_mean_c == 27.2
    assert first.humidity_pct == 64
    assert first.precipitation_mm == 12.5
    assert first.wind_kmh == 14.2
    assert first.radiation_mj_m2 == 21.3
    assert first.condition == "rain"  # WMO 61


@respx.mock
async def test_et0_is_mapped_from_the_provider() -> None:
    """ET₀ drives the whole water balance, so it must come from the provider rather
    than being recomputed locally."""
    _route()

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert all(d.et0_mm == 5.42 for d in result.data.daily)
    assert all(h.et0_mm == 0.22 for h in result.data.hourly)


@respx.mock
async def test_timezone_is_preserved() -> None:
    _route()

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert result.data.timezone == "Asia/Kolkata"


@respx.mock
async def test_current_conditions_are_parsed() -> None:
    _route()

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    current = result.data.current
    assert current is not None
    assert current.temperature_c == 28.6
    assert current.humidity_pct == 71
    assert current.feels_like_c == 31.2
    assert current.wind_speed_kmh == 11.5
    assert current.wind_direction_deg == 245
    assert current.pressure_hpa == 1008.4
    assert current.condition == "rain"
    assert current.condition_label == "Rain"


@respx.mock
async def test_hourly_series_is_parsed() -> None:
    _route()

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    hourly = result.data.hourly
    assert len(hourly) == 72
    assert hourly[0].humidity_pct == 70
    assert hourly[0].soil_moisture_m3m3 == 0.24
    assert hourly[0].radiation_w_m2 == 420.0


@respx.mock
async def test_current_is_none_when_the_provider_omits_it() -> None:
    """A missing 'now' must stay missing. Back-filling it from a daily mean would be
    fabricating a current observation."""

    def responder(request: httpx.Request) -> httpx.Response:
        if "daily" in request.url.params:
            return httpx.Response(200, json=_daily_payload())
        payload = _hourly_payload()
        del payload["current"]
        return httpx.Response(200, json=payload)

    respx.get(FORECAST_URL).mock(side_effect=responder)

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert result.ok
    assert result.data.current is None


@respx.mock
async def test_missing_variable_does_not_misalign_the_series() -> None:
    """Open-Meteo omits variables it cannot supply rather than sending nulls."""

    def responder(request: httpx.Request) -> httpx.Response:
        if "daily" in request.url.params:
            payload = _daily_payload()
            del payload["daily"]["relative_humidity_2m_mean"]
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json=_hourly_payload())

    respx.get(FORECAST_URL).mock(side_effect=responder)

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert len(result.data.daily) == 108
    assert all(d.humidity_pct is None for d in result.data.daily)
    assert result.data.daily[0].temp_max_c == 33.4  # other columns still aligned


@respx.mock
async def test_daily_mean_is_derived_when_absent() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        if "daily" in request.url.params:
            payload = _daily_payload()
            del payload["daily"]["temperature_2m_mean"]
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json=_hourly_payload())

    respx.get(FORECAST_URL).mock(side_effect=responder)

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert result.data.daily[0].temp_mean_c == pytest.approx((33.4 + 21.1) / 2)


# --------------------------------------------------------------------------
# Timezone handling
#
# We request `timezone=auto`, so Open-Meteo returns LOCAL NAIVE timestamps plus a
# separate `utc_offset_seconds`. Reading those as UTC would misattribute every
# instant by the location's offset — 5h30m for India.
# --------------------------------------------------------------------------


IST_PAYLOAD = {
    "latitude": NASHIK_LAT,
    "longitude": NASHIK_LON,
    "timezone": "Asia/Kolkata",
    "utc_offset_seconds": 19800,  # +05:30
    "current": {"time": "2026-08-20T09:00", "temperature_2m": 28.6, "weather_code": 61},
    "hourly": {
        "time": ["2026-08-20T09:00", "2026-08-20T10:00", "2026-08-21T00:00"],
        "temperature_2m": [26.0, 27.0, 22.0],
    },
}


def test_india_local_time_converts_to_utc() -> None:
    """09:00 Asia/Kolkata is 03:30 UTC, not 09:00 UTC."""
    hourly = weather._parse_hourly(IST_PAYLOAD)

    assert hourly[0].time.isoformat() == "2026-08-20T03:30:00+00:00"
    assert hourly[1].time.isoformat() == "2026-08-20T04:30:00+00:00"
    # A local midnight falls back into the previous UTC day.
    assert hourly[2].time.isoformat() == "2026-08-20T18:30:00+00:00"


def test_observed_at_converts_to_utc() -> None:
    current = weather._parse_current(IST_PAYLOAD)

    assert current is not None
    assert current.observed_at.isoformat() == "2026-08-20T03:30:00+00:00"


def test_hourly_ordering_survives_conversion() -> None:
    times = [h.time for h in weather._parse_hourly(IST_PAYLOAD)]

    assert times == sorted(times)


@pytest.mark.parametrize(
    ("offset_seconds", "expected"),
    [
        (19800, "2026-08-20T03:30:00+00:00"),  # Asia/Kolkata      +05:30
        (0, "2026-08-20T09:00:00+00:00"),  # UTC                00:00
        (-18000, "2026-08-20T14:00:00+00:00"),  # America/New_York  -05:00
        (45900, "2026-08-19T20:15:00+00:00"),  # Pacific/Chatham   +12:45
    ],
)
def test_offsets_across_zones(offset_seconds: int, expected: str) -> None:
    payload = {**IST_PAYLOAD, "utc_offset_seconds": offset_seconds}

    assert weather._parse_hourly(payload)[0].time.isoformat() == expected
    assert weather._parse_current(payload).observed_at.isoformat() == expected


def test_missing_offset_falls_back_to_utc() -> None:
    """Absent `utc_offset_seconds`, naive timestamps are read as UTC — a safe
    default rather than a guess at the location's zone."""
    payload = {k: v for k, v in IST_PAYLOAD.items() if k != "utc_offset_seconds"}

    assert weather._parse_hourly(payload)[0].time.isoformat() == "2026-08-20T09:00:00+00:00"


def test_explicit_offset_in_the_timestamp_is_trusted() -> None:
    """An inline offset wins over the payload-level field."""
    payload = {
        **IST_PAYLOAD,
        "hourly": {"time": ["2026-08-20T09:00:00+02:00"], "temperature_2m": [26.0]},
    }

    assert weather._parse_hourly(payload)[0].time.isoformat() == "2026-08-20T07:00:00+00:00"


@respx.mock
async def test_timezone_conversion_reaches_the_observations() -> None:
    """End-to-end through the provider, not only the parser."""

    def responder(request: httpx.Request) -> httpx.Response:
        if "daily" in request.url.params:
            payload = _daily_payload()
            payload["utc_offset_seconds"] = 19800
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json=IST_PAYLOAD)

    respx.get(FORECAST_URL).mock(side_effect=responder)

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert result.data.timezone == "Asia/Kolkata"
    assert result.data.current.observed_at.isoformat() == "2026-08-20T03:30:00+00:00"
    assert result.data.hourly[0].time.isoformat() == "2026-08-20T03:30:00+00:00"


@pytest.mark.parametrize(
    ("code", "condition"),
    [
        (0, "clear"),
        (2, "partly_cloudy"),
        (3, "cloudy"),
        (45, "fog"),
        (61, "rain"),
        (75, "snow"),
        (95, "thunderstorm"),
        (None, None),
    ],
)
def test_wmo_code_mapping(code, condition) -> None:
    assert weather.condition_from_code(code)[0] == condition


# --------------------------------------------------------------------------
# Provenance and degradation
# --------------------------------------------------------------------------


@respx.mock
async def test_success_is_live() -> None:
    _route()

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert result.ok
    assert result.meta.mode is DataMode.live
    assert result.meta.source == "open-meteo"


@respx.mock
async def test_second_fetch_is_cached() -> None:
    route = _route()

    first = await weather.get_observations(NASHIK_LAT, NASHIK_LON)
    second = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert first.meta.mode is DataMode.live
    assert second.meta.mode is DataMode.cached
    assert route.call_count == 2, "two upstream calls total (daily + hourly), not four"


@respx.mock
async def test_nearby_coordinates_share_a_cache_entry() -> None:
    """Keys round to 3 dp (~110 m), so two points in one field are one fetch."""
    route = _route()

    await weather.get_observations(NASHIK_LAT, NASHIK_LON)
    await weather.get_observations(NASHIK_LAT + 0.00004, NASHIK_LON + 0.00003)

    assert route.call_count == 2


@respx.mock
async def test_provider_failure_is_unavailable() -> None:
    respx.get(FORECAST_URL).mock(return_value=httpx.Response(500))

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert result.ok is False
    assert result.data is None
    assert result.meta.mode is DataMode.unavailable


@respx.mock
async def test_empty_daily_series_is_unavailable() -> None:
    """A 200 with nothing usable is still a failure."""

    def responder(request: httpx.Request) -> httpx.Response:
        if "daily" in request.url.params:
            return httpx.Response(200, json={"latitude": 0, "longitude": 0, "daily": {"time": []}})
        return httpx.Response(200, json=_hourly_payload())

    respx.get(FORECAST_URL).mock(side_effect=responder)

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert result.ok is False
    assert result.meta.mode is DataMode.unavailable


@respx.mock
async def test_hourly_failure_still_yields_daily_data() -> None:
    """Losing the hourly call degrades the payload; it must not lose the forecast."""

    def responder(request: httpx.Request) -> httpx.Response:
        if "daily" in request.url.params:
            return httpx.Response(200, json=_daily_payload())
        return httpx.Response(503)

    respx.get(FORECAST_URL).mock(side_effect=responder)

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert result.ok
    assert result.data.daily
    assert result.data.hourly == []
    assert result.data.current is None


# --------------------------------------------------------------------------
# Simulated provider
# --------------------------------------------------------------------------


async def test_simulated_provider_is_marked_simulated(monkeypatch) -> None:
    monkeypatch.setattr(weather.settings, "WEATHER_PROVIDER", "simulated")

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert result.ok
    assert result.meta.mode is DataMode.simulated
    assert result.meta.source == "simulated"
    assert result.data.daily
    assert result.data.current is not None


async def test_simulated_provider_makes_no_network_call(monkeypatch) -> None:
    monkeypatch.setattr(weather.settings, "WEATHER_PROVIDER", "simulated")

    with respx.mock:  # any outbound request would raise here
        result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert result.meta.mode is DataMode.simulated


async def test_simulated_timezone_varies_with_longitude(monkeypatch) -> None:
    """The simulator reported "UTC" for every location on Earth, so a farm's local
    time could be wrong by twelve hours. It cannot know a civil zone, but it can
    report the offset its own timestamps were built from."""
    monkeypatch.setattr(weather.settings, "WEATHER_PROVIDER", "simulated")

    zones = []
    for longitude in (-120.0, -47.8103, 0.0, 36.8172, 73.79096, 113.6486, 150.0):
        result = await weather.get_observations(10.0, longitude)
        zones.append(result.data.timezone)

    assert len(set(zones)) >= 5, f"timezones collapsed: {sorted(set(zones))}"
    assert all(z.startswith("Etc/GMT") for z in zones), zones


async def test_simulated_hourly_times_agree_with_the_reported_timezone(monkeypatch) -> None:
    """The property that makes the reported zone trustworthy: converting the returned
    instants into it yields whole local hours starting at midnight."""
    monkeypatch.setattr(weather.settings, "WEATHER_PROVIDER", "simulated")

    result = await weather.get_observations(19.99727, 73.79096)

    zone = ZoneInfo(result.data.timezone)
    local_hours = [h.time.astimezone(zone).hour for h in result.data.hourly]

    assert local_hours[:24] == list(range(24))
    assert all(h.time.utcoffset() is not None for h in result.data.hourly)


async def test_simulated_observations_stay_deterministic(monkeypatch) -> None:
    """Determinism is what the demo and the contract tests rest on; the timezone
    change must not disturb it."""
    monkeypatch.setattr(weather.settings, "WEATHER_PROVIDER", "simulated")

    first = await weather.get_observations(-1.2864, 36.8172)
    second = await weather.get_observations(-1.2864, 36.8172)

    assert first.data.timezone == second.data.timezone
    assert [(d.date, d.temp_max_c, d.precipitation_mm) for d in first.data.daily] == [
        (d.date, d.temp_max_c, d.precipitation_mm) for d in second.data.daily
    ]
    assert [h.time for h in first.data.hourly] == [h.time for h in second.data.hourly]


async def test_simulated_provider_spans_the_canonical_window(monkeypatch) -> None:
    """Both providers must cover the same window, or consumers would behave
    differently depending on configuration."""
    monkeypatch.setattr(weather.settings, "WEATHER_PROVIDER", "simulated")

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    expected = weather.CANONICAL_PAST_DAYS + weather.CANONICAL_FORECAST_DAYS
    assert len(result.data.daily) == expected


# --------------------------------------------------------------------------
# Default provider selection
# --------------------------------------------------------------------------


async def test_default_configuration_routes_to_the_real_weather_provider(monkeypatch) -> None:
    """With defaults in force, the real Open-Meteo path runs — not the simulator.

    Asserts the branch actually taken rather than only the setting's value.
    """
    from app.core.config import Settings

    declared = Settings.model_fields["WEATHER_PROVIDER"].default
    monkeypatch.setattr(weather.settings, "WEATHER_PROVIDER", declared)

    with respx.mock:
        route = _route()
        result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert route.called, "the default configuration did not reach Open-Meteo"
    assert result.meta.source == "open-meteo"
    assert result.meta.mode is DataMode.live


async def test_explicit_simulated_still_makes_no_outbound_call(monkeypatch) -> None:
    """The simulator remains selectable and entirely offline."""
    monkeypatch.setattr(weather.settings, "WEATHER_PROVIDER", "simulated")

    with respx.mock:  # any outbound request would raise here
        result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert result.meta.mode is DataMode.simulated
    assert result.meta.source == "simulated"
    assert result.data.daily


async def test_default_path_preserves_the_provider_deadline(monkeypatch) -> None:
    """P0-2 behaviour must survive the default flip: the real path still runs inside
    a wall-clock budget rather than restarting it per attempt."""
    from app.core.config import Settings
    from app.providers.http import remaining_budget

    declared = Settings.model_fields["WEATHER_PROVIDER"].default
    monkeypatch.setattr(weather.settings, "WEATHER_PROVIDER", declared)

    seen: list[float | None] = []

    def responder(request: httpx.Request) -> httpx.Response:
        seen.append(remaining_budget())
        if "daily" in request.url.params:
            return httpx.Response(200, json=_daily_payload())
        return httpx.Response(200, json=_hourly_payload())

    with respx.mock:
        respx.get(FORECAST_URL).mock(side_effect=responder)
        await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert seen, "no outbound call was made"
    assert all(b is not None for b in seen), "the default path ran without a budget open"


async def test_default_path_preserves_caching(monkeypatch) -> None:
    """Cache behaviour is unchanged by the default flip."""
    from app.core.config import Settings

    declared = Settings.model_fields["WEATHER_PROVIDER"].default
    monkeypatch.setattr(weather.settings, "WEATHER_PROVIDER", declared)

    with respx.mock:
        route = _route()
        first = await weather.get_observations(NASHIK_LAT, NASHIK_LON)
        second = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert first.meta.mode is DataMode.live
    assert second.meta.mode is DataMode.cached
    assert route.call_count == 2, "two documents fetched once each, then served from cache"


# --------------------------------------------------------------------------
# Provider selection
#
# NASA POWER is opt-in. `get_observations` is the only dispatch point, so these pin that
# the default is unchanged and that nothing reaches POWER without being configured for
# it — the property that keeps the existing degradation ladder intact.
# --------------------------------------------------------------------------


def test_open_meteo_remains_the_declared_default() -> None:
    """Adding a provider must not change which one a fresh deployment uses."""
    from app.core.config import Settings

    assert Settings.model_fields["WEATHER_PROVIDER"].default == "open_meteo"


def test_nasa_power_is_a_permitted_value() -> None:
    from typing import get_args

    from app.core.config import Settings

    permitted = get_args(Settings.model_fields["WEATHER_PROVIDER"].annotation)
    assert set(permitted) == {"open_meteo", "nasa_power", "simulated"}


async def test_the_default_provider_never_calls_nasa_power(monkeypatch) -> None:
    """The regression that matters: an unconfigured deployment must not reach POWER."""
    from app.providers import nasa_power

    monkeypatch.setattr(weather.settings, "WEATHER_PROVIDER", "open_meteo")

    called = False

    async def spy(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(nasa_power, "power_observations", spy)

    with respx.mock:
        _route()
        await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert called is False


async def test_selecting_nasa_power_routes_to_it(monkeypatch) -> None:
    from app.providers import nasa_power
    from app.providers.base import ProviderResult, WeatherObservations

    monkeypatch.setattr(weather.settings, "WEATHER_PROVIDER", "nasa_power")

    seen: dict = {}

    async def spy(latitude, longitude, *, today=None):
        seen.update(latitude=latitude, longitude=longitude)
        return ProviderResult.live(
            WeatherObservations(
                latitude=latitude, longitude=longitude, timezone=None, current=None
            ),
            "nasa-power",
        )

    monkeypatch.setattr(nasa_power, "power_observations", spy)

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert result.meta.source == "nasa-power"
    assert seen == {"latitude": NASHIK_LAT, "longitude": NASHIK_LON}


async def test_selecting_simulated_still_bypasses_every_network_provider(monkeypatch) -> None:
    """Unchanged behaviour, pinned so the new branch cannot have reordered it."""
    monkeypatch.setattr(weather.settings, "WEATHER_PROVIDER", "simulated")

    result = await weather.get_observations(NASHIK_LAT, NASHIK_LON)

    assert result.ok
    assert result.meta.mode is DataMode.simulated
