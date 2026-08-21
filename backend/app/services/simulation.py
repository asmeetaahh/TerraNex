"""Deterministic environmental simulation.

**Everything this module produces is SIMULATED.** It is not an observation, a
forecast, or a measurement. Every payload built from it is stamped
`mode="simulated"`, `source="simulated"` so the API can never present it as real —
see `app.schemas.common.DataMode`.

Phase 3 exists so the frontend can build against realistic, stable payloads before
real providers land. Two properties make that work:

**Determinism.** Every value derives from `seeded_rng(...)`, a SHA-256 seed over the
inputs (coordinates, date, purpose). Python's built-in `hash()` is deliberately not
used — it is salted per process, so it would produce different values on every
restart. Identical requests therefore return identical payloads across processes,
machines and restarts, which is what makes contract tests and demos stable.

**Physical shape.** Where a real formula is cheap, it is used rather than noise:
extraterrestrial radiation and reference evapotranspiration follow FAO-56, and soil
texture classes come from the USDA triangle. Simulated values then vary the way real
ones would — hot dry sites really do show water deficits — so a frontend built
against these will not need reworking when live data arrives.

None of this is the deterministic risk engine described in `docs/ARCHITECTURE.md`;
that arrives with real provider data and replaces the scoring here.
"""

import hashlib
import math
import random
from datetime import UTC, date, datetime, timedelta, timezone

from app.providers.base import simulated_meta as provider_simulated_meta
from app.schemas.common import DataSourceMeta
from app.schemas.enums import SoilTexture

SIMULATED_SOURCE = "simulated"

# Fraction of soil volume that is plant-available water, by USDA texture class.
# Used to convert texture into a water-holding capacity in mm.
_AVAILABLE_WATER_FRACTION: dict[SoilTexture, float] = {
    SoilTexture.sand: 0.06,
    SoilTexture.loamy_sand: 0.08,
    SoilTexture.sandy_loam: 0.12,
    SoilTexture.loam: 0.17,
    SoilTexture.silt_loam: 0.19,
    SoilTexture.silt: 0.18,
    SoilTexture.sandy_clay_loam: 0.14,
    SoilTexture.clay_loam: 0.16,
    SoilTexture.silty_clay_loam: 0.17,
    SoilTexture.sandy_clay: 0.13,
    SoilTexture.silty_clay: 0.14,
    SoilTexture.clay: 0.13,
}

_ROOT_ZONE_MM = 300.0  # 30 cm root zone, matching the reported soil depth interval


# --------------------------------------------------------------------------
# Determinism primitives
# --------------------------------------------------------------------------


def stable_seed(*parts: object) -> int:
    """A process-independent seed derived from the given parts.

    SHA-256 rather than `hash()`: string hashing is salted per interpreter run, so
    `hash()` would silently break determinism across restarts.
    """
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def seeded_rng(*parts: object) -> random.Random:
    """An RNG whose stream depends only on `parts`."""
    return random.Random(stable_seed(*parts))


def simulated_meta(note: str | None = None) -> DataSourceMeta:
    """Provenance for any simulated payload.

    Delegates to the shared provenance factory in `app.providers.base`, which pins
    both source and mode — so no call site can label generated values as live.
    """
    return provider_simulated_meta(note)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# --------------------------------------------------------------------------
# Climate
# --------------------------------------------------------------------------


def extraterrestrial_radiation_mm(latitude: float, day_of_year: int) -> float:
    """FAO-56 extraterrestrial radiation (Ra), expressed in mm/day equivalent.

    Real physics, no randomness — this is what gives the simulated ET₀ a believable
    seasonal and latitudinal shape.

    Latitude is **not** clamped. The only domain hazard is `acos`, and its argument is
    already clamped to [-1, 1] below, which is exactly what produces the polar cases:
    an argument at -1 gives a sunset angle of pi (24 hours of daylight) and one at +1
    gives 0 (polar night, Ra = 0). Clamping latitude instead collapsed every position
    beyond +/-66 deg onto that boundary's value, so a farm at 69 deg N saw the same
    midsummer radiation as one at 66 deg and never saw a polar night at all.
    """
    phi = math.radians(latitude)
    dr = 1 + 0.033 * math.cos(2 * math.pi * day_of_year / 365.25)
    decl = 0.409 * math.sin(2 * math.pi * day_of_year / 365.25 - 1.39)

    cos_ws = _clamp(-math.tan(phi) * math.tan(decl), -1.0, 1.0)
    ws = math.acos(cos_ws)

    ra_mj = (
        (24 * 60 / math.pi)
        * 0.0820
        * dr
        * (ws * math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.sin(ws))
    )
    return max(0.0, ra_mj * 0.408)  # MJ/m²/day → mm/day


