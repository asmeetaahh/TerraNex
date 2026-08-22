"""Deterministic analysis fixtures.

**This is not the risk engine.** The real engine described in `docs/ARCHITECTURE.md`
runs on live provider data and replaces the scoring here. What this module does is
derive plausible, *internally consistent* fixtures from the simulated environment, so
that a hot dry site really does show a water deficit and a humid warm site really does
show elevated disease pressure. A frontend built against these will not need reworking
when the real engine lands, because only the numbers change — never the shapes.

Two invariants hold throughout:

* every payload records `ai_mode="mock"` — no model is called anywhere in Phase 3,
* every input is listed in `sources` with `mode="simulated"`.

Determinism: a run's content is a pure function of the farm's coordinates, its primary
crop and the current date. Re-running without `force_refresh` returns the *same stored
run*; with `force_refresh` a new run id is minted whose scores are identical.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.deps import CurrentUser
from app.core.errors import NoAnalysisYetError
from app.db.memory import FarmCropRecord, FarmRecord, store
from app.engine import composite as engine_composite
from app.engine import disease as engine_disease
from app.engine import soil as engine_soil
from app.engine import vegetation as engine_vegetation
from app.engine import water as engine_water
from app.engine import weather as engine_weather
from app.engine.composite import AdvisoryDraft
from app.engine.disease import DiseaseAssessment
from app.engine.soil import SoilAssessmentResult
from app.engine.vegetation import VegetationAssessment
from app.engine.water import WaterAssessment
from app.engine.weather import WeatherAssessment
from app.providers.base import DailyObservation
from app.schemas.advisory import Advisory, AdvisoryList
from app.schemas.analysis import (
    AnalysisRun,
    AnalysisRunList,
    AnalysisRunSummary,
    FarmDashboard,
)
from app.schemas.common import DataMode, DataSourceMeta, RiskLevel, ScoreBand, ScoredFactor
from app.schemas.crop import Crop
from app.schemas.enums import (
    AdvisoryCategory,
    AdvisoryPriority,
    AIMode,
    AnalysisStatus,
    GrowthStage,
)
from app.schemas.recommendation import (
    CropRecommendation,
    CropRecommendationList,
    RegenerativeRecommendation,
    RegenerativeRecommendationList,
)
from app.schemas.risk import DiseaseRisk, DiseaseRiskItem, WaterRisk, WeatherRisk
from app.schemas.soil import SoilAssessment
from app.schemas.vegetation import CropHealth
from app.services import environment_service
from app.services.analysis_context import build_context
from app.services.environment_service import EnvironmentSnapshot
from app.services.farm_service import (
    _to_farm,
    _to_farm_crop,
    plantings_for_farm,
    primary_planting,
    require_farm,
)
from app.services.reference_service import _ensure_catalog, paginate

PROMPT_VERSION = "phase3-fixture-v1"

# Crop coefficient (Kc) by growth stage — FAO-56 shaped, used to turn ET₀ into crop
# water demand.
_KC_BY_STAGE: dict[GrowthStage, float] = {
    GrowthStage.not_planted: 0.30,
    GrowthStage.germination: 0.40,
    GrowthStage.seedling: 0.55,
    GrowthStage.vegetative: 0.85,
    GrowthStage.flowering: 1.15,
    GrowthStage.fruiting: 1.05,
    GrowthStage.maturity: 0.75,
    GrowthStage.harvested: 0.30,
}

_IRRIGATION_RELIEF = {
    "rainfed": 0.0,
    "none": 0.0,
    "flood": 0.55,
    "furrow": 0.60,
    "sprinkler": 0.75,
    "drip": 0.90,
}

_REGENERATIVE_PRACTICES = [
    {
        "code": "cover_cropping",
        "name": "Cover cropping",
        "description": "Keep living roots in the soil between cash crops using "
        "legume or grass covers.",
        "benefits": ["Builds soil organic carbon", "Reduces erosion", "Suppresses weeds"],
        "carbon": "moderate increase over 3-5 seasons",
        "water": "improves infiltration and reduces surface runoff",
        "steps": [
            "Select a cover mix suited to the fallow window",
            "Drill or broadcast immediately after harvest",
            "Terminate two to three weeks before the next planting",
        ],
        "effort": "moderate",
        "time_to_benefit": "1-2 seasons",
        "targets": {"low_carbon": 30, "erosion": 20},
    },
    {
        "code": "reduced_tillage",
        "name": "Reduced or no-till",
        "description": "Minimise soil disturbance so aggregates, fungal networks "
        "and residue cover stay intact.",
        "benefits": ["Protects soil structure", "Retains moisture", "Cuts fuel use"],
        "carbon": "slow but durable increase",
        "water": "notably higher water retention on light soils",
        "steps": [
            "Start with a single field to build confidence",
            "Adjust the planter for residue",
            "Pair with a cover crop to manage weeds",
        ],
        "effort": "high",
        "time_to_benefit": "2-4 seasons",
        "targets": {"low_carbon": 25, "compaction": 25},
    },
    {
        "code": "compost_application",
        "name": "Compost and organic amendment",
        "description": "Apply well-finished compost or manure to raise organic "
        "matter and feed soil biology.",
        "benefits": ["Raises organic carbon quickly", "Improves nutrient holding", "Buffers pH"],
        "carbon": "fast increase where application rates are sustained",
        "water": "raises available water capacity",
        "steps": [
            "Test the amendment for maturity",
            "Apply 5-10 t/ha before land preparation",
            "Incorporate shallowly to limit nitrogen loss",
        ],
        "effort": "moderate",
        "time_to_benefit": "1 season",
        "targets": {"low_carbon": 35, "low_cec": 20},
    },
    {
        "code": "crop_rotation",
        "name": "Diverse crop rotation",
        "description": "Alternate crop families across seasons, including a "
        "legume, to break pest cycles.",
        "benefits": ["Breaks disease and pest cycles", "Fixes nitrogen", "Spreads market risk"],
        "carbon": "modest increase",
        "water": "varied rooting depths improve profile use",
        "steps": [
            "Plan a three-season sequence",
            "Include at least one legume",
            "Avoid consecutive seasons of the same family",
        ],
        "effort": "low",
        "time_to_benefit": "1-2 seasons",
        "targets": {"disease": 30, "low_carbon": 10},
    },
    {
        "code": "mulching",
        "name": "Surface mulching",
        "description": "Cover bare soil with crop residue or organic mulch to cut evaporation.",
        "benefits": ["Cuts evaporative loss", "Moderates soil temperature", "Suppresses weeds"],
        "carbon": "gradual increase as mulch breaks down",
        "water": "strong reduction in evaporative loss",
        "steps": [
            "Retain residue rather than burning it",
            "Target 30% or more ground cover",
            "Top up before the dry period",
        ],
        "effort": "low",
        "time_to_benefit": "immediate",
        "targets": {"water_stress": 35, "sandy": 20},
    },
    {
        "code": "agroforestry",
        "name": "Agroforestry and windbreaks",
        "description": "Integrate trees or shrubs into field margins and alleys.",
        "benefits": ["Long-term carbon storage", "Wind protection", "Additional income"],
        "carbon": "large increase over 5-10 years",
        "water": "reduces wind-driven evaporation",
        "steps": [
            "Map margins that will not shade the crop",
            "Choose nitrogen-fixing species where possible",
            "Protect seedlings for the first two seasons",
        ],
        "effort": "high",
        "time_to_benefit": "3-5 years",
        "targets": {"wind": 35, "low_carbon": 15},
    },
    {
        "code": "liming",
        "name": "Targeted liming",
        "description": "Correct soil acidity so nutrients become available to the crop.",
        "benefits": ["Unlocks phosphorus", "Reduces aluminium toxicity", "Improves root growth"],
        "carbon": "indirect, through better biomass",
        "water": "indirect, through deeper rooting",
        "steps": [
            "Confirm pH with a soil test",
            "Apply agricultural lime by buffer requirement",
            "Re-test after two seasons",
        ],
        "effort": "low",
        "time_to_benefit": "1-2 seasons",
        "targets": {"acidity": 45},
    },
]


def _band_for(score: float) -> ScoreBand:
    if score >= 80:
        return ScoreBand.excellent
    if score >= 65:
        return ScoreBand.good
    if score >= 45:
        return ScoreBand.moderate
    if score >= 25:
        return ScoreBand.poor
    return ScoreBand.critical


def _risk_level_for(score: float) -> RiskLevel:
    if score >= 70:
        return RiskLevel.severe
    if score >= 50:
        return RiskLevel.high
    if score >= 28:
        return RiskLevel.moderate
    return RiskLevel.low


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# --------------------------------------------------------------------------
# Missing-data handling
#
# A real provider omits variables it cannot supply for a location, so any daily
# field may arrive as None. `None` means *unknown*, which is not the same as zero:
# treating an absent rainfall reading as "no rain" would invent a drought, and
# comparing an absent temperature against a threshold raises TypeError.
#
# The helpers below skip unknown observations rather than substituting a value,
# and report when a field has no readings at all so the run can be marked partial.
# --------------------------------------------------------------------------

# Daily fields the scoring functions depend on, with the label used in messages.
REQUIRED_DAILY_FIELDS: dict[str, str] = {
    "temp_max_c": "maximum temperature",
    "temp_min_c": "minimum temperature",
    "temp_mean_c": "mean temperature",
    "humidity_pct": "humidity",
    "wind_kmh": "wind speed",
    "et0_mm": "reference evapotranspiration",
    "precipitation_mm": "precipitation",
}

INSUFFICIENT = "Insufficient data"
"""Prefix every unavailable-factor explanation carries, so a caller can detect one."""


def _reading(day: DailyObservation, attr: str) -> float | None:
    """One usable numeric reading, or None.

    `None` and non-numeric junk are both "unknown": a provider that sends `"hot"`
    has told us nothing we can compare, and comparing it to a float raises exactly
    as `None` does. `bool` is excluded because it is a subclass of `int`.
    """
    value = getattr(day, attr, None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _known(days: Sequence[DailyObservation], attr: str) -> list[float]:
    """Reported values only. Days without a usable reading are excluded, never defaulted."""
    return [v for d in days if (v := _reading(d, attr)) is not None]


def _has(days: Sequence[DailyObservation], attr: str) -> bool:
    """Whether any day in the window reported a usable value for this field."""
    return any(_reading(d, attr) is not None for d in days)


def _count_where(days: Sequence[DailyObservation], attr: str, predicate) -> int:
    """Days whose reported value satisfies `predicate`.

    An unknown value never counts as a match — we cannot claim a hot day we did
    not observe. The companion `_has` check tells callers whether a zero here
    means "none occurred" or "nothing was measured".
    """
    return sum(1 for d in days if (v := _reading(d, attr)) is not None and predicate(v))


def _max_of(days: Sequence[DailyObservation], attr: str) -> float | None:
    values = _known(days, attr)
    return max(values) if values else None


def _min_of(days: Sequence[DailyObservation], attr: str) -> float | None:
    values = _known(days, attr)
    return min(values) if values else None


def _sum_of(days: Sequence[DailyObservation], attr: str) -> float | None:
    """Sum of reported values, or None when nothing was reported.

    Deliberately not `sum(...)` with its 0 default: an empty window would then
    report 0 mm of rainfall, which is a measurement we never made.
    """
    values = _known(days, attr)
    return sum(values) if values else None


def _unavailable(days: Sequence[DailyObservation], *attrs: str) -> list[str]:
    """Labels of the requested fields that no day in the window reported."""
    return [REQUIRED_DAILY_FIELDS[a] for a in attrs if not _has(days, a)]


def _unknown_factor(key: str, label: str, missing: Sequence[str]) -> ScoredFactor:
    """A factor that could not be assessed.

    The contract requires a numeric score and a band, and offers no "unknown"
    member — so `weight=0.0` carries the meaning instead. It excludes the factor
    from the weighted composite arithmetically rather than by convention, and the
    explanation states plainly what was missing.
    """
    return ScoredFactor(
        key=key,
        label=label,
        # Neutralised placeholders: the contract requires both, and weight=0.0
        # keeps them out of every derived number.
        score=0.0,
        weight=0.0,
        band=ScoreBand.moderate,
        explanation=(
            f"{INSUFFICIENT}: {', '.join(missing)} unavailable from the weather provider, "
            "so this factor was not assessed and is excluded from the overall score."
        ),
    )


# --------------------------------------------------------------------------
# Section builders
# --------------------------------------------------------------------------


def _weather_risk(
    env: EnvironmentSnapshot,
    record: FarmRecord,
    crop: Crop | None,
    stage: GrowthStage,
    planting: FarmCropRecord | None = None,
) -> WeatherRisk:
    """Near-term weather exposure, delegated to the deterministic engine.

    Thresholds now come from the crop rather than from constants, and growing degree
    days are accumulated from its base temperature. The published shape is unchanged —
    `WeatherRisk` has no field for thermal time, so it reaches the response through the
    drivers rather than by growing the contract.
    """
    context = build_context(record, env, crop, stage, planting)
    return _to_weather_risk(engine_weather.evaluate(context))


def _to_weather_risk(assessment: WeatherAssessment) -> WeatherRisk:
    """Map the engine's result onto the published schema."""
    return WeatherRisk(
        level=assessment.level,
        score=int(round(assessment.score)),
        forecast_window_days=assessment.forecast_window_days,
        heat_stress_days=assessment.heat_stress_days,
        frost_risk_days=assessment.frost_risk_days,
        heavy_rain_days=assessment.heavy_rain_days,
        high_wind_days=assessment.high_wind_days,
        longest_dry_spell_days=assessment.longest_dry_spell_days,
        # Nullable in the contract: null is how "not measured" is expressed, and it
        # is what distinguishes "no hot days" from "no temperature data".
        max_temp_c=assessment.max_temp_c,
        min_temp_c=assessment.min_temp_c,
        total_precipitation_mm=_round_or_none(assessment.total_precipitation_mm),
        drivers=list(assessment.drivers),
        factors=list(assessment.factors),
        explanation=assessment.explanation,
    )


