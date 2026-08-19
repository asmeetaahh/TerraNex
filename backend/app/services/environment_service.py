"""Weather, soil and vegetation payloads built from the Phase 3 simulator.

Every response returned here carries `simulated_meta()`, so `mode` is always
`simulated` and `source` is always `"simulated"`. Nothing in this module can emit
`live` or `cached` — that only becomes possible when real providers land.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from app.db.memory import FarmRecord
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
from app.services.simulation import (
    extraterrestrial_radiation_mm,
    reference_et0_mm,
    simulate_day,
    simulate_days,
    simulate_hours,
    simulate_ndvi,
    simulate_soil,
    simulated_meta,
)

_CONDITION_LABELS = {
    "clear": "Clear",
    "partly_cloudy": "Partly cloudy",
    "rain": "Rain",
}


def _today() -> date:
    return datetime.now(UTC).date()


def _longest_dry_spell(days) -> int:
    longest = current = 0
    for day in days:
        if day.precipitation_mm <= 0.2:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def build_weather(record: FarmRecord, *, forecast_days: int, history_days: int) -> WeatherBundle:
    lat, lon = record.latitude, record.longitude
    today = _today()

    forecast = simulate_days(lat, lon, today, forecast_days)
    history_start = today - timedelta(days=history_days)
    history = simulate_days(lat, lon, history_start, history_days)

    now = datetime.now(UTC)
    current_day = simulate_day(lat, lon, today)
    ra = extraterrestrial_radiation_mm(lat, today.timetuple().tm_yday)

    current = WeatherCurrent(
        observed_at=now,
        temperature_c=current_day.temp_mean_c,
        feels_like_c=round(current_day.temp_mean_c + (current_day.humidity_pct - 55) * 0.035, 1),
        humidity_pct=current_day.humidity_pct,
        precipitation_mm=current_day.precipitation_mm,
        wind_speed_kmh=current_day.wind_kmh,
        wind_direction_deg=round((abs(lon) * 7 + today.timetuple().tm_yday * 3) % 360, 1),
        cloud_cover_pct=current_day.cloud_cover_pct,
        pressure_hpa=round(1013.0 - record.latitude * 0.05, 1),
        condition=current_day.condition,
        condition_label=_CONDITION_LABELS.get(current_day.condition),
    )

    hourly = [
        WeatherHourly(
            time=ts,
            temperature_c=temp,
            humidity_pct=humidity,
            precipitation_mm=precip,
            wind_speed_kmh=day.wind_kmh,
            et0_mm=round(day.et0_mm / 24, 3),
            soil_moisture_m3m3=round(max(0.05, min(0.45, 0.18 + day.precipitation_mm * 0.004)), 3),
            soil_temperature_c=round(temp - 1.5, 1),
        )
        # Hourly detail is only useful for the near term; cap it so the payload
        # stays small enough for a dashboard to fetch on every load.
        for ts, day, temp, humidity, precip in simulate_hours(
            lat, lon, today, min(forecast_days, 3)
        )
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

    temps = [d.temp_mean_c for d in history]
    history_summary = WeatherHistorySummary(
        window_days=history_days,
        start_date=history_start,
        end_date=today - timedelta(days=1) if history_days else today,
        total_precipitation_mm=round(sum(d.precipitation_mm for d in history), 1),
        total_et0_mm=round(sum(d.et0_mm for d in history), 1),
        mean_temp_c=round(sum(temps) / len(temps), 1) if temps else None,
        max_temp_c=max((d.temp_max_c for d in history), default=None),
        min_temp_c=min((d.temp_min_c for d in history), default=None),
        rain_days=sum(1 for d in history if d.precipitation_mm > 0.2),
        longest_dry_spell_days=_longest_dry_spell(history),
    )

    # Referenced so the simplification above stays honest if ET₀ is ever recomputed here.
    del ra

    return WeatherBundle(
        farm_id=str(record.id),
        latitude=lat,
        longitude=lon,
        timezone="UTC",
        current=current,
        hourly=hourly,
        daily=daily,
        history=history_summary,
        meta=simulated_meta(
            "Simulated weather: seasonal climatology by latitude with FAO-56 "
            "reference evapotranspiration. Not a forecast."
        ),
    )


def build_soil(record: FarmRecord) -> SoilProfile:
    soil = simulate_soil(record.latitude, record.longitude)
    return SoilProfile(
        farm_id=str(record.id),
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
        meta=simulated_meta(
            "Simulated soil: particle sizes classified on the USDA texture triangle. "
            "Not a soil survey or laboratory result."
        ),
    )


def build_vegetation(record: FarmRecord, *, days: int) -> VegetationSeries:
    has_crop = primary_planting(record.id) is not None
    today = _today()

    # One observation every 5 days, mirroring a satellite revisit cadence.
    step = 5
    sample_dates = [today - timedelta(days=offset) for offset in range(days, -1, -step)]

    series = [
        VegetationPoint(
            date=sample_date,
            ndvi=simulate_ndvi(record.latitude, record.longitude, sample_date, has_crop=has_crop),
            evi=round(
                simulate_ndvi(record.latitude, record.longitude, sample_date, has_crop=has_crop)
                * 0.85,
                3,
            ),
            cloud_cover_pct=round(
                simulate_day(record.latitude, record.longitude, sample_date).cloud_cover_pct, 1
            ),
        )
        for sample_date in sample_dates
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
        farm_id=str(record.id),
        series=series,
        current_ndvi=current,
        mean_ndvi=mean,
        trend=trend,
        trend_pct=trend_pct,
        meta=simulated_meta(
            "Simulated vegetation indices: seasonal canopy model. "
            "Not derived from satellite imagery."
        ),
    )


def weather_for_farm(farm_id: UUID, *, forecast_days: int, history_days: int) -> WeatherBundle:
    return build_weather(
        require_farm(farm_id), forecast_days=forecast_days, history_days=history_days
    )


def soil_for_farm(farm_id: UUID) -> SoilProfile:
    return build_soil(require_farm(farm_id))


def vegetation_for_farm(farm_id: UUID, *, days: int) -> VegetationSeries:
    return build_vegetation(require_farm(farm_id), days=days)


def reference_et0(mean_temp_c: float, diurnal_range_c: float, latitude: float, day: date) -> float:
    """Exposed for the analysis fixture, which needs ET₀ without a full bundle."""
    ra = extraterrestrial_radiation_mm(latitude, day.timetuple().tm_yday)
    return reference_et0_mm(mean_temp_c, diurnal_range_c, ra)
