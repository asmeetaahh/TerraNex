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

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from app.core.errors import NoAnalysisYetError
from app.db.memory import FarmRecord, store
from app.schemas.advisory import Advisory, AdvisoryList
from app.schemas.analysis import (
    AnalysisRun,
    AnalysisRunList,
    AnalysisRunSummary,
    FarmDashboard,
)
from app.schemas.common import DataSourceMeta, RiskLevel, ScoreBand, ScoredFactor
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
from app.services.farm_service import _to_farm, _to_farm_crop, primary_planting, require_farm
from app.services.reference_service import _ensure_catalog, paginate
from app.services.simulation import (
    seeded_rng,
    simulate_days,
    simulate_ndvi,
    simulate_soil,
    simulated_meta,
)

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
# Section builders
# --------------------------------------------------------------------------


def _weather_risk(record: FarmRecord, crop: Crop | None, today: date) -> WeatherRisk:
    window = 7
    forecast = simulate_days(record.latitude, record.longitude, today, window)

    hot_threshold = crop.optimal_temp_max_c if crop and crop.optimal_temp_max_c else 32.0
    cold_threshold = 2.0

    heat_days = sum(1 for d in forecast if d.temp_max_c > hot_threshold)
    frost_days = sum(1 for d in forecast if d.temp_min_c < cold_threshold)
    heavy_rain_days = sum(1 for d in forecast if d.precipitation_mm > 25)
    windy_days = sum(1 for d in forecast if d.wind_kmh > 40)

    dry_run = longest_dry = 0
    for d in forecast:
        if d.precipitation_mm <= 0.2:
            dry_run += 1
            longest_dry = max(longest_dry, dry_run)
        else:
            dry_run = 0

    score = _clamp(
        heat_days * 9 + frost_days * 14 + heavy_rain_days * 8 + windy_days * 6 + longest_dry * 3,
        0,
        100,
    )

    drivers: list[str] = []
    if heat_days:
        drivers.append(f"{heat_days} forecast day(s) above {hot_threshold:.0f} °C")
    if frost_days:
        drivers.append(f"{frost_days} forecast day(s) below {cold_threshold:.0f} °C")
    if heavy_rain_days:
        drivers.append(f"{heavy_rain_days} day(s) with more than 25 mm of rain")
    if windy_days:
        drivers.append(f"{windy_days} day(s) with winds above 40 km/h")
    if longest_dry >= 5:
        drivers.append(f"A {longest_dry}-day dry spell in the forecast window")
    if not drivers:
        drivers.append("No threshold exceedances in the forecast window")

    return WeatherRisk(
        level=_risk_level_for(score),
        score=int(round(score)),
        forecast_window_days=window,
        heat_stress_days=heat_days,
        frost_risk_days=frost_days,
        heavy_rain_days=heavy_rain_days,
        high_wind_days=windy_days,
        longest_dry_spell_days=longest_dry,
        max_temp_c=max(d.temp_max_c for d in forecast),
        min_temp_c=min(d.temp_min_c for d in forecast),
        total_precipitation_mm=round(sum(d.precipitation_mm for d in forecast), 1),
        drivers=drivers,
        factors=[
            ScoredFactor(
                key="heat_stress",
                label="Heat stress",
                score=_clamp(100 - heat_days * 18, 0, 100),
                weight=0.4,
                band=_band_for(_clamp(100 - heat_days * 18, 0, 100)),
                explanation=(
                    f"{heat_days} of {window} forecast days exceed the crop's optimal maximum."
                ),
            ),
            ScoredFactor(
                key="rainfall_extremes",
                label="Rainfall extremes",
                score=_clamp(100 - heavy_rain_days * 22, 0, 100),
                weight=0.3,
                band=_band_for(_clamp(100 - heavy_rain_days * 22, 0, 100)),
                explanation=f"{heavy_rain_days} day(s) exceed 25 mm of rainfall.",
            ),
            ScoredFactor(
                key="dry_spell",
                label="Dry spell",
                score=_clamp(100 - longest_dry * 12, 0, 100),
                weight=0.3,
                band=_band_for(_clamp(100 - longest_dry * 12, 0, 100)),
                explanation=f"Longest run without measurable rain is {longest_dry} day(s).",
            ),
        ],
        explanation=(
            f"Simulated conditions over the next {window} days place weather risk at "
            f"{_risk_level_for(score).value}. " + "; ".join(drivers) + "."
        ),
    )


