"""Deterministic analysis fixtures.

**This is not the risk engine.** The real engine described in `docs/ARCHITECTURE.md`
runs on live provider data and replaces the scoring here. What this module does is
derive plausible, *internally consistent* fixtures from the simulated environment, so
that a hot dry site really does show a water deficit and a humid warm site really does
show elevated disease pressure. A frontend built against these will not need reworking
when the real engine lands, because only the numbers change — never the shapes.

Two invariants hold throughout:

* every input is listed in `sources` with `mode="simulated"`;
* the numbers — scores, bands, advisories, recommendations — are always this
  module's own deterministic output. When `settings.AI_PROVIDER == "gemini"`,
  `app.ai.gemini.generate_narrative` is asked to *narrate* `summary` from those
  already-computed numbers (`ai_mode="gemini"`); it never recomputes them, and if
  it is unavailable the deterministic `summary` is used as-is (`ai_mode="fallback"`).
  With the default `settings.AI_PROVIDER == "mock"`, no model is called and every
  payload records `ai_mode="mock"`, exactly as before.

Determinism: a run's content is a pure function of the farm's coordinates, its primary
crop and the current date. Re-running without `force_refresh` returns the *same stored
run*; with `force_refresh` a new run id is minted whose scores are identical.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.ai import gemini
from app.core.config import settings
from app.core.deps import CurrentUser
from app.core.errors import NoAnalysisYetError
from app.db import analysis_repo
from app.db.memory import FarmCropRecord, FarmRecord, RunMetadata, store
from app.db.session import database_enabled
from app.engine import composite as engine_composite
from app.engine import context as engine_context
from app.engine import disease as engine_disease
from app.engine import recommendations as engine_recommendations
from app.engine import soil as engine_soil
from app.engine import vegetation as engine_vegetation
from app.engine import water as engine_water
from app.engine import weather as engine_weather
from app.engine.composite import AdvisoryDraft
from app.engine.disease import DiseaseAssessment
from app.engine.recommendations import CropSuggestion, PracticeSuggestion
from app.engine.soil import SoilAssessmentResult
from app.engine.vegetation import VegetationAssessment
from app.engine.water import WaterAssessment
from app.engine.weather import WeatherAssessment
from app.schemas.advisory import Advisory, AdvisoryList
from app.schemas.analysis import (
    AnalysisRun,
    AnalysisRunList,
    AnalysisRunSummary,
    FarmDashboard,
)
from app.schemas.common import DataMode, DataSourceMeta, ScoreBand, ScoredFactor
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
from app.services import environment_service, image_service
from app.services.analysis_context import build_context
from app.services.environment_service import EnvironmentSnapshot
from app.services.farm_service import (
    _to_farm,
    _to_farm_crop,
    plantings_for_farm,
    primary_planting,
    require_farm,
)
from app.services.reference_service import paginate

PROMPT_VERSION = "phase3-fixture-v1"


# Crop coefficient (Kc) by growth stage — FAO-56 shaped, used to turn ET₀ into crop
# water demand.
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


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


INSUFFICIENT = "Insufficient data"
"""Prefix every unavailable-factor explanation carries, so a caller can detect one.

