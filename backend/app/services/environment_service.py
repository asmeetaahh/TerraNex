"""The single source of environmental truth for a farm.

Every consumer — `/weather`, `/soil`, `/vegetation`, `POST /analysis` and the
dashboard — reads from one :class:`EnvironmentSnapshot` produced by
:func:`gather_environment`. Nothing computes its own weather.

That matters because the alternative is subtly incoherent output: analysis previously
called the simulator directly, so wiring in a real provider would have produced a
dashboard showing genuine current conditions beside risk scores derived from invented
ones, with provenance claiming both were the same. Routing everything through one
snapshot makes that state unrepresentable.

The snapshot spans a **canonical window** (92 days back, 16 forward) that every
consumer slices from, so a 7-day forecast panel and a 30-day water balance are
guaranteed to agree on any date they share.

Soil and vegetation remain simulated in this phase. They carry their own provenance,
so a response can honestly report live weather alongside simulated soil.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from app.core.config import settings
from app.core.logging import get_logger
from app.db.memory import FarmRecord
from app.providers import weather as weather_provider
from app.providers.base import (
    CurrentObservation,
    DailyObservation,
    HourlyObservation,
    WeatherObservations,
    simulated_meta,
)
from app.schemas.common import DataMode, DataSourceMeta
from app.schemas.soil import SoilProfile
from app.schemas.vegetation import VegetationPoint, VegetationSeries
from app.schemas.weather import (
    WeatherBundle,
    WeatherCurrent,
    WeatherDaily,
    WeatherHistorySummary,
    WeatherHourly,
)
from app.services.farm_service import primary_planting, require_farm
from app.services.simulation import SimulatedSoil, simulate_ndvi, simulate_soil

logger = get_logger(__name__)

VEGETATION_WINDOW_DAYS = 90
VEGETATION_STEP_DAYS = 5


def _today() -> date:
    return datetime.now(UTC).date()


@dataclass(slots=True)
class EnvironmentSnapshot:
    """One coherent view of a farm's environment at a moment in time."""

    farm_id: str
    latitude: float
    longitude: float
    timezone: str | None
    today: date

    daily: list[DailyObservation]
    hourly: list[HourlyObservation]
    current: CurrentObservation | None
    soil: SimulatedSoil
    vegetation: list[tuple[date, float]]

    weather_meta: DataSourceMeta
    soil_meta: DataSourceMeta
    vegetation_meta: DataSourceMeta

    # Providers that genuinely failed or degraded while building this snapshot.
    # Deliberately simulated inputs are NOT listed: soil and vegetation are
    # simulated by design in this phase, which is a stated capability, not a fault.
    # Their provenance is reported through `sources`.
    failed_sources: list[str] = field(default_factory=list)

    # ---- slicing helpers: every consumer reads the same underlying series ----

    def history(self, days: int) -> list[DailyObservation]:
        """The `days` days immediately before today, oldest first."""
        start = self.today - timedelta(days=days)
        return [d for d in self.daily if start <= d.date < self.today]

    def forecast(self, days: int) -> list[DailyObservation]:
        """Today plus the following `days - 1` days."""
        end = self.today + timedelta(days=days)
        return [d for d in self.daily if self.today <= d.date < end]

    def between(self, start: date, end: date) -> list[DailyObservation]:
        return [d for d in self.daily if start <= d.date <= end]

    def day(self, when: date) -> DailyObservation | None:
        for observation in self.daily:
            if observation.date == when:
                return observation
        return None

    @property
    def available_history_days(self) -> int:
        past = [d for d in self.daily if d.date < self.today]
        return len(past)

    @property
    def sources(self) -> list[DataSourceMeta]:
        return [self.weather_meta, self.soil_meta, self.vegetation_meta]

    @property
    def degraded_sources(self) -> list[str]:
        """Provider names that failed or fell back, de-duplicated, in first-seen order.

        Empty in normal operation — including when soil and vegetation are simulated,
        because that is intended behaviour in this phase rather than degradation.
        Provenance for every input is reported separately through `sources`.
        """
        return list(dict.fromkeys(self.failed_sources))