def _water_risk(
    env: EnvironmentSnapshot, record: FarmRecord, crop: Crop | None, stage: GrowthStage
) -> WaterRisk:
    """Soil water balance, delegated to the deterministic engine.

    This function no longer computes anything. It builds a pure context, runs the
    FAO-56 daily balance in `app.engine.water`, and maps the result onto the frozen
    `WaterRisk` schema. The arithmetic lives in the engine so it can be unit-tested
    without a provider and recomputed from a stored run.
    """
    return _to_water_risk(_water_assessment(env, record, crop, stage), record)


def _water_assessment(
    env: EnvironmentSnapshot, record: FarmRecord, crop: Crop | None, stage: GrowthStage
) -> WaterAssessment:
    """The engine's own result, before it is narrowed to the published schema.

    `_build_run` needs this rather than the mapped `WaterRisk`, because the engine
    records things the contract has no field for — chiefly how many days had their
    evapotranspiration estimated rather than measured, which decides whether the run
    reports degraded provenance.
    """
    return engine_water.evaluate(build_context(record, env, crop, stage))


def _irrigation_note(record: FarmRecord, efficiency: float | None) -> str:
    if efficiency is None:
        return "Farm is rainfed; there is no irrigation buffer against a shortfall."
    return (
        f"Farm uses {record.irrigation_type} irrigation, which delivers about "
        f"{efficiency * 100:.0f}% of what is applied."
    )