The engines own this constant now (`app.engine.scoring`); the copy here is what the
run summary and the irrigation note still cite while they remain service-layer prose.
"""


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


#: What "no limit" means when ranking during an analysis.
#:
#: The engine ranks every candidate and slices last, so an unreachable ceiling stores the
#: complete ordering without changing a single score or rank. It is a sentinel rather
#: than `None` because the engine's signature takes an `int`, and this module has no
#: business widening the engine's contract to express "all of them".
RANK_EVERYTHING = 1_000_000


def _crop_recommendations(
    env: EnvironmentSnapshot,
    record: FarmRecord,
    crop: Crop | None,
    stage: GrowthStage,
    limit: int = RANK_EVERYTHING,
) -> list[CropRecommendation]:
    """Crop suitability, delegated to the deterministic engine.

    **The whole ranking is stored, not the first five.** The endpoints are projections of
    the stored run, so a ranking truncated here is a ceiling the API can never see past:
    the contract advertises `limit` up to 25 while an analysis had committed to 5, and a
    client asking for 10 got 5 with nothing to say why.

    Storing every ranked crop costs payload size and buys contract conformance plus an
    honest `total`. Ordering is unaffected — the engine sorts before it slices.
    """
    context = build_context(record, env, crop, stage)
    return [
        _to_crop_recommendation(suggestion)
        for suggestion in engine_recommendations.crop_recommendations(context, limit=limit)
    ]


def _to_crop_recommendation(suggestion: CropSuggestion) -> CropRecommendation:
    """Map one ranked crop onto the published schema."""
    return CropRecommendation(
        crop_code=suggestion.code,
        crop_name=suggestion.name,
        category=suggestion.category,
        season=suggestion.season,
        suitability_score=int(round(suggestion.score)),
        rank=suggestion.rank,
        is_current_crop=suggestion.is_current_crop,
        water_requirement_mm=suggestion.water_requirement_mm,
        # Null, and deliberately so. The crop catalog carries fifteen agronomic fields
        # and not one of them is a yield: no t/ha, no yield potential, no historical
        # record. Anything written here would have to be derived from `gdd_to_maturity`
        # or `water_need_mm_season`, neither of which determines yield — that depends on
        # variety, management, fertility and season, none of which TerraNex holds.
        #
        # A plausible-looking figure is worse than an absent one, because a farmer would
        # plan against it. It stays null until the catalog carries real yield data.
        expected_yield_note=None,
        planting_window=suggestion.planting_window,
        strengths=list(suggestion.strengths),
        considerations=list(suggestion.considerations),
        factors=list(suggestion.factors),
        rationale=suggestion.rationale,
    )


def _regenerative_recommendations(
    env: EnvironmentSnapshot,
    record: FarmRecord,
    crop: Crop | None,
    stage: GrowthStage,
    soil: SoilAssessmentResult,
    water: WaterAssessment,
    disease: DiseaseAssessment,
    weather: WeatherAssessment,
    limit: int = RANK_EVERYTHING,
) -> list[RegenerativeRecommendation]:
    """Regenerative practices, delegated to the deterministic engine.

    Stores the full ranking for the same reason as `_crop_recommendations`.
    """
    context = build_context(record, env, crop, stage)
    return [
        _to_regenerative_recommendation(suggestion)
        for suggestion in engine_recommendations.regenerative_recommendations(
            context, soil, water, disease, weather, limit=limit
        )
    ]


def _to_regenerative_recommendation(
    suggestion: PracticeSuggestion,
) -> RegenerativeRecommendation:
    """Map one ranked practice onto the published schema."""
    return RegenerativeRecommendation(
        practice_code=suggestion.code,
        practice_name=suggestion.name,
        rank=suggestion.rank,
        relevance_score=int(round(suggestion.relevance)),
        description=suggestion.description,
        expected_benefits=list(suggestion.benefits),
        soil_carbon_impact=suggestion.carbon_impact,
        water_retention_impact=suggestion.water_impact,
        implementation_steps=list(suggestion.steps),
        effort_level=suggestion.effort,
        time_to_benefit=suggestion.time_to_benefit,
        considerations=list(suggestion.considerations),
        rationale=suggestion.rationale,
    )


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


def _to_analysis_facts(
    *,
    record: FarmRecord,
    crop: Crop | None,
    overall: float,
    weather: WeatherRisk,
    water: WaterRisk,
    disease: DiseaseRisk,
    crop_health: CropHealth,
    soil_assessment: SoilAssessment,
    advisories: list[Advisory],
    degraded: list[str],
    env: EnvironmentSnapshot,
) -> gemini.AnalysisFacts:
    """Reduce this run's already-computed numbers to the narrow shape Gemini is
    allowed to see. Every value here is copied, never derived further — the
    narrative provider gets no field it could use to compute a number of its own.
    """
    simulated_sources = [s.source for s in env.sources if s.mode == DataMode.simulated]

    return gemini.AnalysisFacts(
        farm_name=record.name,
        crop_name=crop.name if crop else None,
        overall_score=int(round(overall)),
        overall_band=_band_for(overall).value,
        weather=gemini.RiskFact(
            level=weather.level.value,
            score=weather.score,
            explanation=weather.explanation,
            drivers=tuple(weather.drivers),
        ),
        water=gemini.RiskFact(
            level=water.level.value,
            score=water.score,
            explanation=water.explanation,
            drivers=tuple(water.drivers),
        ),
        disease=gemini.RiskFact(
            level=disease.level.value,
            score=disease.score,
            explanation=disease.explanation,
        ),
        crop_health_score=crop_health.score,
        crop_health_band=crop_health.band.value,
        crop_health_explanation=crop_health.explanation,
        soil_band=soil_assessment.band.value,
        soil_explanation=soil_assessment.explanation,
        advisories=tuple(
            gemini.AdvisoryFact(
                title=advisory.title,
                body=advisory.body,
                rationale=advisory.rationale,
                priority=advisory.priority.value,
            )
            for advisory in advisories
        ),
        degraded_sources=tuple(degraded),
        simulated_sources=tuple(simulated_sources),
    )


async def _build_run(
    record: FarmRecord,
    env: EnvironmentSnapshot,
    context: engine_context.AnalysisContext,
    crop: Crop | None,
    stage: GrowthStage,
) -> AnalysisRun:
    """Score one farm from a single environmental snapshot.

    Every section below reads `env`. Nothing regenerates weather, soil or vegetation,
    so the analysis and the environment endpoints are guaranteed to describe the same
    conditions — and the run's provenance is exactly the snapshot's provenance.

    **The context is passed in, never rebuilt here.** `run_analysis` derives the cache
    key from it, so a context built a second time in this function would be a second
    question — and any disagreement between the two would store a run under a digest
    that does not describe it. One context, one digest, one run.

    One context, five engines. The per-section helpers above build their own context
    and are the seam a single-section caller would use; the run needs the engines' own
    results rather than the narrowed schemas, because the composite and the advisories
    read evidence the published shapes have no field for.
    """
    started = datetime.now(UTC)

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

    crop_recs = _crop_recommendations(env, record, crop, stage)
    regen_recs = _regenerative_recommendations(
        env,
        record,
        crop,
        stage,
        soil_result,
        water_assessment,
        disease_assessment,
        weather_assessment,
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

    # Gemini, when configured, only ever narrates the numbers already computed
    # above — it cannot see the engines, and nothing it returns feeds back into
    # `overall`, `weather`, `water`, `disease`, `crop_health`, `soil_assessment` or
    # `advisories`. On any failure the deterministic `summary` is kept as-is.
    ai_mode = AIMode.mock
    ai_model: str | None = None
    if settings.AI_PROVIDER == "gemini":
        facts = _to_analysis_facts(
            record=record,
            crop=crop,
            overall=overall,
            weather=weather,
            water=water,
            disease=disease,
            crop_health=crop_health,
            soil_assessment=soil_assessment,
            advisories=advisories,
            degraded=degraded,
            env=env,
        )
        narrative = await gemini.generate_narrative(facts)
        if narrative.ok and narrative.data:
            summary = narrative.data
            ai_mode = AIMode.gemini
            ai_model = settings.GEMINI_MODEL
        else:
            ai_mode = AIMode.fallback

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
        model=ai_model,
        prompt_version=PROMPT_VERSION,
        ai_mode=ai_mode,
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


def _latest(farm_id: UUID, user: CurrentUser) -> AnalysisRun | None:
    """The newest stored run for a farm, from whichever storage is configured."""
    if _persisted():
        return analysis_repo.latest_run(farm_id, user.id)
    return store.latest_run(farm_id)


def _runs_for_farm(farm_id: UUID, user: CurrentUser) -> list[AnalysisRun]:
    """Every stored run for a farm, newest first."""
    if _persisted():
        return analysis_repo.runs_for_farm(farm_id, user.id)
    return store.runs_for_farm(farm_id)


def _persisted() -> bool:
    """Whether analysis runs live in the database on this deployment."""
    return database_enabled()


def _store_run(
    run: AnalysisRun, context: engine_context.AnalysisContext, user: CurrentUser
) -> None:
    """Write one run, with its reproducibility metadata, to the configured storage."""
    inputs_hash = engine_context.inputs_hash(context)

    if _persisted():
        analysis_repo.insert_run(
            run,
            inputs_hash=inputs_hash,
            engine_version=context.engine_version,
            ruleset_version=context.ruleset_version,
            user_id=user.id,
        )
        return

    store.record_run(
        run,
        RunMetadata(
            inputs_hash=inputs_hash,
            engine_version=context.engine_version,
            ruleset_version=context.ruleset_version,
        ),
    )


def _cached_run(
    farm_id: UUID, context: engine_context.AnalysisContext, user: CurrentUser
) -> AnalysisRun | None:
    """A previous run of this exact analysis, if one is still usable.

    Both storage paths answer the same question, clause for clause: same farm, same
    inputs — provenance included — same engine and ruleset, recent enough to still
    describe the weather. The offline path used to return the most recent run for the
    farm regardless of inputs, so changing a farm's crop and re-analysing returned the
    old crop's score until someone passed `force_refresh`.

    Scoped to the farm even though `inputs_hash` deliberately is not: the payload
    carries `farm_id` on the run and on every advisory, so another farm's run is the
    wrong data however identical its inputs were.
    """
    inputs_hash = engine_context.inputs_hash(context)
    not_before = analysis_repo.cache_cutoff(datetime.now(UTC), settings.ANALYSIS_CACHE_TTL_S)

    if not _persisted():
        return store.find_cached_run(
            farm_id,
            inputs_hash,
            engine_version=context.engine_version,
            ruleset_version=context.ruleset_version,
            not_before=not_before,
        )

    return analysis_repo.find_cached_run(
        farm_id,
        inputs_hash,
        engine_version=context.engine_version,
        ruleset_version=context.ruleset_version,
        not_before=not_before,
        user_id=user.id,
    )


async def run_analysis(
    farm_id: UUID, *, user: CurrentUser, force_refresh: bool = False
) -> AnalysisRun:
    """Analyse a farm, reusing a stored result when the same question was already asked.

    The environment is gathered before the cache is consulted, which looks wasteful and
    is not: the cache key *is* the environment — every observation and its provenance —
    so there is nothing to look up until it has been fetched. What the hit saves is the
    engine work and, now that the narrative layer is wired in, a Gemini call.
    """
    record = require_farm(farm_id, user)

    env = await environment_service.gather_environment(record)

    # Resolved once, here, and handed to `_build_run` — the digest below and the run it
    # labels must be computed from the same context. `stage` is read off the planting
    # whether or not the crop resolves, because the planting is what states the stage;
    # a crop missing from the catalog costs the run its coefficients, not its calendar.
    planting = primary_planting(record.id)
    crop = None
    stage = GrowthStage.not_planted
    if planting is not None:
        from app.services.reference_service import get_crop

        crop = get_crop(planting.crop_id)
        stage = GrowthStage(planting.growth_stage)

    context = build_context(record, env, crop, stage, planting)

    if not force_refresh:
        existing = _cached_run(farm_id, context, user)
        if existing is not None:
            return existing

    run = await _build_run(record, env, context, crop, stage)
    _store_run(run, context, user)
    return run


def latest_analysis(farm_id: UUID, user: CurrentUser) -> AnalysisRun:
    require_farm(farm_id, user)
    run = _latest(farm_id, user)
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
        for run in _runs_for_farm(farm_id, user)
    ]
    return paginate(AnalysisRunList, summaries, page, page_size)


def get_run(run_id: UUID, user: CurrentUser) -> AnalysisRun:
    """A stored run, scoped to its farm's owner.

    The run is addressed by its own id, so ownership has to be resolved through the
    farm it belongs to. `require_farm` raises `FARM_NOT_FOUND` for someone else's farm,
    which is deliberately indistinguishable from a run that does not exist.
    """
    run = analysis_repo.get_run(run_id) if _persisted() else store.analysis_runs.get(run_id)
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
    run = _latest(farm_id, user)

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
        # Through `image_service` for the same reason as `crops` above: reading the
        # store directly showed an empty diagnosis panel for a farm whose images were
        # in the database. Ownership is already resolved by `require_farm` at the top.
        recent_images=image_service.images_for_farm(farm_id)[:5],
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
    """The top `limit` ranked crops, with `total` reporting the whole ranking.

    `paginate` receives the full list and does the slicing, so `total` is the number of
    crops that were ranked rather than the number handed back on this page. Truncating
    first — as this did — made `total` and `page_size` agree at every limit, which told a
    client there was nothing more to ask for however few it had requested.
    """
    items = latest_analysis(farm_id, user).crop_recommendations
    return paginate(CropRecommendationList, list(items), 1, max(limit, 1))


def regenerative_recommendations(
    farm_id: UUID, *, limit: int, user: CurrentUser
) -> RegenerativeRecommendationList:
    """The top `limit` ranked practices, with `total` reporting the whole ranking."""
    items = latest_analysis(farm_id, user).regenerative_recommendations
    return paginate(RegenerativeRecommendationList, list(items), 1, max(limit, 1))