async def gather_environment(record: FarmRecord) -> EnvironmentSnapshot:
    """Assemble the snapshot for one farm, using its exact stored coordinates.

    Coordinates come from the farm record — the ones the user selected during
    registration. Nothing here re-resolves a location from a name.
    """
    latitude, longitude = record.latitude, record.longitude
    today = _today()

    result = await weather_provider.get_observations(latitude, longitude, today=today)
    failed_sources: list[str] = []

    if not result.ok or result.data is None:
        # A real provider was asked for and could not answer — that is degradation,
        # and it is recorded whether or not a fallback covers it.
        failed_sources.append(result.meta.source)

        if settings.WEATHER_FALLBACK_TO_SIMULATION:
            # Fall back so the product stays usable, but label it honestly: the
            # response will report mode=simulated, never live.
            logger.warning(
                "weather_provider_fallback",
                extra={
                    "farm_id": str(record.id),
                    "source": result.meta.source,
                    "reason": result.meta.note,
                },
            )
            fallback = weather_provider.simulated_observations(latitude, longitude, today=today)
            observations = fallback.data
            weather_meta = DataSourceMeta(
                source=fallback.meta.source,
                mode=DataMode.simulated,
                fetched_at=fallback.meta.fetched_at,
                note=(
                    f"{result.meta.source} was unavailable ({result.meta.note}); "
                    "values below are simulated, not observations."
                ),
            )
        else:
            observations = WeatherObservations(
                latitude=latitude,
                longitude=longitude,
                timezone=None,
                current=None,
                daily=[],
                hourly=[],
            )
            weather_meta = result.meta
    else:
        observations = result.data
        weather_meta = result.meta

    assert observations is not None  # narrowed by both branches above

    soil = simulate_soil(latitude, longitude)
    has_crop = primary_planting(record.id) is not None

    vegetation = [
        (
            sample_date,
            simulate_ndvi(latitude, longitude, sample_date, has_crop=has_crop),
        )
        for sample_date in _vegetation_dates(today)
    ]

    return EnvironmentSnapshot(
        farm_id=str(record.id),
        latitude=latitude,
        longitude=longitude,
        timezone=observations.timezone,
        today=today,
        daily=observations.daily,
        hourly=observations.hourly,
        current=observations.current,
        soil=soil,
        vegetation=vegetation,
        weather_meta=weather_meta,
        failed_sources=failed_sources,
        soil_meta=simulated_meta(
            "Simulated soil: particle sizes classified on the USDA texture triangle. "
            "Not a soil survey or laboratory result."
        ),
        vegetation_meta=simulated_meta(
            "Simulated vegetation indices: seasonal canopy model. "
            "Not derived from satellite imagery."
        ),
    )


def _vegetation_dates(today: date, days: int = VEGETATION_WINDOW_DAYS) -> list[date]:
    return [today - timedelta(days=offset) for offset in range(days, -1, -VEGETATION_STEP_DAYS)]


# --------------------------------------------------------------------------
# Projections onto the API schemas
# --------------------------------------------------------------------------


def _longest_dry_spell(days: list[DailyObservation]) -> int:
    """Longest run of days that reported little or no rain.

    A day with no reading breaks the run rather than extending it — an unmeasured
    day is not evidence of dryness.
    """
    longest = current = 0
    for day in days:
        if day.precipitation_mm is None:
            current = 0
        elif day.precipitation_mm <= 0.2:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def to_weather_bundle(
    env: EnvironmentSnapshot, *, forecast_days: int, history_days: int
) -> WeatherBundle:
    """Slice the snapshot into the response shape. No recomputation."""
    forecast = env.forecast(forecast_days)
    history = env.history(history_days)
    history_start = env.today - timedelta(days=history_days)

    current = None
    if env.current is not None:
        current = WeatherCurrent(
            observed_at=env.current.observed_at,
            temperature_c=env.current.temperature_c,
            feels_like_c=env.current.feels_like_c,
            humidity_pct=env.current.humidity_pct,
            precipitation_mm=env.current.precipitation_mm,
            wind_speed_kmh=env.current.wind_speed_kmh,
            wind_direction_deg=env.current.wind_direction_deg,
            cloud_cover_pct=env.current.cloud_cover_pct,
            pressure_hpa=env.current.pressure_hpa,
            condition=env.current.condition,
            condition_label=env.current.condition_label,
        )

    hourly = [
        WeatherHourly(
            time=step.time,
            temperature_c=step.temperature_c,
            humidity_pct=step.humidity_pct,
            precipitation_mm=step.precipitation_mm,
            wind_speed_kmh=step.wind_kmh,
            et0_mm=step.et0_mm,
            soil_moisture_m3m3=step.soil_moisture_m3m3,
            soil_temperature_c=step.soil_temperature_c,
        )
        for step in env.hourly
    ]

    daily = [
        WeatherDaily(
            date=day.date,
            temp_min_c=day.temp_min_c,
            temp_max_c=day.temp_max_c,
            temp_mean_c=day.temp_mean_c,
            humidity_mean_pct=day.humidity_pct,
            precipitation_mm=day.precipitation_mm,
            precipitation_hours=day.precipitation_hours,
            wind_max_kmh=day.wind_kmh,
            et0_mm=day.et0_mm,
            condition=day.condition,
        )
        for day in forecast
    ]

    temps = [d.temp_mean_c for d in history if d.temp_mean_c is not None]
    et0_values = [d.et0_mm for d in history if d.et0_mm is not None]

    history_summary = WeatherHistorySummary(
        window_days=history_days,
        start_date=history_start,
        end_date=env.today - timedelta(days=1) if history_days else env.today,
        # Sums only the days that reported rainfall. The field is required by the
        # contract, so an all-unknown window reports 0.0 mm across zero rain days —
        # `rain_days` alongside it shows nothing was observed.
        total_precipitation_mm=round(
            sum(d.precipitation_mm for d in history if d.precipitation_mm is not None), 1
        ),
        total_et0_mm=round(sum(et0_values), 1) if et0_values else None,
        mean_temp_c=round(sum(temps) / len(temps), 1) if temps else None,
        max_temp_c=max((d.temp_max_c for d in history if d.temp_max_c is not None), default=None),
        min_temp_c=min((d.temp_min_c for d in history if d.temp_min_c is not None), default=None),
        rain_days=sum(
            1 for d in history if d.precipitation_mm is not None and d.precipitation_mm > 0.2
        ),
        longest_dry_spell_days=_longest_dry_spell(history),
    )

    return WeatherBundle(
        farm_id=env.farm_id,
        latitude=env.latitude,
        longitude=env.longitude,
        timezone=env.timezone,
        current=current,
        hourly=hourly,
        daily=daily,
        history=history_summary,
        meta=env.weather_meta,
    )