def _to_water_risk(assessment: WaterAssessment, record: FarmRecord) -> WaterRisk:
    """Map the engine's result onto the published schema.

    The insufficient branch reports nulls where the contract allows them rather than
    substituting values. `water_holding_capacity_mm` in particular stays null: the
    behaviour this replaced defaulted it to 50 mm, and that invented reservoir drove a
    real irrigation recommendation for a farm whose soil was never measured.
    """
    efficiency = assessment.application_efficiency
    note = _irrigation_note(record, efficiency)

    if not assessment.sufficient:
        return WaterRisk(
            level=assessment.level,
            score=0,
            # Required by the contract and not nullable; the explanation, drivers and
            # the run's partial status carry the fact that nothing was computed.
            water_balance_mm=0.0,
            deficit_mm=0.0,
            recommended_irrigation_mm=0.0,
            # Nullable measurements stay null rather than reporting a false zero.
            total_precipitation_mm=None,
            total_crop_water_demand_mm=None,
            soil_moisture_pct=None,
            water_holding_capacity_mm=assessment.taw_mm,
            days_until_stress=None,
            irrigation_window=None,
            irrigation_efficiency_note=note,
            drivers=list(assessment.drivers),
            factors=[
                *assessment.factors,
                ScoredFactor(
                    key="irrigation_capacity",
                    label="Irrigation capacity",
                    score=_clamp((efficiency or 0.0) * 100, 0, 100),
                    weight=0.4,
                    band=_band_for(_clamp((efficiency or 0.0) * 100, 0, 100)),
                    explanation=note,
                ),
            ],
            explanation=assessment.explanation,
        )

    score = assessment.score
    return WaterRisk(
        level=assessment.level,
        score=int(round(score)),
        water_balance_mm=round(assessment.water_balance_mm, 1),
        deficit_mm=round(assessment.deficit_mm, 1),
        total_precipitation_mm=_round_or_none(assessment.total_precipitation_mm),
        total_crop_water_demand_mm=_round_or_none(assessment.total_crop_demand_mm),
        soil_moisture_pct=_round_or_none(assessment.soil_moisture_pct),
        water_holding_capacity_mm=_round_or_none(assessment.taw_mm),
        days_until_stress=assessment.days_until_stress,
        recommended_irrigation_mm=round(assessment.applied_irrigation_mm, 1),
        irrigation_window=(
            "within 48 hours" if score >= 50 else "within the next week" if score >= 28 else None
        ),
        irrigation_efficiency_note=note,
        drivers=list(assessment.drivers),
        factors=list(assessment.factors),
        explanation=assessment.explanation,
    )