def _water_risk(
    record: FarmRecord, crop: Crop | None, stage: GrowthStage, today: date
) -> WaterRisk:
    lookback, lookahead = 30, 7
    history = simulate_days(
        record.latitude, record.longitude, today - timedelta(days=lookback), lookback
    )
    forecast = simulate_days(record.latitude, record.longitude, today, lookahead)
    window = history + forecast

    kc = _KC_BY_STAGE.get(stage, 0.85)
    precipitation = sum(d.precipitation_mm for d in window)
    demand = sum(d.et0_mm for d in window) * kc
    balance = precipitation - demand
    deficit = max(0.0, -balance)

    soil = simulate_soil(record.latitude, record.longitude)
    capacity = soil.water_holding_capacity_mm or 50.0

    relief = _IRRIGATION_RELIEF.get(str(record.irrigation_type), 0.0)
    effective_deficit = deficit * (1 - relief)

    score = _clamp(effective_deficit / max(capacity, 1) * 100, 0, 100)

    daily_demand = max(0.1, demand / max(len(window), 1))
    days_until_stress = (
        int(max(0, (capacity - effective_deficit) / daily_demand))
        if effective_deficit > 0
        else None
    )
    if days_until_stress is not None:
        days_until_stress = min(days_until_stress, 60)

    soil_moisture = _clamp(100 * (1 - effective_deficit / max(capacity, 1)), 0, 100)

    drivers = [
        f"{precipitation:.0f} mm rainfall against {demand:.0f} mm crop demand over "
        f"{lookback} days of history and {lookahead} days of forecast",
        f"Soil holds about {capacity:.0f} mm of plant-available water",
    ]
    if relief:
        drivers.append(
            f"{record.irrigation_type} irrigation offsets roughly "
            f"{relief * 100:.0f}% of the shortfall"
        )

    return WaterRisk(
        level=_risk_level_for(score),
        score=int(round(score)),
        water_balance_mm=round(balance, 1),
        deficit_mm=round(deficit, 1),
        total_precipitation_mm=round(precipitation, 1),
        total_crop_water_demand_mm=round(demand, 1),
        soil_moisture_pct=round(soil_moisture, 1),
        water_holding_capacity_mm=capacity,
        days_until_stress=days_until_stress,
        recommended_irrigation_mm=round(effective_deficit if effective_deficit > 5 else 0.0, 1),
        irrigation_window=(
            "within 48 hours" if score >= 50 else "within the next week" if score >= 28 else None
        ),
        irrigation_efficiency_note=(
            f"Farm uses {record.irrigation_type} irrigation."
            if relief
            else "Farm is rainfed; there is no irrigation buffer against a shortfall."
        ),
        drivers=drivers,
        factors=[
            ScoredFactor(
                key="water_balance",
                label="Water balance",
                score=_clamp(100 - score, 0, 100),
                weight=0.6,
                band=_band_for(_clamp(100 - score, 0, 100)),
                explanation=f"Balance of {balance:.0f} mm against a {capacity:.0f} mm reservoir.",
            ),
            ScoredFactor(
                key="irrigation_capacity",
                label="Irrigation capacity",
                score=_clamp(relief * 100, 0, 100),
                weight=0.4,
                band=_band_for(_clamp(relief * 100, 0, 100)),
                explanation=f"{record.irrigation_type} irrigation.",
            ),
        ],
        explanation=(
            f"Simulated water balance is {balance:.0f} mm "
            f"({'deficit' if balance < 0 else 'surplus'}), placing water risk at "
            f"{_risk_level_for(score).value}."
        ),
    )


