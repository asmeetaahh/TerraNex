"""Adapter: live provider data in, pure engine context out.

This is the seam between the two halves of the analysis path. Everything upstream deals
in providers, records and sessions; everything downstream is pure arithmetic over an
:class:`~app.engine.context.AnalysisContext`.

**It lives in the service layer on purpose.** Building a context means reading an
`EnvironmentSnapshot`, which comes from `app.providers`, and resolving crop coefficients,
which reads a file. Neither is permitted inside `app/engine/` — so the conversion
happens here, and the engine receives data it cannot trace back to a source. That is
what `tests/unit/engine/test_engine_is_pure.py` enforces.
"""

from datetime import date, datetime

from app.db.memory import FarmCropRecord, FarmRecord
from app.engine.context import AnalysisContext, CropParameters, DailyPoint, HourlyPoint, SoilPoint
from app.providers.base import DailyObservation, HourlyObservation
from app.rules.registry import RULESET_VERSION, disease_rules_for, resolve_crop_parameters
from app.schemas.crop import Crop
from app.schemas.enums import GrowthStage
from app.services.environment_service import EnvironmentSnapshot


def _numeric(value: object) -> float | None:
    """One usable numeric measurement, or None.

    **This is the trust boundary.** A provider that sends `"hot"` has told us nothing
    comparable, and a string reaching a `>` against a float raises exactly where a
    `None` would. Both mean unknown, so both arrive at the engine as `None`.

    Coercing here rather than inside each engine means every calculation downstream can
    assume its numbers are numbers. `bool` is excluded deliberately: it subclasses `int`,
    so `True` would otherwise pass as 1.0.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _to_daily_point(observation: DailyObservation) -> DailyPoint:
    return DailyPoint(
        day=observation.date,
        temp_min_c=_numeric(observation.temp_min_c),
        temp_max_c=_numeric(observation.temp_max_c),
        temp_mean_c=_numeric(observation.temp_mean_c),
        humidity_pct=_numeric(observation.humidity_pct),
        precipitation_mm=_numeric(observation.precipitation_mm),
        precipitation_hours=_numeric(observation.precipitation_hours),
        wind_kmh=_numeric(observation.wind_kmh),
        et0_mm=_numeric(observation.et0_mm),
        radiation_mj_m2=_numeric(observation.radiation_mj_m2),
    )


def _to_hourly_point(observation: HourlyObservation) -> HourlyPoint:
    return HourlyPoint(
        at=observation.time,
        temperature_c=_numeric(observation.temperature_c),
        humidity_pct=_numeric(observation.humidity_pct),
        precipitation_mm=_numeric(observation.precipitation_mm),
        wind_kmh=_numeric(observation.wind_kmh),
        soil_moisture_m3m3=_numeric(observation.soil_moisture_m3m3),
    )


def _to_soil_point(soil: object) -> SoilPoint:
    """Map the environment's soil onto the engine's value type.

    `getattr` throughout because the source is a slotted object whose attributes are set
    dynamically, and because a real provider will eventually supply a partial profile
    where a simulation supplies a complete one. A missing property must arrive as None,
    not raise — the engine is built to report it as insufficient.
    """
    texture = getattr(soil, "texture_class", None)
    return SoilPoint(
        ph=_numeric(getattr(soil, "ph", None)),
        organic_carbon_pct=_numeric(getattr(soil, "organic_carbon_pct", None)),
        nitrogen_g_kg=_numeric(getattr(soil, "nitrogen_g_kg", None)),
        cec_cmol_kg=_numeric(getattr(soil, "cec_cmol_kg", None)),
        bulk_density_kg_dm3=_numeric(getattr(soil, "bulk_density_kg_dm3", None)),
        sand_pct=_numeric(getattr(soil, "sand_pct", None)),
        silt_pct=_numeric(getattr(soil, "silt_pct", None)),
        clay_pct=_numeric(getattr(soil, "clay_pct", None)),
        texture_class=str(texture) if texture is not None else None,
        water_holding_capacity_mm=_numeric(getattr(soil, "water_holding_capacity_mm", None)),
        available_water_fraction=_numeric(getattr(soil, "available_water_fraction", None)),
    )


def crop_parameters_for(crop: Crop | None) -> CropParameters:
    """Merge the published crop catalog with the FAO-56 ruleset.

    Two sources, deliberately kept distinct: the catalog fields are published through
    the frozen `Crop` schema, while the coefficients come from
    `app/rules/crop_coefficients.yaml` because the contract has nowhere to carry them.
    `parameters_source` records whether the crop had its own coefficients or fell back
    to a category default, so a result can say which.
    """
    resolved = resolve_crop_parameters(
        crop.code if crop else None,
        crop.category if crop else None,
    )
    return CropParameters(
        code=crop.code if crop else None,
        name=crop.name if crop else None,
        category=str(crop.category) if crop and crop.category else None,
        season=str(crop.season) if crop and crop.season else None,
        kc_by_stage=resolved["kc_by_stage"],
        root_depth_m=resolved["root_depth_m"],
        depletion_fraction=resolved["depletion_fraction"],
        parameters_source=resolved["parameters_source"],
        base_temp_c=crop.base_temp_c if crop else None,
        optimal_temp_min_c=crop.optimal_temp_min_c if crop else None,
        optimal_temp_max_c=crop.optimal_temp_max_c if crop else None,
        gdd_to_maturity=crop.gdd_to_maturity if crop else None,
        water_need_mm_season=crop.water_need_mm_season if crop else None,
        ph_min=crop.ph_min if crop else None,
        ph_max=crop.ph_max if crop else None,
        preferred_textures=tuple(str(t) for t in crop.preferred_textures) if crop else (),
        common_diseases=tuple(crop.common_diseases) if crop else (),
        drought_tolerance=str(crop.drought_tolerance) if crop and crop.drought_tolerance else None,
    )


def crop_catalog() -> tuple[CropParameters, ...]:
    """Every crop the recommender may propose, from whichever catalog is configured.

    Routed through `reference_service`, which already branches on `database_enabled()`
    — so a database-backed deployment offers the same twenty-six crops as an in-memory
    one. The recommender previously read `app.db.memory.store` directly, and that store
    is empty whenever a database is configured, so every such deployment silently
    offered zero crops while `GET /reference/crops` returned the full catalog.

    The engine cannot make this call itself: it reads a session. Resolving here is what
    keeps `app/engine/` free of the database while still letting it rank a persisted
    catalog.
    """
    from app.services.reference_service import catalog_crops

    return tuple(crop_parameters_for(crop) for crop in catalog_crops())


def _planting_date(planting: FarmCropRecord | None) -> date | None:
    """The date the crop went in, when it is known and is really a date.

    `FarmCropRecord.planting_date` is typed loosely, and thermal time is accumulated
    from it — so anything that is not a date is treated as absent rather than coerced.
    Guessing an origin would report a season the crop never had.
    """
    if planting is None:
        return None
    value = planting.planting_date
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _harvest_date(planting: FarmCropRecord | None) -> date | None:
    """When the crop is expected to come off, when that is known and is really a date.

    Same discipline as `_planting_date`: the field is loosely typed, and a value that
    is not a date is treated as absent rather than coerced into one.
    """
    if planting is None:
        return None
    value = planting.expected_harvest_date
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def build_context(
    record: FarmRecord,
    env: EnvironmentSnapshot,
    crop: Crop | None,
    stage: GrowthStage,
    planting: FarmCropRecord | None = None,
) -> AnalysisContext:
    """Assemble everything the engine may read, and nothing else."""
    return AnalysisContext(
        farm_id=str(record.id),
        latitude=record.latitude,
        longitude=record.longitude,
        timezone=env.timezone,
        today=env.today,
        daily=tuple(_to_daily_point(day) for day in env.daily),
        hourly=tuple(_to_hourly_point(hour) for hour in env.hourly),
        vegetation=tuple(env.vegetation),
        soil=_to_soil_point(env.soil),
        crop=crop_parameters_for(crop),
        disease_rules=disease_rules_for(crop.code if crop else None),
        catalog=crop_catalog(),
        growth_stage=str(stage),
        irrigation_type=str(record.irrigation_type),
        farming_practice=str(record.farming_practice),
        planting_date=_planting_date(planting),
        expected_harvest_date=_harvest_date(planting),
        area_hectares=record.area_hectares,
        ruleset_version=RULESET_VERSION,
    )
