"""Weather retrieval, real and simulated.

Both implementations return :class:`WeatherObservations` at the **exact coordinates
they are given**. Nothing here re-derives a location from a place name — once a user
picks a candidate, those coordinates are what every provider call uses.

The real provider is Open-Meteo (no API key). Two requests are made and cached
independently, because the two horizons have very different shapes and lifetimes:

* a **daily** request spanning ~92 days of history plus 16 days of forecast, which is
  the superset every consumer slices from;
* an **hourly + current** request covering only the near term, since hourly detail over
  100+ days would be megabytes of JSON for data nothing reads.

`current` is only ever populated from the provider's own current block. When it is
missing, it stays `None` all the way to the response rather than being back-filled
from a daily mean — a fabricated "now" is worse than an absent one.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.base import (
    CurrentObservation,
    DailyObservation,
    HourlyObservation,
    ProviderCallError,
    ProviderResult,
    WeatherObservations,
)
from app.providers.cache import build_key, coord_key, get_cache
from app.providers.http import get_json, provider_deadline
from app.services import simulation

logger = get_logger(__name__)

OPEN_METEO_SOURCE = "open-meteo"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# The canonical window every consumer slices from. 92 past days is Open-Meteo's
# `past_days` maximum; 16 forecast days is the contract's maximum.
CANONICAL_PAST_DAYS = 92
CANONICAL_FORECAST_DAYS = 16
HOURLY_FORECAST_DAYS = 3

DAILY_VARIABLES = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "relative_humidity_2m_mean",
    "precipitation_sum",
    "precipitation_hours",
    "wind_speed_10m_max",
    "et0_fao_evapotranspiration",
    "shortwave_radiation_sum",
    "cloud_cover_mean",
    "weather_code",
]

HOURLY_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "et0_fao_evapotranspiration",
    "shortwave_radiation",
    "soil_moisture_0_to_1cm",
    "soil_temperature_0cm",
]

CURRENT_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
    "cloud_cover",
    "pressure_msl",
    "weather_code",
]

# WMO 4677 weather codes, grouped into the condition vocabulary the UI renders.
_WMO_CONDITIONS: list[tuple[set[int], str, str]] = [
    ({0}, "clear", "Clear"),
    ({1, 2}, "partly_cloudy", "Partly cloudy"),
    ({3}, "cloudy", "Overcast"),
    ({45, 48}, "fog", "Fog"),
    ({51, 53, 55, 56, 57}, "drizzle", "Drizzle"),
    ({61, 63, 65, 66, 67, 80, 81, 82}, "rain", "Rain"),
    ({71, 73, 75, 77, 85, 86}, "snow", "Snow"),
    ({95, 96, 99}, "thunderstorm", "Thunderstorm"),
]


def condition_from_code(code: int | float | None) -> tuple[str | None, str | None]:
    """Map a WMO weather code onto (condition, label)."""
    if code is None:
        return None, None
    value = int(code)
    for codes, name, label in _WMO_CONDITIONS:
        if value in codes:
            return name, label
    return "unknown", "Unknown"


def payload_timezone(payload: dict[str, Any]) -> timezone:
    """The timezone the payload's timestamps are expressed in.

    We request `timezone=auto`, so Open-Meteo returns **local naive** timestamps with
    no offset in the string, plus a separate `utc_offset_seconds`. Treating those
    naive values as UTC would misattribute every instant by the location's offset —
    5h30m for Asia/Kolkata. This reads the offset the provider actually reported.
    """
    offset = payload.get("utc_offset_seconds")
    if offset is None:
        return UTC
    try:
        return timezone(timedelta(seconds=int(offset)))
    except (TypeError, ValueError):
        return UTC


def _to_utc(stamp: object, tz: timezone) -> datetime | None:
    """Parse a provider timestamp and normalise it to UTC.

    Naive values are interpreted in `tz` (the provider's local zone) and then
    converted, so the stored instant is correct regardless of the farm's longitude.
    Timestamps that already carry an offset are trusted as-is.
    """
    try:
        moment = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    return moment.astimezone(UTC)


def _series(block: dict[str, Any], key: str, length: int) -> list[Any]:
    """Read one variable from an Open-Meteo block, padded to `length` with None.

    Open-Meteo omits variables it cannot supply for a location rather than filling
    them with nulls, so a missing key must not shorten or misalign the series.
    """
    values = block.get(key)
    if not isinstance(values, list):
        return [None] * length
    if len(values) < length:
        return list(values) + [None] * (length - len(values))
    return list(values[:length])


def _parse_daily(payload: dict[str, Any]) -> list[DailyObservation]:
    block = payload.get("daily") or {}
    times = block.get("time") or []
    n = len(times)
    if n == 0:
        return []

    columns = {name: _series(block, name, n) for name in DAILY_VARIABLES}
    observations: list[DailyObservation] = []

    for index, stamp in enumerate(times):
        try:
            day = date.fromisoformat(str(stamp))
        except ValueError:
            continue

        code = columns["weather_code"][index]
        condition, _ = condition_from_code(code)

        mean = columns["temperature_2m_mean"][index]
        low = columns["temperature_2m_min"][index]
        high = columns["temperature_2m_max"][index]
        # Open-Meteo does not always return a daily mean; derive it from the
        # extremes rather than dropping the day from the series.
        if mean is None and low is not None and high is not None:
            mean = (low + high) / 2

        precipitation = columns["precipitation_sum"][index]

        observations.append(
            DailyObservation(
                date=day,
                temp_min_c=low,
                temp_max_c=high,
                temp_mean_c=mean,
                humidity_pct=columns["relative_humidity_2m_mean"][index],
                precipitation_mm=float(precipitation) if precipitation is not None else None,
                precipitation_hours=columns["precipitation_hours"][index],
                wind_kmh=columns["wind_speed_10m_max"][index],
                et0_mm=columns["et0_fao_evapotranspiration"][index],
                radiation_mj_m2=columns["shortwave_radiation_sum"][index],
                cloud_cover_pct=columns["cloud_cover_mean"][index],
                condition=condition,
            )
        )

    return observations


def _parse_hourly(payload: dict[str, Any]) -> list[HourlyObservation]:
    block = payload.get("hourly") or {}
    times = block.get("time") or []
    n = len(times)
    if n == 0:
        return []

    columns = {name: _series(block, name, n) for name in HOURLY_VARIABLES}
    tz = payload_timezone(payload)
    observations: list[HourlyObservation] = []

    for index, stamp in enumerate(times):
        moment = _to_utc(stamp, tz)
        if moment is None:
            continue

        observations.append(
            HourlyObservation(
                time=moment,
                temperature_c=columns["temperature_2m"][index],
                humidity_pct=columns["relative_humidity_2m"][index],
                precipitation_mm=columns["precipitation"][index],
                wind_kmh=columns["wind_speed_10m"][index],
                et0_mm=columns["et0_fao_evapotranspiration"][index],
                radiation_w_m2=columns["shortwave_radiation"][index],
                soil_moisture_m3m3=columns["soil_moisture_0_to_1cm"][index],
                soil_temperature_c=columns["soil_temperature_0cm"][index],
            )
        )

    return observations


def _parse_current(payload: dict[str, Any]) -> CurrentObservation | None:
    """Map the provider's current block. Returns None when it is absent or has no
    temperature — never invents a reading."""
    block = payload.get("current")
    if not isinstance(block, dict):
        return None

    temperature = block.get("temperature_2m")
    if temperature is None:
        return None

    stamp = block.get("time")
    observed_at = _to_utc(stamp, payload_timezone(payload)) if stamp else None
    if observed_at is None:
        observed_at = datetime.now(UTC)

    condition, label = condition_from_code(block.get("weather_code"))

    return CurrentObservation(
        observed_at=observed_at,
        temperature_c=float(temperature),
        feels_like_c=block.get("apparent_temperature"),
        humidity_pct=block.get("relative_humidity_2m"),
        precipitation_mm=block.get("precipitation"),
        wind_speed_kmh=block.get("wind_speed_10m"),
        wind_direction_deg=block.get("wind_direction_10m"),
        cloud_cover_pct=block.get("cloud_cover"),
        pressure_hpa=block.get("pressure_msl"),
        condition=condition,
        condition_label=label,
    )


async def _fetch_daily(latitude: float, longitude: float) -> ProviderResult[dict[str, Any]]:
    try:
        payload = await get_json(
            OPEN_METEO_SOURCE,
            FORECAST_URL,
            {
                "latitude": latitude,
                "longitude": longitude,
                "daily": ",".join(DAILY_VARIABLES),
                "past_days": CANONICAL_PAST_DAYS,
                "forecast_days": CANONICAL_FORECAST_DAYS,
                "timezone": "auto",
                "wind_speed_unit": "kmh",
            },
        )
    except ProviderCallError as exc:
        return ProviderResult.unavailable(OPEN_METEO_SOURCE, exc.reason)
    return ProviderResult.live(payload, OPEN_METEO_SOURCE)


async def _fetch_hourly(latitude: float, longitude: float) -> ProviderResult[dict[str, Any]]:
    try:
        payload = await get_json(
            OPEN_METEO_SOURCE,
            FORECAST_URL,
            {
                "latitude": latitude,
                "longitude": longitude,
                "hourly": ",".join(HOURLY_VARIABLES),
                "current": ",".join(CURRENT_VARIABLES),
                "forecast_days": HOURLY_FORECAST_DAYS,
                "timezone": "auto",
                "wind_speed_unit": "kmh",
            },
        )
    except ProviderCallError as exc:
        return ProviderResult.unavailable(OPEN_METEO_SOURCE, exc.reason)
    return ProviderResult.live(payload, OPEN_METEO_SOURCE)


async def open_meteo_observations(
    latitude: float, longitude: float
) -> ProviderResult[WeatherObservations]:
    """Fetch the canonical window from Open-Meteo at these exact coordinates.

    The daily and hourly documents are one logical operation, so they share a single
    wall-clock budget: an unresponsive upstream cannot consume the deadline twice.
    The budget is opened here rather than in the service layer so it covers only real
    network work — the simulated provider returns from `get_observations` before this
    function is reached and never engages the deadline machinery at all.
    """
    with provider_deadline():
        return await _open_meteo_within_budget(latitude, longitude)


async def _open_meteo_within_budget(
    latitude: float, longitude: float
) -> ProviderResult[WeatherObservations]:
    daily_cache = get_cache("weather_daily", settings.CACHE_TTL_WEATHER_S)
    hourly_cache = get_cache("weather_hourly", settings.CACHE_TTL_WEATHER_S)
    key = build_key(coord_key(latitude, longitude))

    daily_result = await daily_cache.get_or_fetch(key, lambda: _fetch_daily(latitude, longitude))
    if not daily_result.ok or daily_result.data is None:
        return ProviderResult.unavailable(
            OPEN_METEO_SOURCE,
            daily_result.meta.note or "Weather provider unavailable.",
        )

    hourly_result = await hourly_cache.get_or_fetch(key, lambda: _fetch_hourly(latitude, longitude))

    daily_payload = daily_result.data
    hourly_payload = hourly_result.data if hourly_result.ok else {}

    observations = WeatherObservations(
        # Echo the provider's own resolved coordinates, which may be nudged to its
        # nearest grid cell. The request still used the farm's exact position.
        latitude=daily_payload.get("latitude", latitude),
        longitude=daily_payload.get("longitude", longitude),
        timezone=daily_payload.get("timezone") or hourly_payload.get("timezone"),
        current=_parse_current(hourly_payload) if hourly_payload else None,
        daily=_parse_daily(daily_payload),
        hourly=_parse_hourly(hourly_payload) if hourly_payload else [],
    )

    if not observations.daily:
        return ProviderResult.unavailable(
            OPEN_METEO_SOURCE, "Provider returned no usable daily series."
        )

    # A cache hit on the daily window is what makes the whole result `cached`.
    mode_source = daily_result.meta
    note = (
        f"Open-Meteo forecast at {observations.latitude:.4f}, {observations.longitude:.4f}"
        f" ({len(observations.daily)} daily steps, {len(observations.hourly)} hourly steps)."
    )
    return ProviderResult(
        data=observations,
        meta=type(mode_source)(
            source=OPEN_METEO_SOURCE,
            mode=mode_source.mode,
            fetched_at=mode_source.fetched_at,
            note=note,
        ),
        ok=True,
    )


def simulated_observations(
    latitude: float,
    longitude: float,
    *,
    today: date | None = None,
) -> ProviderResult[WeatherObservations]:
    """Build the canonical window from the deterministic simulator.

    Kept for tests, offline development and demo mode. Output is always stamped
    `simulated`.
    """
    anchor = today or datetime.now(UTC).date()
    start = anchor - timedelta(days=CANONICAL_PAST_DAYS)
    total = CANONICAL_PAST_DAYS + CANONICAL_FORECAST_DAYS

    daily = [
        DailyObservation(
            date=day.date,
            temp_min_c=day.temp_min_c,
            temp_max_c=day.temp_max_c,
            temp_mean_c=day.temp_mean_c,
            humidity_pct=day.humidity_pct,
            precipitation_mm=day.precipitation_mm,
            precipitation_hours=day.precipitation_hours,
            wind_kmh=day.wind_kmh,
            et0_mm=day.et0_mm,
            radiation_mj_m2=round(
                simulation.extraterrestrial_radiation_mm(latitude, day.date.timetuple().tm_yday)
                * 2.45,
                2,
            ),
            cloud_cover_pct=day.cloud_cover_pct,
            condition=day.condition,
        )
        for day in simulation.simulate_days(latitude, longitude, start, total)
    ]

    hourly = [
        HourlyObservation(
            time=ts,
            temperature_c=temp,
            humidity_pct=humidity,
            precipitation_mm=precip,
            wind_kmh=day.wind_kmh,
            et0_mm=round(day.et0_mm / 24, 3),
            radiation_w_m2=None,
            soil_moisture_m3m3=round(
                max(0.05, min(0.45, 0.18 + (day.precipitation_mm or 0.0) * 0.004)), 3
            ),
            soil_temperature_c=round(temp - 1.5, 1),
        )
        for ts, day, temp, humidity, precip in simulation.simulate_hours(
            latitude, longitude, anchor, HOURLY_FORECAST_DAYS
        )
    ]

    today_sim = simulation.simulate_day(latitude, longitude, anchor)
    condition_label = {
        "clear": "Clear",
        "partly_cloudy": "Partly cloudy",
        "rain": "Rain",
    }.get(today_sim.condition)

    current = CurrentObservation(
        observed_at=datetime.now(UTC),
        temperature_c=today_sim.temp_mean_c,
        feels_like_c=round(today_sim.temp_mean_c + (today_sim.humidity_pct - 55) * 0.035, 1),
        humidity_pct=today_sim.humidity_pct,
        precipitation_mm=today_sim.precipitation_mm,
        wind_speed_kmh=today_sim.wind_kmh,
        wind_direction_deg=round((abs(longitude) * 7 + anchor.timetuple().tm_yday * 3) % 360, 1),
        cloud_cover_pct=today_sim.cloud_cover_pct,
        pressure_hpa=round(1013.0 - latitude * 0.05, 1),
        condition=today_sim.condition,
        condition_label=condition_label,
    )

    return ProviderResult.simulated(
        WeatherObservations(
            latitude=latitude,
            longitude=longitude,
            timezone="UTC",
            current=current,
            daily=daily,
            hourly=hourly,
        ),
        "Simulated weather: seasonal climatology by latitude with FAO-56 reference "
        "evapotranspiration. Not a forecast or an observation.",
    )


async def get_observations(
    latitude: float, longitude: float, *, today: date | None = None
) -> ProviderResult[WeatherObservations]:
    """Fetch weather using the configured provider.

    On provider failure the result is `unavailable` — this function never silently
    substitutes simulated values for real ones. Whether a fallback is acceptable is
    the caller's decision, and the caller must label it.
    """
    if settings.WEATHER_PROVIDER == "simulated":
        return simulated_observations(latitude, longitude, today=today)

    return await open_meteo_observations(latitude, longitude)