def _disease_risk(record: FarmRecord, crop: Crop | None, today: date) -> DiseaseRisk:
    window = simulate_days(record.latitude, record.longitude, today, 7)

    humid_days = sum(1 for d in window if d.humidity_pct >= 80)
    mild_wet_days = sum(1 for d in window if 15 <= d.temp_mean_c <= 27 and d.humidity_pct >= 75)
    warm_wet_days = sum(1 for d in window if d.temp_mean_c > 24 and d.precipitation_mm > 2)

    score = _clamp(humid_days * 7 + mild_wet_days * 9 + warm_wet_days * 6, 0, 100)
    level = _risk_level_for(score)

    disease_names = (
        crop.common_diseases if crop and crop.common_diseases else ["fungal_leaf_spot"]
    )[:3]
    rng = seeded_rng("disease", record.id, today.isoformat())

    items: list[DiseaseRiskItem] = []
    for index, name in enumerate(disease_names):
        item_score = _clamp(score - index * 12 + rng.uniform(-5, 5), 0, 100)
        conditions = []
        if mild_wet_days:
            conditions.append(f"{mild_wet_days} day(s) at 15-27 °C with humidity at or above 75%")
        if humid_days:
            conditions.append(f"{humid_days} day(s) with humidity at or above 80%")
        if warm_wet_days:
            conditions.append(f"{warm_wet_days} warm day(s) with measurable rain")
        if not conditions:
            conditions.append("No sustained humidity or leaf-wetness window in the forecast")

        items.append(
            DiseaseRiskItem(
                name=name.replace("_", " ").capitalize(),
                pathogen=None,
                crop_code=crop.code if crop else None,
                level=_risk_level_for(item_score),
                probability=round(item_score / 100, 2),
                triggering_conditions=conditions,
                preventive_actions=[
                    "Scout the lower canopy twice this week",
                    "Improve airflow by managing canopy density",
                    "Avoid overhead irrigation late in the day",
                ],
                scouting_advice="Inspect 10 plants at 5 points across the field.",
            )
        )

    return DiseaseRisk(
        level=level,
        score=int(round(score)),
        conditions_summary=(
            f"{humid_days} of 7 simulated days reach 80% humidity, with "
            f"{mild_wet_days} in the 15-27 °C infection window."
        ),
        risks=sorted(items, key=lambda i: i.probability, reverse=True),
        factors=[
            ScoredFactor(
                key="humidity_hours",
                label="Humidity exposure",
                score=_clamp(100 - humid_days * 14, 0, 100),
                weight=0.5,
                band=_band_for(_clamp(100 - humid_days * 14, 0, 100)),
                explanation=f"{humid_days} day(s) at or above 80% relative humidity.",
            ),
            ScoredFactor(
                key="infection_window",
                label="Infection temperature window",
                score=_clamp(100 - mild_wet_days * 16, 0, 100),
                weight=0.5,
                band=_band_for(_clamp(100 - mild_wet_days * 16, 0, 100)),
                explanation=f"{mild_wet_days} day(s) inside the 15-27 °C infection window.",
            ),
        ],
        explanation=(
            f"Simulated humidity and temperature patterns place disease pressure at "
            f"{level.value} for the coming week."
        ),
    )


