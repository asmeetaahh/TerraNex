"""Weather payloads.

Every model here carries `meta: DataSourceMeta`. Until a real provider is wired in,
`meta.mode` is `simulated` and the UI must label it — a generated forecast is never
presented as an observation.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.common import DataSourceMeta


class WeatherCurrent(BaseModel):
    """Conditions at the farm right now."""

    observed_at: datetime
    temperature_c: float
    feels_like_c: float | None = None
    humidity_pct: float | None = Field(default=None, ge=0, le=100)
    precipitation_mm: float | None = Field(default=None, ge=0)
    wind_speed_kmh: float | None = Field(default=None, ge=0)
    wind_direction_deg: float | None = Field(default=None, ge=0, le=360)
    cloud_cover_pct: float | None = Field(default=None, ge=0, le=100)
    pressure_hpa: float | None = None
    condition: str | None = Field(default=None, examples=["partly_cloudy"])
    condition_label: str | None = Field(default=None, examples=["Partly cloudy"])


class WeatherHourly(BaseModel):
    """One hourly step. Hourly resolution is what disease rules need — leaf-wetness
    style conditions are defined over consecutive hours, not daily means."""

    time: datetime
    temperature_c: float | None = None
    humidity_pct: float | None = Field(default=None, ge=0, le=100)
    precipitation_mm: float | None = Field(default=None, ge=0)
    wind_speed_kmh: float | None = Field(default=None, ge=0)
    et0_mm: float | None = Field(
        default=None, ge=0, description="FAO-56 reference evapotranspiration."
    )
    soil_moisture_m3m3: float | None = Field(default=None, ge=0, le=1)
    soil_temperature_c: float | None = None


class WeatherDaily(BaseModel):
    """One daily aggregate."""

    date: date
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    temp_mean_c: float | None = None
    humidity_mean_pct: float | None = Field(default=None, ge=0, le=100)
    precipitation_mm: float | None = Field(default=None, ge=0)
    precipitation_hours: float | None = Field(default=None, ge=0, le=24)
    wind_max_kmh: float | None = Field(default=None, ge=0)
    et0_mm: float | None = Field(default=None, ge=0)
    condition: str | None = None


class WeatherHistorySummary(BaseModel):
    """Aggregates over the lookback window — the inputs to the water balance."""

    window_days: int = Field(ge=1, examples=[30])
    start_date: date
    end_date: date
    total_precipitation_mm: float = Field(ge=0)
    total_et0_mm: float | None = Field(default=None, ge=0)
    mean_temp_c: float | None = None
    max_temp_c: float | None = None
    min_temp_c: float | None = None
    rain_days: int = Field(ge=0, description="Days with measurable precipitation.")
    longest_dry_spell_days: int = Field(ge=0)


class WeatherBundle(BaseModel):
    """Response for `GET /api/v1/farms/{farm_id}/weather`.

    Current conditions, forecast, and the historical summary in one payload — the
    dashboard needs all three and should not make three round trips.
    """

    farm_id: str
    latitude: float
    longitude: float
    timezone: str | None = None
    current: WeatherCurrent | None = None
    hourly: list[WeatherHourly] = Field(
        default_factory=list, description="Forecast at hourly resolution."
    )
    daily: list[WeatherDaily] = Field(
        default_factory=list, description="Daily forecast, typically 7 days."
    )
    history: WeatherHistorySummary | None = None
    meta: DataSourceMeta