def _round_or_none(value: float | None, places: int = 1) -> float | None:
    return None if value is None else round(value, places)


def _disease_risk(
    env: EnvironmentSnapshot, record: FarmRecord, crop: Crop | None, stage: GrowthStage
) -> DiseaseRisk:
    """Disease pressure, delegated to the deterministic engine.

    Rules are matched against the hourly series rather than daily means, and a pathogen
    is named only when a rule for the planted crop met its duration requirement. The
    published shape is unchanged.
    """
    context = build_context(record, env, crop, stage)
    return _to_disease_risk(engine_disease.evaluate(context))


def _to_disease_risk(assessment: DiseaseAssessment) -> DiseaseRisk:
    """Map the engine's result onto the published schema."""
    return DiseaseRisk(
        level=assessment.level,
        score=int(round(assessment.score)),
        conditions_summary=assessment.conditions_summary,
        risks=[
            DiseaseRiskItem(
                name=item.name,
                pathogen=item.pathogen,
                crop_code=item.crop_code,
                level=item.level,
                probability=round(item.probability, 2),
                triggering_conditions=list(item.triggering_conditions),
                preventive_actions=list(item.preventive_actions),
                scouting_advice=item.scouting_advice,
            )
            for item in assessment.items
        ],
        factors=list(assessment.factors),
        explanation=assessment.explanation,
    )


def _soil_assessment(
    env: EnvironmentSnapshot, record: FarmRecord, crop: Crop | None
) -> SoilAssessment:
    """Soil suitability, delegated to the deterministic engine.

    Scoring now tolerates a partial profile: a real survey answers for the properties it
    sampled, and the engine reports the rest as unassessed rather than reading a value
    that is not there.
    """
    context = build_context(record, env, crop, GrowthStage.not_planted)
    return _to_soil_assessment(engine_soil.evaluate(context), env)