def _soil_assessment(record: FarmRecord, crop: Crop | None) -> SoilAssessment:
    soil = simulate_soil(record.latitude, record.longitude)

    ph_min = crop.ph_min if crop and crop.ph_min else 5.5
    ph_max = crop.ph_max if crop and crop.ph_max else 7.5

    if ph_min <= soil.ph <= ph_max:
        ph_score, ph_status = 92.0, "optimal"
    else:
        distance = min(abs(soil.ph - ph_min), abs(soil.ph - ph_max))
        ph_score = _clamp(92 - distance * 32, 5, 92)
        ph_status = "too_acidic" if soil.ph < ph_min else "too_alkaline"

    soc_score = _clamp(soil.organic_carbon_pct / 3.0 * 100, 5, 100)
    organic_status = (
        "low"
        if soil.organic_carbon_pct < 1.0
        else "adequate"
        if soil.organic_carbon_pct < 2.5
        else "high"
    )

    preferred = set(crop.preferred_textures) if crop and crop.preferred_textures else set()
    texture_score = 90.0 if not preferred else (90.0 if soil.texture_class in preferred else 55.0)

    cec_score = _clamp(soil.cec_cmol_kg / 25 * 100, 5, 100)
    fertility_status = (
        "low" if soil.cec_cmol_kg < 10 else "moderate" if soil.cec_cmol_kg < 20 else "high"
    )

    composite = ph_score * 0.35 + soc_score * 0.25 + texture_score * 0.25 + cec_score * 0.15

    limitations: list[str] = []
    if ph_status != "optimal":
        limitations.append(
            f"pH {soil.ph} is outside the {ph_min}-{ph_max} range preferred by "
            f"{crop.name if crop else 'most crops'}"
        )
    if soil.organic_carbon_pct < 1.0:
        limitations.append(f"Organic carbon is low at {soil.organic_carbon_pct}%")
    if preferred and soil.texture_class not in preferred:
        limitations.append(f"{soil.texture_class.value} texture is not preferred by this crop")
    if soil.cec_cmol_kg < 10:
        limitations.append(f"Low cation exchange capacity ({soil.cec_cmol_kg} cmol/kg)")

    return SoilAssessment(
        score=int(round(composite)),
        band=_band_for(composite),
        texture_class=soil.texture_class,
        ph_status=ph_status,
        organic_matter_status=organic_status,
        fertility_status=fertility_status,
        limitations=limitations,
        factors=[
            ScoredFactor(
                key="soil_ph",
                label="Soil pH",
                score=round(ph_score, 1),
                weight=0.35,
                band=_band_for(ph_score),
                explanation=f"pH {soil.ph} against a preferred {ph_min}-{ph_max}.",
            ),
            ScoredFactor(
                key="organic_carbon",
                label="Organic carbon",
                score=round(soc_score, 1),
                weight=0.25,
                band=_band_for(soc_score),
                explanation=f"Organic carbon at {soil.organic_carbon_pct}%.",
            ),
            ScoredFactor(
                key="texture",
                label="Texture match",
                score=round(texture_score, 1),
                weight=0.25,
                band=_band_for(texture_score),
                explanation=f"{soil.texture_class.value} texture.",
            ),
            ScoredFactor(
                key="cec",
                label="Nutrient holding",
                score=round(cec_score, 1),
                weight=0.15,
                band=_band_for(cec_score),
                explanation=f"CEC of {soil.cec_cmol_kg} cmol/kg.",
            ),
        ],
        explanation=(
            f"Simulated soil is {soil.texture_class.value} at pH {soil.ph} with "
            f"{soil.organic_carbon_pct}% organic carbon, scoring "
            f"{_band_for(composite).value} for this farm."
        ),
    )


def _crop_health(
    record: FarmRecord, crop: Crop | None, planting, stage: GrowthStage, today: date
) -> CropHealth:
    has_crop = planting is not None
    ndvi_now = simulate_ndvi(record.latitude, record.longitude, today, has_crop=has_crop)
    ndvi_then = simulate_ndvi(
        record.latitude, record.longitude, today - timedelta(days=30), has_crop=has_crop
    )

    change = ((ndvi_now - ndvi_then) / abs(ndvi_then) * 100) if ndvi_then else 0.0
    trend = "improving" if change > 5 else "declining" if change < -5 else "stable"

    ndvi_score = _clamp(ndvi_now / 0.85 * 100, 0, 100)
    trend_score = _clamp(65 + change, 0, 100)

    days_since_planting = None
    days_to_harvest = None
    gdd_accumulated = None
    if planting is not None and planting.planting_date is not None:
        days_since_planting = max(0, (today - planting.planting_date).days)
        history = simulate_days(
            record.latitude, record.longitude, planting.planting_date, days_since_planting or 1
        )
        base = crop.base_temp_c if crop and crop.base_temp_c is not None else 10.0
        gdd_accumulated = round(sum(max(0.0, d.temp_mean_c - base) for d in history), 1)
    if planting is not None and planting.expected_harvest_date is not None:
        days_to_harvest = (planting.expected_harvest_date - today).days

    stress: list[str] = []
    if change < -5:
        stress.append(f"NDVI declined {abs(change):.0f}% over the last 30 simulated days")
    if ndvi_now < 0.35:
        stress.append(f"Canopy vigour is low (NDVI {ndvi_now})")
    if not has_crop:
        stress.append("No crop is registered for this farm, so vigour reflects bare ground")

    composite = ndvi_score * 0.6 + trend_score * 0.4

    return CropHealth(
        score=int(round(composite)),
        band=_band_for(composite),
        current_ndvi=ndvi_now,
        ndvi_trend=trend,
        growth_stage=stage.value,
        days_since_planting=days_since_planting,
        days_to_expected_harvest=days_to_harvest,
        gdd_accumulated=gdd_accumulated,
        gdd_required=crop.gdd_to_maturity if crop else None,
        stress_indicators=stress,
        factors=[
            ScoredFactor(
                key="canopy_vigour",
                label="Canopy vigour",
                score=round(ndvi_score, 1),
                weight=0.6,
                band=_band_for(ndvi_score),
                explanation=f"Simulated NDVI of {ndvi_now}.",
            ),
            ScoredFactor(
                key="vigour_trend",
                label="Vigour trend",
                score=round(trend_score, 1),
                weight=0.4,
                band=_band_for(trend_score),
                explanation=f"NDVI is {trend} ({change:+.0f}% over 30 days).",
            ),
        ],
        explanation=(
            f"Simulated canopy vigour reads NDVI {ndvi_now} and is {trend}, giving "
            f"{_band_for(composite).value} crop health."
        ),
    )