def solar_utc_offset_hours(longitude: float) -> int:
    """The whole-hour UTC offset implied by a longitude, at 15 deg per hour.

    A *solar* offset, not a civil one — see :func:`solar_timezone_name`.
    """
    # Etc/GMT covers UTC-12 to UTC+14; a longitude in [-180, 180] never leaves
    # [-12, 12], but clamping keeps an out-of-range input from producing an
    # unresolvable zone name.
    return int(_clamp(round(longitude / 15.0), -12, 14))


def solar_timezone_name(longitude: float) -> str:
    """A valid IANA `Etc/GMT+-N` zone for a longitude.

    **This is a solar-longitude approximation, not a political timezone.** It reports
    the offset the sun implies at this meridian, which is often not the offset the
    country observes: China spans five solar hours on a single civil zone, India sits
    at a half-hour offset no whole-hour zone can express, and nothing here models DST.
    Naming a real zone such as `Asia/Shanghai` would be a fabrication the simulator
    has no basis for, so it names only what it actually knows.

    Note the POSIX sign inversion baked into the `Etc/GMT*` names: `Etc/GMT-3` is
    **UTC+3**. Positive longitudes (east) therefore map to `Etc/GMT-N`.
    """
    offset = solar_utc_offset_hours(longitude)
    if offset == 0:
        return "Etc/GMT"
    return f"Etc/GMT{-offset:+d}"


def climate_zone(latitude: float) -> str:
    abs_lat = abs(latitude)
    if abs_lat < 23.5:
        return "tropical"
    if abs_lat < 35:
        return "subtropical"
    if abs_lat < 55:
        return "temperate"
    return "boreal"


def _seasonal_temperature(latitude: float, day_of_year: int) -> tuple[float, float]:
    """Mean daily temperature and its diurnal range for a latitude and day.

    Returns (mean_temp_c, diurnal_range_c).
    """
    abs_lat = abs(latitude)
    annual_mean = 29.0 - 0.42 * abs_lat
    amplitude = 0.28 * abs_lat  # seasonality grows with distance from the equator

    # Northern hemisphere peaks near day 196; the south is half a year out of phase.
    peak_day = 196.0 if latitude >= 0 else 196.0 - 182.6
    seasonal = amplitude * math.cos(2 * math.pi * (day_of_year - peak_day) / 365.25)

    diurnal_range = 8.0 + 0.06 * abs_lat
    return annual_mean + seasonal, diurnal_range


def _wet_season_factor(latitude: float, day_of_year: int) -> float:
    """0.2-1.8 multiplier approximating a monsoon/wet-season cycle."""
    peak_day = 210.0 if latitude >= 0 else 210.0 - 182.6
    swing = math.cos(2 * math.pi * (day_of_year - peak_day) / 365.25)
    return 1.0 + 0.8 * swing


def _rain_probability(zone: str, wet_factor: float) -> float:
    base = {"tropical": 0.42, "subtropical": 0.26, "temperate": 0.32, "boreal": 0.28}[zone]
    return _clamp(base * wet_factor, 0.02, 0.85)


def reference_et0_mm(mean_temp_c: float, diurnal_range_c: float, ra_mm: float) -> float:
    """Hargreaves reference evapotranspiration (FAO-56 fallback method)."""
    et0 = 0.0023 * ra_mm * (mean_temp_c + 17.8) * math.sqrt(max(diurnal_range_c, 1.0))
    return round(max(0.0, et0), 2)


# --------------------------------------------------------------------------
# Daily weather
# --------------------------------------------------------------------------


class SimulatedDay:
    """One simulated day of weather at a location."""

    __slots__ = (
        "date",
        "temp_mean_c",
        "temp_min_c",
        "temp_max_c",
        "humidity_pct",
        "precipitation_mm",
        "precipitation_hours",
        "wind_kmh",
        "et0_mm",
        "cloud_cover_pct",
        "condition",
    )

    def __init__(self, **kwargs: object) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