def _to_soil_assessment(result: SoilAssessmentResult, env: EnvironmentSnapshot) -> SoilAssessment:
    """Map the engine's result onto the published schema.

    The explanation names the provenance rather than asserting the soil is simulated:
    the same code path now serves a SoilGrids prediction and a local simulation, and
    only `meta.mode` knows which.
    """
    texture = env.soil.texture_class
    return SoilAssessment(
        score=int(round(result.score)),
        band=result.band,
        texture_class=texture,
        ph_status=result.ph_status,
        organic_matter_status=result.organic_matter_status,
        fertility_status=result.fertility_status,
        limitations=list(result.limitations),
        factors=list(result.factors),
        explanation=result.explanation,
    )


def _crop_health(
    env: EnvironmentSnapshot,
    record: FarmRecord,
    crop: Crop | None,
    planting,
    stage: GrowthStage,
) -> CropHealth:
    """Canopy vigour and crop development, delegated to the deterministic engine.

    The vegetation index remains simulated, and its provenance is reported through
    `env.vegetation_meta` exactly as before — this changes where the arithmetic lives,
    not what the numbers are made of.
    """
    context = build_context(record, env, crop, stage, planting)
    return _to_crop_health(engine_vegetation.evaluate(context))


def _to_crop_health(assessment: VegetationAssessment) -> CropHealth:
    """Map the engine's result onto the published schema."""
    return CropHealth(
        score=int(round(assessment.score)),
        band=assessment.band,
        current_ndvi=assessment.current_ndvi,
        ndvi_trend=assessment.trend,
        growth_stage=assessment.growth_stage,
        days_since_planting=assessment.days_since_planting,
        days_to_expected_harvest=assessment.days_to_expected_harvest,
        gdd_accumulated=assessment.growing_degree_days,
        gdd_required=assessment.gdd_required,
        stress_indicators=list(assessment.stress_indicators),
        factors=list(assessment.factors),
        explanation=assessment.explanation,
    )


def _advisories(
    farm_id: UUID,
    run_id: UUID,
    created_at: datetime,
    drafts: Sequence[AdvisoryDraft],
) -> list[Advisory]:
    """Attach identity and time to the engine's advisory drafts.

    The drafts are a pure function of the five assessments — every claim in them cites
    a threshold an engine applied or a rule that matched. This function adds the two
    things a pure engine must not invent: a UUID and a timestamp.

    `ai_mode` on the run is `mock`, so none of this text is model output.
    """
    return [
        Advisory(
            id=uuid4(),
            farm_id=farm_id,
            analysis_run_id=run_id,
            category=draft.category,
            priority=draft.priority,
            title=draft.title,
            body=draft.body,
            rationale=draft.rationale,
            action_window=draft.action_window,
            confidence=draft.confidence,
            created_at=created_at,
            dismissed_at=None,
        )
        for draft in drafts
    ]