def to_soil_profile(env: EnvironmentSnapshot) -> SoilProfile:
    soil = env.soil
    return SoilProfile(
        farm_id=env.farm_id,
        depth_cm="0-30",
        ph=soil.ph,
        organic_carbon_pct=soil.organic_carbon_pct,
        nitrogen_g_kg=soil.nitrogen_g_kg,
        cec_cmol_kg=soil.cec_cmol_kg,
        bulk_density_kg_dm3=soil.bulk_density_kg_dm3,
        sand_pct=soil.sand_pct,
        silt_pct=soil.silt_pct,
        clay_pct=soil.clay_pct,
        texture_class=soil.texture_class,
        water_holding_capacity_mm=soil.water_holding_capacity_mm,
        meta=env.soil_meta,
    )


def to_vegetation_series(env: EnvironmentSnapshot, *, days: int) -> VegetationSeries:
    cutoff = env.today - timedelta(days=days)
    samples = [(d, v) for d, v in env.vegetation if d >= cutoff] or env.vegetation

    series = [
        VegetationPoint(
            date=sample_date,
            ndvi=value,
            evi=round(value * 0.85, 3),
            cloud_cover_pct=(
                observation.cloud_cover_pct
                if (observation := env.day(sample_date)) is not None
                else None
            ),
        )
        for sample_date, value in samples
    ]

    values = [p.ndvi for p in series if p.ndvi is not None]
    current = values[-1] if values else None
    mean = round(sum(values) / len(values), 3) if values else None

    trend = trend_pct = None
    if len(values) >= 2 and values[0] != 0:
        change = (values[-1] - values[0]) / abs(values[0]) * 100
        trend_pct = round(change, 1)
        trend = "improving" if change > 5 else "declining" if change < -5 else "stable"

    return VegetationSeries(
        farm_id=env.farm_id,
        series=series,
        current_ndvi=current,
        mean_ndvi=mean,
        trend=trend,
        trend_pct=trend_pct,
        meta=env.vegetation_meta,
    )


# --------------------------------------------------------------------------
# Endpoint entry points
# --------------------------------------------------------------------------


async def weather_for_farm(farm_id, *, forecast_days: int, history_days: int) -> WeatherBundle:
    env = await gather_environment(require_farm(farm_id))
    return to_weather_bundle(env, forecast_days=forecast_days, history_days=history_days)


async def soil_for_farm(farm_id) -> SoilProfile:
    env = await gather_environment(require_farm(farm_id))
    return to_soil_profile(env)


async def vegetation_for_farm(farm_id, *, days: int) -> VegetationSeries:
    env = await gather_environment(require_farm(farm_id))
    return to_vegetation_series(env, days=days)