def simulate_day(latitude: float, longitude: float, day: date) -> SimulatedDay:
    """Simulate one day. Depends only on (latitude, longitude, day)."""
    rng = seeded_rng("weather", round(latitude, 3), round(longitude, 3), day.isoformat())
    doy = day.timetuple().tm_yday
    zone = climate_zone(latitude)

    mean_temp, diurnal = _seasonal_temperature(latitude, doy)
    mean_temp += rng.uniform(-3.0, 3.0)  # day-to-day weather variability
    diurnal = max(3.0, diurnal + rng.uniform(-2.0, 2.0))

    temp_min = mean_temp - diurnal / 2
    temp_max = mean_temp + diurnal / 2

    wet = _wet_season_factor(latitude, doy)
    rains = rng.random() < _rain_probability(zone, wet)
    if rains:
        # Heavy-tailed: most rain days are light, a few are downpours.
        precip = round(rng.expovariate(1 / (6.0 * wet)), 1)
        precip_hours = round(_clamp(rng.uniform(1, 10), 0, 24), 1)
        humidity = _clamp(rng.uniform(72, 95), 0, 100)
        cloud = _clamp(rng.uniform(60, 100), 0, 100)
        condition = "rain"
    else:
        precip = 0.0
        precip_hours = 0.0
        humidity = _clamp(rng.uniform(38, 74), 0, 100)
        cloud = _clamp(rng.uniform(0, 55), 0, 100)
        condition = "clear" if cloud < 25 else "partly_cloudy"

    ra = extraterrestrial_radiation_mm(latitude, doy)
    et0 = reference_et0_mm(mean_temp, diurnal, ra)

    return SimulatedDay(
        date=day,
        temp_mean_c=round(mean_temp, 1),
        temp_min_c=round(temp_min, 1),
        temp_max_c=round(temp_max, 1),
        humidity_pct=round(humidity, 1),
        precipitation_mm=precip,
        precipitation_hours=precip_hours,
        wind_kmh=round(_clamp(rng.gauss(11, 6), 0, 90), 1),
        et0_mm=et0,
        cloud_cover_pct=round(cloud, 1),
        condition=condition,
    )


def simulate_days(latitude: float, longitude: float, start: date, count: int) -> list[SimulatedDay]:
    return [simulate_day(latitude, longitude, start + timedelta(days=i)) for i in range(count)]


def simulate_hours(
    latitude: float, longitude: float, start: date, days: int
) -> list[tuple[datetime, SimulatedDay, float, float, float]]:
    """Hourly steps derived from their parent day.

    Yields `(timestamp, parent_day, temp_c, humidity_pct, precip_mm)`. Hourly
    resolution matters because disease rules are defined over consecutive hours of
    humidity and temperature, not daily means.

    `hour` is a **local** hour at this longitude, so the diurnal curve peaks in the
    local afternoon rather than the afternoon at Greenwich. Timestamps are returned as
    UTC instants, matching what the real provider's parser produces, so a consumer
    never has to know which provider supplied them.
    """
    # The same offset `solar_timezone_name` reports, so the timezone a response
    # advertises and the instants it carries cannot disagree.
    tz = timezone(timedelta(hours=solar_utc_offset_hours(longitude)))
    out = []
    for day in simulate_days(latitude, longitude, start, days):
        local_midnight = datetime.combine(day.date, datetime.min.time(), tzinfo=tz)
        for hour in range(24):
            # Coolest just before dawn, warmest mid-afternoon.
            phase = math.cos(2 * math.pi * (hour - 15) / 24)
            temp = day.temp_mean_c + (day.temp_max_c - day.temp_min_c) / 2 * phase
            # Humidity moves inversely to temperature.
            humidity = _clamp(day.humidity_pct - 12 * phase, 0, 100)
            precip = round(day.precipitation_mm / 24, 3) if day.precipitation_mm else 0.0
            ts = (local_midnight + timedelta(hours=hour)).astimezone(UTC)
            out.append((ts, day, round(temp, 1), round(humidity, 1), precip))
    return out


# --------------------------------------------------------------------------
# Soil
# --------------------------------------------------------------------------