def _crop_recommendations(env: EnvironmentSnapshot, current_code: str | None, limit: int = 5):
    _ensure_catalog()
    soil = env.soil

    # Use every past day the snapshot holds, then annualise. The window is bounded by
    # what the weather provider can supply, so scaling by its actual length keeps the
    # figure comparable regardless of provider.
    history = env.history(env.available_history_days) or env.daily
    temps = _known(history, "temp_mean_c")
    mean_temp = sum(temps) / len(temps) if temps else None

    # Annualise only the days that actually reported rainfall. Days with no reading
    # are excluded from both numerator and denominator rather than counted as dry,
    # which would bias every recommendation toward drought-tolerant crops.
    rain_days = _known(history, "precipitation_mm")
    seasonal_rain = (sum(rain_days) * (365 / len(rain_days))) if rain_days else None

    scored: list[tuple[float, Crop, list[str], list[str], list[ScoredFactor]]] = []
    for crop in store.crops.values():
        strengths: list[str] = []
        considerations: list[str] = []

        if crop.ph_min is not None and crop.ph_max is not None:
            if crop.ph_min <= soil.ph <= crop.ph_max:
                ph_score = 95.0
                strengths.append(f"Tolerates the farm's pH of {soil.ph}")
            else:
                distance = min(abs(soil.ph - crop.ph_min), abs(soil.ph - crop.ph_max))
                ph_score = _clamp(95 - distance * 30, 5, 95)
                considerations.append(
                    f"Prefers pH {crop.ph_min}-{crop.ph_max}; farm reads {soil.ph}"
                )
        else:
            ph_score = 70.0

        if (
            crop.optimal_temp_min_c is not None
            and crop.optimal_temp_max_c is not None
            and mean_temp is not None
        ):
            if crop.optimal_temp_min_c <= mean_temp <= crop.optimal_temp_max_c:
                temp_score = 95.0
                strengths.append(f"Mean temperature of {mean_temp:.0f} °C is in its optimal band")
            else:
                distance = min(
                    abs(mean_temp - crop.optimal_temp_min_c),
                    abs(mean_temp - crop.optimal_temp_max_c),
                )
                temp_score = _clamp(95 - distance * 6, 5, 95)
                considerations.append(
                    f"Optimal range is {crop.optimal_temp_min_c:.0f}-"
                    f"{crop.optimal_temp_max_c:.0f} °C"
                )
        else:
            temp_score = 70.0

        if crop.preferred_textures:
            if soil.texture_class.value in crop.preferred_textures:
                texture_score = 92.0
                strengths.append(f"Suits {soil.texture_class.value} soils")
            else:
                texture_score = 52.0
                considerations.append(f"Prefers {', '.join(crop.preferred_textures[:2])}")
        else:
            texture_score = 70.0

        if crop.water_need_mm_season and seasonal_rain is not None:
            ratio = seasonal_rain / crop.water_need_mm_season
            water_score = _clamp(100 - abs(1 - ratio) * 55, 5, 100)
            if ratio < 0.7:
                considerations.append(
                    f"Needs about {crop.water_need_mm_season:.0f} mm/season; "
                    f"observed rainfall is around {seasonal_rain:.0f} mm"
                )
            elif ratio >= 1.0:
                strengths.append("Simulated rainfall covers its seasonal water requirement")
        else:
            water_score = 70.0

        composite = ph_score * 0.3 + temp_score * 0.3 + texture_score * 0.2 + water_score * 0.2

        factors = [
            ScoredFactor(
                key="ph_match",
                label="pH match",
                score=round(ph_score, 1),
                weight=0.3,
                band=_band_for(ph_score),
                explanation=f"Soil pH {soil.ph}.",
            ),
            ScoredFactor(
                key="temperature_match",
                label="Temperature match",
                score=round(temp_score, 1),
                weight=0.3,
                band=_band_for(temp_score),
                explanation=(
                    f"Mean temperature {mean_temp:.0f} °C."
                    if mean_temp is not None
                    else f"{INSUFFICIENT}: mean temperature unavailable."
                ),
            ),
            ScoredFactor(
                key="texture_match",
                label="Texture match",
                score=round(texture_score, 1),
                weight=0.2,
                band=_band_for(texture_score),
                explanation=f"{soil.texture_class.value} soil.",
            ),
            ScoredFactor(
                key="water_match",
                label="Water availability",
                score=round(water_score, 1),
                weight=0.2,
                band=_band_for(water_score),
                explanation=(
                    f"Seasonal rainfall around {seasonal_rain:.0f} mm."
                    if seasonal_rain is not None
                    else f"{INSUFFICIENT}: rainfall unavailable."
                ),
            ),
        ]
        scored.append((composite, crop, strengths, considerations, factors))

    # Sort by score, then code, so ties never reorder between identical requests.
    scored.sort(key=lambda entry: (-entry[0], entry[1].code))

    return [
        CropRecommendation(
            crop_code=crop.code,
            crop_name=crop.name,
            category=crop.category,
            season=crop.season,
            suitability_score=int(round(score)),
            rank=index,
            is_current_crop=crop.code == current_code,
            water_requirement_mm=crop.water_need_mm_season,
            expected_yield_note=None,
            planting_window=f"{crop.season.value.replace('_', ' ').title()} window",
            strengths=strengths or ["No standout advantages at this site"],
            considerations=considerations or ["No significant constraints identified"],
            factors=factors,
            rationale=(
                f"{crop.name} scores {int(round(score))}/100 against this farm's simulated "
                f"soil and climate."
            ),
        )
        for index, (score, crop, strengths, considerations, factors) in enumerate(scored[:limit], 1)
    ]


def _regenerative_recommendations(
    env: EnvironmentSnapshot,
    record: FarmRecord,
    soil_assessment: SoilAssessment,
    water: WaterRisk,
    disease: DiseaseRisk,
    weather: WeatherRisk,
    limit: int = 5,
):
    soil = env.soil

    signals = {
        "low_carbon": 100 if soil.organic_carbon_pct < 1.5 else 40,
        "erosion": 100 if soil.sand_pct > 55 else 35,
        "compaction": 100 if soil.bulk_density_kg_dm3 > 1.5 else 30,
        "low_cec": 100 if soil.cec_cmol_kg < 12 else 30,
        "disease": min(100, disease.score + 20),
        "water_stress": min(100, water.score + 20),
        "sandy": 100 if soil.sand_pct > 55 else 25,
        "wind": min(100, weather.high_wind_days * 30 + 20),
        "acidity": 100 if soil.ph < 5.5 else 20,
    }

    scored = []
    for practice in _REGENERATIVE_PRACTICES:
        targets: dict[str, int] = practice["targets"]  # type: ignore[assignment]
        relevance = sum(signals.get(key, 0) * weight for key, weight in targets.items())
        relevance = _clamp(relevance / max(sum(targets.values()), 1), 5, 100)
        if record.farming_practice == "regenerative":
            relevance = _clamp(relevance - 10, 5, 100)
        scored.append((relevance, practice))

    scored.sort(key=lambda entry: (-entry[0], entry[1]["code"]))

    return [
        RegenerativeRecommendation(
            practice_code=practice["code"],
            practice_name=practice["name"],
            rank=index,
            relevance_score=int(round(relevance)),
            description=practice["description"],
            expected_benefits=practice["benefits"],
            soil_carbon_impact=practice["carbon"],
            water_retention_impact=practice["water"],
            implementation_steps=practice["steps"],
            effort_level=practice["effort"],
            time_to_benefit=practice["time_to_benefit"],
            considerations=soil_assessment.limitations[:2] or ["No blocking soil constraints"],
            rationale=(
                f"{practice['name']} scores {int(round(relevance))}/100 for this farm given "
                f"{soil.organic_carbon_pct}% organic carbon, {soil.texture_class.value} texture "
                f"and a water risk of {water.score}/100."
            ),
        )
        for index, (relevance, practice) in enumerate(scored[:limit], 1)
    ]


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _provenance_sentence(env: EnvironmentSnapshot) -> str:
    """State plainly what the figures rest on, so the headline can never imply the
    inputs were measured when they were generated (or the reverse)."""
    weather_mode = env.weather_meta.mode
    if weather_mode in (DataMode.live, DataMode.cached):
        return (
            f"Weather is real data from {env.weather_meta.source}; "
            "soil and vegetation figures are simulated."
        )
    if weather_mode is DataMode.unavailable:
        return "Weather data was unavailable; figures below are incomplete."
    return "All figures are simulated, not live measurements."