def _advisories(
    farm_id: UUID,
    run_id: UUID,
    created_at: datetime,
    weather: WeatherRisk,
    water: WaterRisk,
    disease: DiseaseRisk,
    soil: SoilAssessment,
) -> list[Advisory]:
    """Template advisories derived from the fixture scores.

    `ai_mode` on the run is `mock`, so this text is explicitly not model output.
    """
    drafts: list[tuple[AdvisoryCategory, AdvisoryPriority, str, str, str, str | None, float]] = []

    if water.score >= 28:
        priority = (
            AdvisoryPriority.critical
            if water.score >= 70
            else AdvisoryPriority.high
            if water.score >= 50
            else AdvisoryPriority.medium
        )
        drafts.append(
            (
                AdvisoryCategory.irrigation,
                priority,
                f"Apply about {water.recommended_irrigation_mm:.0f} mm of irrigation",
                "The simulated soil water balance is negative and the forecast offers "
                "little relief. Applying the recommended depth would return the root "
                "zone to a comfortable range.",
                f"Water balance is {water.water_balance_mm:.0f} mm with a "
                f"{water.deficit_mm:.0f} mm deficit against a "
                f"{water.water_holding_capacity_mm:.0f} mm reservoir.",
                water.irrigation_window,
                0.75,
            )
        )

    if disease.score >= 28:
        top = disease.risks[0].name if disease.risks else "foliar disease"
        drafts.append(
            (
                AdvisoryCategory.disease,
                AdvisoryPriority.high if disease.score >= 50 else AdvisoryPriority.medium,
                f"Scout for {top.lower()} this week",
                "Humidity and temperature are inside the window where infection establishes. "
                "Scouting now is far cheaper than treating an established outbreak.",
                disease.conditions_summary,
                "within 3 days",
                0.7,
            )
        )

    if weather.heat_stress_days >= 2:
        drafts.append(
            (
                AdvisoryCategory.weather,
                AdvisoryPriority.high if weather.heat_stress_days >= 4 else AdvisoryPriority.medium,
                f"Prepare for {weather.heat_stress_days} days of heat stress",
                "Shift irrigation to early morning and avoid canopy operations during peak heat.",
                "; ".join(weather.drivers),
                "this week",
                0.68,
            )
        )

    if weather.frost_risk_days:
        drafts.append(
            (
                AdvisoryCategory.weather,
                AdvisoryPriority.critical,
                f"Frost expected on {weather.frost_risk_days} day(s)",
                "Consider frost protection for sensitive growth stages.",
                f"{weather.frost_risk_days} forecast day(s) fall below 2 °C.",
                "within 48 hours",
                0.72,
            )
        )

    for limitation in soil.limitations[:2]:
        drafts.append(
            (
                AdvisoryCategory.soil,
                AdvisoryPriority.medium,
                "Address a soil constraint",
                f"{limitation}. Correcting this raises the ceiling on every other intervention.",
                f"Soil assessment scored {soil.score}/100 ({soil.band.value}).",
                "this season",
                0.6,
            )
        )

    if not drafts:
        drafts.append(
            (
                AdvisoryCategory.planting,
                AdvisoryPriority.low,
                "Conditions are stable — maintain the current plan",
                "No simulated risk threshold was exceeded this week. Keep monitoring.",
                f"Weather {weather.score}/100, water {water.score}/100, "
                f"disease {disease.score}/100.",
                None,
                0.55,
            )
        )

    order = {
        AdvisoryPriority.critical: 0,
        AdvisoryPriority.high: 1,
        AdvisoryPriority.medium: 2,
        AdvisoryPriority.low: 3,
    }
    drafts.sort(key=lambda d: order[d[1]])

    return [
        Advisory(
            id=uuid4(),
            farm_id=farm_id,
            analysis_run_id=run_id,
            category=category,
            priority=priority,
            title=title,
            body=body,
            rationale=rationale,
            action_window=window,
            confidence=confidence,
            created_at=created_at,
            dismissed_at=None,
        )
        for category, priority, title, body, rationale, window, confidence in drafts
    ]