def usda_texture_class(sand: float, silt: float, clay: float) -> SoilTexture:
    """Classify a particle-size distribution using the USDA texture triangle."""
    if silt + 1.5 * clay < 15:
        return SoilTexture.sand
    if silt + 2 * clay < 30:
        return SoilTexture.loamy_sand
    if (7 <= clay < 20 and sand > 52 and silt + 2 * clay >= 30) or (clay < 7 and silt < 50):
        return SoilTexture.sandy_loam
    if 7 <= clay < 27 and 28 <= silt < 50 and sand <= 52:
        return SoilTexture.loam
    if silt >= 80 and clay < 12:
        return SoilTexture.silt
    if (silt >= 50 and 12 <= clay < 27) or (50 <= silt < 80 and clay < 12):
        return SoilTexture.silt_loam
    if 20 <= clay < 35 and silt < 28 and sand > 45:
        return SoilTexture.sandy_clay_loam
    if 27 <= clay < 40 and 20 < sand <= 45:
        return SoilTexture.clay_loam
    if 27 <= clay < 40 and sand <= 20:
        return SoilTexture.silty_clay_loam
    if clay >= 35 and sand > 45:
        return SoilTexture.sandy_clay
    if clay >= 40 and silt >= 40:
        return SoilTexture.silty_clay
    return SoilTexture.clay


class SimulatedSoil:
    """Simulated soil properties at a location. Stable for a given coordinate."""

    __slots__ = (
        "ph",
        "organic_carbon_pct",
        "nitrogen_g_kg",
        "cec_cmol_kg",
        "bulk_density_kg_dm3",
        "sand_pct",
        "silt_pct",
        "clay_pct",
        "texture_class",
        "water_holding_capacity_mm",
    )

    def __init__(self, **kwargs: object) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


def simulate_soil(latitude: float, longitude: float) -> SimulatedSoil:
    """Simulate a soil profile. Depends only on coordinates — soil is static, so a
    farm's soil never changes between requests."""
    rng = seeded_rng("soil", round(latitude, 3), round(longitude, 3))

    sand = rng.uniform(15, 70)
    clay = rng.uniform(8, min(50, 95 - sand))
    silt = max(1.0, 100 - sand - clay)
    total = sand + silt + clay
    sand, silt, clay = (100 * v / total for v in (sand, silt, clay))

    texture = usda_texture_class(sand, silt, clay)

    # Humid tropics tend acidic; drylands tend alkaline.
    ph_centre = 6.6 - 0.9 * math.cos(math.radians(abs(latitude) * 2))
    ph = _clamp(rng.gauss(ph_centre, 0.55), 3.9, 8.8)

    # Organic carbon falls with mean temperature (faster mineralisation).
    soc_centre = _clamp(3.4 - 0.055 * (29.0 - 0.42 * abs(latitude)), 0.4, 4.0)
    soc = _clamp(rng.gauss(soc_centre, 0.6), 0.15, 6.0)

    awc = _AVAILABLE_WATER_FRACTION[texture]

    return SimulatedSoil(
        ph=round(ph, 1),
        organic_carbon_pct=round(soc, 2),
        nitrogen_g_kg=round(_clamp(soc * 0.9 + rng.uniform(-0.2, 0.2), 0.05, 6.0), 2),
        cec_cmol_kg=round(_clamp(4 + clay * 0.42 + soc * 2.6, 1, 60), 1),
        bulk_density_kg_dm3=round(_clamp(1.62 - soc * 0.06, 0.9, 1.8), 2),
        sand_pct=round(sand, 1),
        silt_pct=round(silt, 1),
        clay_pct=round(clay, 1),
        texture_class=texture,
        water_holding_capacity_mm=round(awc * _ROOT_ZONE_MM, 1),
    )


# --------------------------------------------------------------------------
# Vegetation
# --------------------------------------------------------------------------


def simulate_ndvi(latitude: float, longitude: float, day: date, *, has_crop: bool) -> float:
    """Simulate an NDVI value. Bare ground reads low; a growing canopy reads high."""
    rng = seeded_rng("ndvi", round(latitude, 3), round(longitude, 3), day.isoformat())
    doy = day.timetuple().tm_yday

    peak_day = 210.0 if latitude >= 0 else 210.0 - 182.6
    seasonal = math.cos(2 * math.pi * (doy - peak_day) / 365.25)

    base = 0.34 if not has_crop else 0.52
    amplitude = 0.10 if not has_crop else 0.22

    return round(_clamp(base + amplitude * seasonal + rng.uniform(-0.05, 0.05), -0.05, 0.95), 3)