def _build_run(record: FarmRecord, env: EnvironmentSnapshot) -> AnalysisRun:
    """Score one farm from a single environmental snapshot.

    Every section below reads `env`. Nothing regenerates weather, soil or vegetation,
    so the analysis and the environment endpoints are guaranteed to describe the same
    conditions — and the run's provenance is exactly the snapshot's provenance.
    """
    started = datetime.now(UTC)

    planting = primary_planting(record.id)
    crop = None
    stage = GrowthStage.not_planted
    if planting is not None:
        from app.services.reference_service import get_crop

        crop = get_crop(planting.crop_id)
        stage = GrowthStage(planting.growth_stage)

    # One context, five engines. The per-section helpers above build their own context
    # and are the seam a single-section caller would use; the run needs the engines'
    # own results rather than the narrowed schemas, because the composite and the
    # advisories read evidence the published shapes have no field for.
    context = build_context(record, env, crop, stage, planting)

    weather_assessment = engine_weather.evaluate(context)
    water_assessment = engine_water.evaluate(context)
    disease_assessment = engine_disease.evaluate(context)
    soil_result = engine_soil.evaluate(context)
    vegetation_result = engine_vegetation.evaluate(context)

    weather = _to_weather_risk(weather_assessment)
    water = _to_water_risk(water_assessment, record)
    disease = _to_disease_risk(disease_assessment)
    soil_assessment = _to_soil_assessment(soil_result, env)
    crop_health = _to_crop_health(vegetation_result)

    overall_assessment = engine_composite.evaluate(
        context,
        weather_assessment,
        water_assessment,
        disease_assessment,
        soil_result,
        vegetation_result,
    )
    factors = list(overall_assessment.factors)
    overall = overall_assessment.score
    unassessed = list(overall_assessment.unassessed)

    # An estimated input is degradation, not absence. When the provider supplied no
    # reference evapotranspiration, the engine falls back to Hargreaves from the day's
    # temperature range — FAO-56's own method for exactly that gap — so the factor is
    # still assessed and the run is still complete. But the value was derived rather
    # than measured, and a payload must never let that pass unlabelled.
    estimated_inputs = water_assessment.estimated_et0_days > 0

    degraded = list(
        dict.fromkeys(
            [
                *env.degraded_sources,
                *([env.weather_meta.source] if unassessed or estimated_inputs else []),
            ]
        )
    )
    status = AnalysisStatus.partial if unassessed else AnalysisStatus.complete

    run_id = uuid4()

    advisories = _advisories(
        record.id,
        run_id,
        started,
        engine_composite.derive_advisories(
            context,
            weather_assessment,
            water_assessment,
            disease_assessment,
            soil_result,
            overall_assessment,
        ),
    )

    crop_recs = _crop_recommendations(env, crop.code if crop else None)
    regen_recs = _regenerative_recommendations(
        env, record, soil_assessment, water, disease, weather
    )

    crop_label = crop.name if crop else "no registered crop"
    summary = (
        f"{record.name} scores {int(round(overall))}/100 ({_band_for(overall).value}) with "
        f"{crop_label}. Water risk is {water.level.value}, disease pressure is "
        f"{disease.level.value} and weather risk is {weather.level.value}. "
        f"{len(advisories)} advisory item(s) require attention. "
        + (f"{INSUFFICIENT} for: {', '.join(unassessed)}. " if unassessed else "")
        + f"{_provenance_sentence(env)}"
    )

    finished = datetime.now(UTC)

    # Provenance is the snapshot's provenance verbatim. The run cannot claim its
    # inputs were live when they were simulated, or the reverse.
    sources: list[DataSourceMeta] = list(env.sources)

    return AnalysisRun(
        id=run_id,
        farm_id=record.id,
        status=status,
        created_at=started,
        duration_ms=max(1, int((finished - started).total_seconds() * 1000)),
        model=None,
        prompt_version=PROMPT_VERSION,
        ai_mode=AIMode.mock,
        degraded_sources=degraded,
        overall_health_score=int(round(overall)),
        overall_band=_band_for(overall),
        summary=summary,
        factors=factors,
        weather_risk=weather,
        water_risk=water,
        disease_risk=disease,
        crop_health=crop_health,
        soil_assessment=soil_assessment,
        advisories=advisories,
        crop_recommendations=crop_recs,
        regenerative_recommendations=regen_recs,
        sources=sources,
    )