def _crop_recommendations(record: FarmRecord, current_code: str | None, limit: int = 5):
    _ensure_catalog()
    soil = simulate_soil(record.latitude, record.longitude)
    today = datetime.now(UTC).date()
    year = simulate_days(record.latitude, record.longitude, today - timedelta(days=180), 180)
    mean_temp = sum(d.temp_mean_c for d in year) / len(year)
    seasonal_rain = sum(d.precipitation_mm for d in year) * 2  # scale a half-year to annual

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

        if crop.optimal_temp_min_c is not None and crop.optimal_temp_max_c is not None:
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

        if crop.water_need_mm_season:
            ratio = seasonal_rain / crop.water_need_mm_season
            water_score = _clamp(100 - abs(1 - ratio) * 55, 5, 100)
            if ratio < 0.7:
                considerations.append(
                    f"Needs about {crop.water_need_mm_season:.0f} mm/season; "
                    f"simulated rainfall is around {seasonal_rain:.0f} mm"
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
                explanation=f"Mean temperature {mean_temp:.0f} °C.",
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
                explanation=f"Simulated seasonal rainfall around {seasonal_rain:.0f} mm.",
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
    record: FarmRecord,
    soil_assessment: SoilAssessment,
    water: WaterRisk,
    disease: DiseaseRisk,
    weather: WeatherRisk,
    limit: int = 5,
):
    soil = simulate_soil(record.latitude, record.longitude)

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


