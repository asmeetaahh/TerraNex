"""Daily agroclimatology from NASA POWER.

POWER publishes a global, keyless, free daily record derived from satellite and
reanalysis products. It exists here as an **opt-in alternative** to Open-Meteo for
deployments that prefer NASA's record, not as an automatic fallback: nothing selects it
unless `WEATHER_PROVIDER=nasa_power` is set explicitly, and the degradation ladder in
`environment_service` is untouched.

Three properties were measured against the live API rather than assumed, and each one
shapes the code below:

* **`-999` is the fill value, and it is common.** The three most recent days of a
  twenty-one-day window came back `-999.0` for every parameter. Passing that through
  would be catastrophic rather than merely wrong — `-999 mm` of rainfall inverts the
  water balance and would produce confident irrigation advice from a fabricated drought.
  Every value goes through :func:`_measurement`, which maps it to `None`.
* **The record lags.** Complete data ended three days before today. POWER is a *record*,
  not a forecast, so this provider returns history only: no `current` block, and the
  forward half of the canonical window is simply absent.
* **The grid is coarse.** Two points twenty kilometres apart returned byte-identical
  values, consistent with POWER's ~0.5° cell. Acceptable for a regional record;
  materially coarser than Open-Meteo's ~11 km.

**Daily only.** POWER's hourly endpoint covers a different parameter set and is not
requested here, so `hourly=[]`. The disease engine needs consecutive humid hours and will
report itself unassessed rather than guess — which is the correct outcome and not a
degradation this module should paper over.

**`et0_mm` is deliberately left `None`.** POWER publishes evapotranspiration products,
but they are not FAO-56 reference ET₀ and substituting them would quietly change the
water balance. The engine already falls back to Hargreaves from the day's temperature
range — FAO-56's own method for exactly this gap — so leaving the field empty gets a
correct ET₀ rather than a plausible-looking wrong one.
"""

from datetime import date, datetime
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.base import (
    DailyObservation,
    ProviderCallError,
    ProviderResult,
    WeatherObservations,
)
from app.providers.cache import build_key, coord_key, get_cache
from app.providers.http import get_json, provider_deadline

logger = get_logger(__name__)

NASA_POWER_SOURCE = "nasa-power"
NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

#: The agroclimatology community. Selects POWER's agriculture-oriented parameter set,
#: which is where `PRECTOTCORR` (bias-corrected precipitation) lives.
COMMUNITY = "AG"

#: Requested parameters, mapped to the normalised field each one fills.
#:
#: Deliberately minimal: every entry has a consumer in `app/engine/`. `PS` (surface
#: pressure) is available and omitted, because no daily consumer reads it and an unused
#: parameter is a larger payload for no benefit.
PARAMETERS: dict[str, str] = {
    "T2M_MIN": "temp_min_c",
    "T2M_MAX": "temp_max_c",
    "T2M": "temp_mean_c",
    "PRECTOTCORR": "precipitation_mm",
    "RH2M": "humidity_pct",
    "WS2M": "wind_kmh",
    "ALLSKY_SFC_SW_DWN": "radiation_mj_m2",
}

#: POWER's fill value for "no data at this cell on this day".
#:
#: Checked as a threshold rather than for equality: the API returned `-999.0` throughout,
#: but the documented family of fill values sits at or below this, and a `-998` slipping
#: through as a real measurement is not a failure worth risking for one comparison.
FILL_VALUE_THRESHOLD = -900.0

#: How far back to request. Matches the history half of the canonical window the rest of
#: the system already reasons over, so a POWER-backed snapshot slices the same way.
HISTORY_DAYS = 92

#: m/s → km/h. POWER reports `WS2M` in m/s; every normalised field in
#: `WeatherObservations` is km/h, and the weather engine's thresholds are km/h.
MS_TO_KMH = 3.6