async def run_analysis(
    farm_id: UUID, *, user: CurrentUser, force_refresh: bool = False
) -> AnalysisRun:
    record = require_farm(farm_id, user)

    if not force_refresh:
        existing = store.latest_run(farm_id)
        if existing is not None:
            return existing

    env = await environment_service.gather_environment(record)
    run = _build_run(record, env)
    with store.lock:
        store.analysis_runs[run.id] = run
    return run


def latest_analysis(farm_id: UUID, user: CurrentUser) -> AnalysisRun:
    require_farm(farm_id, user)
    run = store.latest_run(farm_id)
    if run is None:
        raise NoAnalysisYetError(
            "No analysis has been run for this farm yet. POST to "
            f"/api/v1/farms/{farm_id}/analysis to create one.",
            details={"farm_id": str(farm_id)},
        )
    return run


def list_runs(farm_id: UUID, *, page: int, page_size: int, user: CurrentUser) -> AnalysisRunList:
    require_farm(farm_id, user)
    summaries = [
        AnalysisRunSummary(
            id=run.id,
            farm_id=run.farm_id,
            status=run.status,
            created_at=run.created_at,
            duration_ms=run.duration_ms,
            overall_health_score=run.overall_health_score,
            overall_band=run.overall_band,
            ai_mode=run.ai_mode,
            degraded_sources=run.degraded_sources,
        )
        for run in store.runs_for_farm(farm_id)
    ]
    return paginate(AnalysisRunList, summaries, page, page_size)


def get_run(run_id: UUID, user: CurrentUser) -> AnalysisRun:
    """A stored run, scoped to its farm's owner.

    The run is addressed by its own id, so ownership has to be resolved through the
    farm it belongs to. `require_farm` raises `FARM_NOT_FOUND` for someone else's farm,
    which is deliberately indistinguishable from a run that does not exist.
    """
    run = store.analysis_runs.get(run_id)
    if run is None:
        raise NoAnalysisYetError(
            f"Analysis run {run_id} does not exist.", details={"run_id": str(run_id)}
        )
    require_farm(UUID(str(run.farm_id)), user)
    return run


async def dashboard(farm_id: UUID, user: CurrentUser) -> FarmDashboard:
    """Never 404s on a missing analysis — returns `has_analysis: false` instead, so
    a newly registered farm renders an empty state rather than an error."""
    record = require_farm(farm_id, user)
    run = store.latest_run(farm_id)

    env = await environment_service.gather_environment(record)
    weather = environment_service.to_weather_bundle(env, forecast_days=7, history_days=30)

    return FarmDashboard(
        farm=_to_farm(record),
        # Through `farm_service` rather than the store directly, so the dashboard
        # shows persisted plantings when a database is configured.
        crops=[_to_farm_crop(c) for c in plantings_for_farm(farm_id)],
        has_analysis=run is not None,
        analysis=run,
        current_weather=weather.current,
        recent_images=store.images_for_farm(farm_id)[:5],
        # The dashboard reports the same provenance as the snapshot it was built
        # from, per panel, so a badge can be rendered accurately for each.
        data_freshness=list(env.sources),
    )


# ---- projections of the latest run ----


def weather_risk(farm_id: UUID, user: CurrentUser) -> WeatherRisk:
    return latest_analysis(farm_id, user).weather_risk


def water_risk(farm_id: UUID, user: CurrentUser) -> WaterRisk:
    return latest_analysis(farm_id, user).water_risk


def disease_risk(farm_id: UUID, user: CurrentUser) -> DiseaseRisk:
    return latest_analysis(farm_id, user).disease_risk


def crop_health(farm_id: UUID, user: CurrentUser) -> CropHealth:
    return latest_analysis(farm_id, user).crop_health


def advisories(
    farm_id: UUID,
    *,
    category: AdvisoryCategory | None,
    priority: AdvisoryPriority | None,
    include_dismissed: bool,
    page: int,
    page_size: int,
    user: CurrentUser,
) -> AdvisoryList:
    items = list(latest_analysis(farm_id, user).advisories)
    if category is not None:
        items = [a for a in items if a.category == category]
    if priority is not None:
        items = [a for a in items if a.priority == priority]
    if not include_dismissed:
        items = [a for a in items if a.dismissed_at is None]
    return paginate(AdvisoryList, items, page, page_size)


def crop_recommendations(farm_id: UUID, *, limit: int, user: CurrentUser) -> CropRecommendationList:
    items = latest_analysis(farm_id, user).crop_recommendations[:limit]
    return paginate(CropRecommendationList, items, 1, max(limit, 1))


def regenerative_recommendations(
    farm_id: UUID, *, limit: int, user: CurrentUser
) -> RegenerativeRecommendationList:
    items = latest_analysis(farm_id, user).regenerative_recommendations[:limit]
    return paginate(RegenerativeRecommendationList, items, 1, max(limit, 1))