def _build_run(record: FarmRecord) -> AnalysisRun:
    started = datetime.now(UTC)
    today = started.date()

    planting = primary_planting(record.id)
    crop = None
    stage = GrowthStage.not_planted
    if planting is not None:
        from app.services.reference_service import get_crop

        crop = get_crop(planting.crop_id)
        stage = GrowthStage(planting.growth_stage)

    weather = _weather_risk(record, crop, today)
    water = _water_risk(record, crop, stage, today)
    disease = _disease_risk(record, crop, today)
    soil_assessment = _soil_assessment(record, crop)
    crop_health = _crop_health(record, crop, planting, stage, today)

    factors = [
        ScoredFactor(
            key="weather_risk",
            label="Weather risk",
            score=_clamp(100 - weather.score, 0, 100),
            weight=0.2,
            band=_band_for(100 - weather.score),
            explanation=weather.explanation,
        ),
        ScoredFactor(
            key="water_risk",
            label="Water availability",
            score=_clamp(100 - water.score, 0, 100),
            weight=0.25,
            band=_band_for(100 - water.score),
            explanation=water.explanation,
        ),
        ScoredFactor(
            key="disease_risk",
            label="Disease pressure",
            score=_clamp(100 - disease.score, 0, 100),
            weight=0.2,
            band=_band_for(100 - disease.score),
            explanation=disease.explanation,
        ),
        ScoredFactor(
            key="soil_suitability",
            label="Soil suitability",
            score=float(soil_assessment.score),
            weight=0.2,
            band=soil_assessment.band,
            explanation=soil_assessment.explanation,
        ),
        ScoredFactor(
            key="crop_health",
            label="Crop health",
            score=float(crop_health.score),
            weight=0.15,
            band=crop_health.band,
            explanation=crop_health.explanation,
        ),
    ]

    overall = sum(f.score * f.weight for f in factors) / sum(f.weight for f in factors)
    run_id = uuid4()

    advisories = _advisories(record.id, run_id, started, weather, water, disease, soil_assessment)

    crop_recs = _crop_recommendations(record, crop.code if crop else None)
    regen_recs = _regenerative_recommendations(record, soil_assessment, water, disease, weather)

    crop_label = crop.name if crop else "no registered crop"
    summary = (
        f"{record.name} scores {int(round(overall))}/100 ({_band_for(overall).value}) with "
        f"{crop_label}. Water risk is {water.level.value}, disease pressure is "
        f"{disease.level.value} and weather risk is {weather.level.value}. "
        f"{len(advisories)} advisory item(s) require attention. "
        "All figures are simulated Phase 3 fixtures, not live measurements."
    )

    finished = datetime.now(UTC)
    sources: list[DataSourceMeta] = [
        simulated_meta("Weather inputs: simulated seasonal climatology."),
        simulated_meta("Soil inputs: simulated profile classified on the USDA triangle."),
        simulated_meta("Vegetation inputs: simulated seasonal canopy model."),
    ]

    return AnalysisRun(
        id=run_id,
        farm_id=record.id,
        status=AnalysisStatus.complete,
        created_at=started,
        duration_ms=max(1, int((finished - started).total_seconds() * 1000)),
        model=None,
        prompt_version=PROMPT_VERSION,
        ai_mode=AIMode.mock,
        degraded_sources=[],
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


def run_analysis(farm_id: UUID, *, force_refresh: bool = False) -> AnalysisRun:
    record = require_farm(farm_id)

    if not force_refresh:
        existing = store.latest_run(farm_id)
        if existing is not None:
            return existing

    run = _build_run(record)
    with store.lock:
        store.analysis_runs[run.id] = run
    return run


def latest_analysis(farm_id: UUID) -> AnalysisRun:
    require_farm(farm_id)
    run = store.latest_run(farm_id)
    if run is None:
        raise NoAnalysisYetError(
            "No analysis has been run for this farm yet. POST to "
            f"/api/v1/farms/{farm_id}/analysis to create one.",
            details={"farm_id": str(farm_id)},
        )
    return run


def list_runs(farm_id: UUID, *, page: int, page_size: int) -> AnalysisRunList:
    require_farm(farm_id)
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


def get_run(run_id: UUID) -> AnalysisRun:
    run = store.analysis_runs.get(run_id)
    if run is None:
        raise NoAnalysisYetError(
            f"Analysis run {run_id} does not exist.", details={"run_id": str(run_id)}
        )
    return run


def dashboard(farm_id: UUID) -> FarmDashboard:
    """Never 404s on a missing analysis — returns `has_analysis: false` instead, so
    a newly registered farm renders an empty state rather than an error."""
    record = require_farm(farm_id)
    run = store.latest_run(farm_id)

    weather = environment_service.build_weather(record, forecast_days=7, history_days=30)

    return FarmDashboard(
        farm=_to_farm(record),
        crops=[_to_farm_crop(c) for c in store.crops_for_farm(farm_id)],
        has_analysis=run is not None,
        analysis=run,
        current_weather=weather.current,
        recent_images=store.images_for_farm(farm_id)[:5],
        data_freshness=[
            simulated_meta("Dashboard weather panel: simulated."),
            simulated_meta("Dashboard analysis panel: simulated Phase 3 fixture."),
        ],
    )


# ---- projections of the latest run ----


def weather_risk(farm_id: UUID) -> WeatherRisk:
    return latest_analysis(farm_id).weather_risk


def water_risk(farm_id: UUID) -> WaterRisk:
    return latest_analysis(farm_id).water_risk


def disease_risk(farm_id: UUID) -> DiseaseRisk:
    return latest_analysis(farm_id).disease_risk


def crop_health(farm_id: UUID) -> CropHealth:
    return latest_analysis(farm_id).crop_health


def advisories(
    farm_id: UUID,
    *,
    category: AdvisoryCategory | None,
    priority: AdvisoryPriority | None,
    include_dismissed: bool,
    page: int,
    page_size: int,
) -> AdvisoryList:
    items = list(latest_analysis(farm_id).advisories)
    if category is not None:
        items = [a for a in items if a.category == category]
    if priority is not None:
        items = [a for a in items if a.priority == priority]
    if not include_dismissed:
        items = [a for a in items if a.dismissed_at is None]
    return paginate(AdvisoryList, items, page, page_size)


def crop_recommendations(farm_id: UUID, *, limit: int) -> CropRecommendationList:
    items = latest_analysis(farm_id).crop_recommendations[:limit]
    return paginate(CropRecommendationList, items, 1, max(limit, 1))


def regenerative_recommendations(farm_id: UUID, *, limit: int) -> RegenerativeRecommendationList:
    items = latest_analysis(farm_id).regenerative_recommendations[:limit]
    return paginate(RegenerativeRecommendationList, items, 1, max(limit, 1))