def _measurement(value: Any) -> float | None:
    """One usable number, or None.

    **The trust boundary for this provider.** POWER encodes absence as `-999`, which is a
    number and would survive every downstream `is None` check — arriving at the water
    balance as a metre of negative rainfall. Anything at or below the fill threshold, and
    anything non-numeric, becomes `None`, which the engine already understands as "not
    measured".

    `bool` is excluded deliberately: it subclasses `int`, so `True` would otherwise pass
    as 1.0.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return None if number <= FILL_VALUE_THRESHOLD else number


def _parse_day(stamp: str) -> date | None:
    """POWER keys days as `YYYYMMDD`. An unparseable key drops that day."""
    try:
        return datetime.strptime(stamp, "%Y%m%d").date()
    except (ValueError, TypeError):
        return None


def parse_power(payload: dict[str, Any]) -> list[DailyObservation]:
    """Map a POWER response onto normalised daily observations.

    Forgiving about *shape* and strict about *values*, matching `parse_soilgrids`: a
    missing parameter block yields `None` for that field rather than failing the whole
    request, and a day whose every measurement is absent is dropped rather than published
    as an empty observation that would read as a measured calm dry day.
    """
    parameters = payload.get("properties", {}).get("parameter", {})
    if not isinstance(parameters, dict):
        return []

    # Every requested parameter carries the same day keys; union them so a partially
    # populated response still yields whichever days any parameter covers.
    stamps: set[str] = set()
    for block in parameters.values():
        if isinstance(block, dict):
            stamps.update(block.keys())

    observations: list[DailyObservation] = []
    for stamp in sorted(stamps):
        day = _parse_day(stamp)
        if day is None:
            continue

        values: dict[str, float | None] = {}
        for parameter, field_name in PARAMETERS.items():
            block = parameters.get(parameter)
            raw = block.get(stamp) if isinstance(block, dict) else None
            values[field_name] = _measurement(raw)

        if values["wind_kmh"] is not None:
            values["wind_kmh"] = round(values["wind_kmh"] * MS_TO_KMH, 2)

        if all(value is None for value in values.values()):
            # Every parameter was a fill value — POWER has not published this day yet.
            continue

        observations.append(
            DailyObservation(
                date=day,
                temp_min_c=values["temp_min_c"],
                temp_max_c=values["temp_max_c"],
                temp_mean_c=values["temp_mean_c"],
                humidity_pct=values["humidity_pct"],
                precipitation_mm=values["precipitation_mm"],
                # POWER publishes no wet-hour count, and it must not be inferred from
                # the daily total: rain depth says nothing about duration, which is what
                # the disease rules read.
                precipitation_hours=None,
                wind_kmh=values["wind_kmh"],
                # Left empty on purpose — see the module docstring. Hargreaves computes
                # a real FAO-56 ET₀ from the temperature range above.
                et0_mm=None,
                radiation_mj_m2=values["radiation_mj_m2"],
            )
        )

    return observations


async def _fetch(latitude: float, longitude: float, *, today: date) -> ProviderResult[dict]:
    start = today.toordinal() - HISTORY_DAYS
    try:
        payload = await get_json(
            NASA_POWER_SOURCE,
            NASA_POWER_URL,
            {
                "parameters": ",".join(PARAMETERS),
                "community": COMMUNITY,
                "latitude": latitude,
                "longitude": longitude,
                "start": date.fromordinal(start).strftime("%Y%m%d"),
                "end": today.strftime("%Y%m%d"),
                "format": "JSON",
            },
        )
    except ProviderCallError as exc:
        return ProviderResult.unavailable(NASA_POWER_SOURCE, exc.reason)
    return ProviderResult.live(payload, NASA_POWER_SOURCE)


async def power_observations(
    latitude: float, longitude: float, *, today: date | None = None
) -> ProviderResult[WeatherObservations]:
    """Fetch the daily record from NASA POWER at these exact coordinates.

    Reuses the shared HTTP layer, so the configured timeout, retry policy and wall-clock
    deadline apply unchanged, and the shared TTL cache, so repeated requests for one
    field coalesce exactly as they do for Open-Meteo.

    Never raises: a transport failure, an HTTP error or an unusable body all become an
    `unavailable` result, and the caller's existing ladder decides what to do about it.
    """
    day = today or date.today()

    with provider_deadline():
        cache = get_cache("weather_nasa_power", settings.CACHE_TTL_WEATHER_S)
        key = build_key(coord_key(latitude, longitude), day.isoformat())

        result = await cache.get_or_fetch(key, lambda: _fetch(latitude, longitude, today=day))
        if not result.ok or result.data is None:
            return ProviderResult.unavailable(NASA_POWER_SOURCE, result.meta.note)

        daily = parse_power(result.data)
        if not daily:
            # A 200 carrying no usable day is not a success. Reporting it as one would
            # publish an empty history as though the weather had been measured and found
            # to be nothing.
            logger.warning(
                "nasa_power_no_usable_days",
                extra={"source": NASA_POWER_SOURCE, "latitude": latitude, "longitude": longitude},
            )
            return ProviderResult.unavailable(
                NASA_POWER_SOURCE, "NASA POWER returned no usable days for this location."
            )

        return ProviderResult(
            data=WeatherObservations(
                latitude=latitude,
                longitude=longitude,
                # POWER reports UTC days and publishes no timezone for the point.
                timezone=None,
                # A record that lags by days has no "now" to report. Inventing one from
                # the most recent day would present history as a current observation.
                current=None,
                daily=daily,
                hourly=[],
            ),
            meta=result.meta,
            ok=True,
        )
