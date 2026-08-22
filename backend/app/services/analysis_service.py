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
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.core.deps import CurrentUser
from app.core.errors import NoAnalysisYetError
from app.db.memory import FarmRecord, store
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
from app.services.environment_service import EnvironmentSnapshot
from app.services.farm_service import (
    _to_farm,
    _to_farm_crop,
    plantings_for_farm,
    primary_planting,
    require_farm,
)
from app.services.reference_service import _ensure_catalog, paginate
from app.services.simulation import seeded_rng

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


def _weather_risk(env: EnvironmentSnapshot, crop: Crop | None) -> WeatherRisk:
    window = 7
    forecast = env.forecast(window)

    hot_threshold = crop.optimal_temp_max_c if crop and crop.optimal_temp_max_c else 32.0
    cold_threshold = 2.0

    # Each count only considers days that actually reported the field.
    heat_days = _count_where(forecast, "temp_max_c", lambda v: v > hot_threshold)
    frost_days = _count_where(forecast, "temp_min_c", lambda v: v < cold_threshold)
    heavy_rain_days = _count_where(forecast, "precipitation_mm", lambda v: v > 25)
    windy_days = _count_where(forecast, "wind_kmh", lambda v: v > 40)

    # A day with no rainfall reading breaks the run rather than extending it: an
    # unmeasured day is not evidence of dryness.
    dry_run = longest_dry = 0
    for d in forecast:
        rain = _reading(d, "precipitation_mm")
        if rain is None:
            dry_run = 0
        elif rain <= 0.2:
            dry_run += 1
            longest_dry = max(longest_dry, dry_run)
        else:
            dry_run = 0

    missing = _unavailable(forecast, "temp_max_c", "temp_min_c", "precipitation_mm", "wind_kmh")

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
    if missing:
        # Stated before any all-clear, so a partial window is never read as calm.
        drivers.append(
            f"{INSUFFICIENT}: {', '.join(missing)} unavailable — the corresponding "
            "exposure was not assessed"
        )
    if not forecast:
        drivers.append(f"{INSUFFICIENT}: no daily forecast was available")
    elif not drivers:
        drivers.append("No threshold exceedances in the forecast window")

    factors: list[ScoredFactor] = []
    if _has(forecast, "temp_max_c"):
        heat_score = _clamp(100 - heat_days * 18, 0, 100)
        factors.append(
            ScoredFactor(
                key="heat_stress",
                label="Heat stress",
                score=heat_score,
                weight=0.4,
                band=_band_for(heat_score),
                explanation=(
                    f"{heat_days} of {len(forecast)} forecast days exceed the crop's "
                    "optimal maximum."
                ),
            )
        )
    else:
        factors.append(_unknown_factor("heat_stress", "Heat stress", ["maximum temperature"]))

    if _has(forecast, "precipitation_mm"):
        rain_score = _clamp(100 - heavy_rain_days * 22, 0, 100)
        factors.append(
            ScoredFactor(
                key="rainfall_extremes",
                label="Rainfall extremes",
                score=rain_score,
                weight=0.3,
                band=_band_for(rain_score),
                explanation=f"{heavy_rain_days} day(s) exceed 25 mm of rainfall.",
            )
        )
        dry_score = _clamp(100 - longest_dry * 12, 0, 100)
        factors.append(
            ScoredFactor(
                key="dry_spell",
                label="Dry spell",
                score=dry_score,
                weight=0.3,
                band=_band_for(dry_score),
                explanation=f"Longest run without measurable rain is {longest_dry} day(s).",
            )
        )
    else:
        factors.append(_unknown_factor("rainfall_extremes", "Rainfall extremes", ["precipitation"]))
        factors.append(_unknown_factor("dry_spell", "Dry spell", ["precipitation"]))

    if not forecast:
        explanation = (
            f"{INSUFFICIENT}: no daily forecast was available, so weather risk could "
            "not be assessed."
        )
    elif missing:
        explanation = (
            f"Weather risk over the next {window} days is {_risk_level_for(score).value} "
            f"for the inputs that were available. " + "; ".join(drivers) + "."
        )
    else:
        explanation = (
            f"Conditions over the next {window} days place weather risk at "
            f"{_risk_level_for(score).value}. " + "; ".join(drivers) + "."
        )

    return WeatherRisk(
        level=_risk_level_for(score),
        score=int(round(score)),
        forecast_window_days=window,
        heat_stress_days=heat_days,
        frost_risk_days=frost_days,
        heavy_rain_days=heavy_rain_days,
        high_wind_days=windy_days,
        longest_dry_spell_days=longest_dry,
        # Nullable in the contract: null is how "not measured" is expressed, and it
        # is what distinguishes "no hot days" from "no temperature data".
        max_temp_c=_max_of(forecast, "temp_max_c"),
        min_temp_c=_min_of(forecast, "temp_min_c"),
        total_precipitation_mm=(
            round(total, 1)
            if (total := _sum_of(forecast, "precipitation_mm")) is not None
            else None
        ),
        drivers=drivers,
        factors=factors,
        explanation=explanation,
    )


def _water_risk(
    env: EnvironmentSnapshot, record: FarmRecord, crop: Crop | None, stage: GrowthStage
) -> WaterRisk:
    lookback, lookahead = 30, 7
    history = env.history(lookback)
    forecast = env.forecast(lookahead)
    window = history + forecast

    kc = _KC_BY_STAGE.get(stage, 0.85)
    soil = env.soil
    capacity = soil.water_holding_capacity_mm or 50.0
    relief = _IRRIGATION_RELIEF.get(str(record.irrigation_type), 0.0)

    precipitation = _sum_of(window, "precipitation_mm")
    et0_total = _sum_of(window, "et0_mm")
    missing = _unavailable(window, "precipitation_mm", "et0_mm")

    # The balance is a difference of two sums; without either side there is no
    # balance to report. Reporting one as zero would invent a drought or a surplus.
    if precipitation is None or et0_total is None:
        return WaterRisk(
            level=RiskLevel.low,
            score=0,
            # Required by the contract and not nullable; the explanation, drivers and
            # the run's partial status carry the fact that nothing was computed.
            water_balance_mm=0.0,
            deficit_mm=0.0,
            recommended_irrigation_mm=0.0,
            # Nullable measurements stay null rather than reporting a false zero.
            total_precipitation_mm=None if precipitation is None else round(precipitation, 1),
            total_crop_water_demand_mm=None,
            soil_moisture_pct=None,
            water_holding_capacity_mm=capacity,
            days_until_stress=None,
            irrigation_window=None,
            irrigation_efficiency_note=(
                f"Farm uses {record.irrigation_type} irrigation."
                if relief
                else "Farm is rainfed; there is no irrigation buffer against a shortfall."
            ),
            drivers=[
                f"{INSUFFICIENT}: {', '.join(missing or ['weather data'])} unavailable, "
                "so the soil water balance was not calculated"
            ],
            factors=[
                _unknown_factor("water_balance", "Water balance", missing or ["weather data"]),
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
                f"{INSUFFICIENT}: {', '.join(missing or ['weather data'])} unavailable from "
                "the weather provider, so water risk could not be assessed."
            ),
        )

    demand = et0_total * kc
    balance = precipitation - demand
    deficit = max(0.0, -balance)
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
            f"Water balance is {balance:.0f} mm "
            f"({'deficit' if balance < 0 else 'surplus'}), placing water risk at "
            f"{_risk_level_for(score).value}."
        ),
    )


def _disease_risk(env: EnvironmentSnapshot, record: FarmRecord, crop: Crop | None) -> DiseaseRisk:
    today = env.today
    window = env.forecast(7)

    humid_days = _count_where(window, "humidity_pct", lambda v: v >= 80)

    # Compound conditions need both readings on the same day; a day missing either
    # is unknown, not a non-match, so it is skipped rather than counted as safe.
    def _both(d: DailyObservation, a: str, b: str) -> tuple[float, float] | None:
        first, second = _reading(d, a), _reading(d, b)
        return None if first is None or second is None else (first, second)

    mild_wet_days = sum(
        1
        for d in window
        if (pair := _both(d, "temp_mean_c", "humidity_pct")) is not None
        and 15 <= pair[0] <= 27
        and pair[1] >= 75
    )
    warm_wet_days = sum(
        1
        for d in window
        if (pair := _both(d, "temp_mean_c", "precipitation_mm")) is not None
        and pair[0] > 24
        and pair[1] > 2
    )

    missing = _unavailable(window, "humidity_pct", "temp_mean_c")

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
            (
                f"{INSUFFICIENT}: {', '.join(missing)} unavailable, so infection "
                "conditions could not be evaluated."
            )
            if missing
            else (
                f"{humid_days} of {len(window)} forecast days reach 80% humidity, with "
                f"{mild_wet_days} in the 15-27 °C infection window."
            )
        ),
        risks=sorted(items, key=lambda i: i.probability, reverse=True),
        factors=[
            (
                ScoredFactor(
                    key="humidity_hours",
                    label="Humidity exposure",
                    score=_clamp(100 - humid_days * 14, 0, 100),
                    weight=0.5,
                    band=_band_for(_clamp(100 - humid_days * 14, 0, 100)),
                    explanation=f"{humid_days} day(s) at or above 80% relative humidity.",
                )
                if _has(window, "humidity_pct")
                else _unknown_factor("humidity_hours", "Humidity exposure", ["humidity"])
            ),
            (
                ScoredFactor(
                    key="infection_window",
                    label="Infection temperature window",
                    score=_clamp(100 - mild_wet_days * 16, 0, 100),
                    weight=0.5,
                    band=_band_for(_clamp(100 - mild_wet_days * 16, 0, 100)),
                    explanation=f"{mild_wet_days} day(s) inside the 15-27 °C infection window.",
                )
                if _has(window, "humidity_pct") and _has(window, "temp_mean_c")
                else _unknown_factor(
                    "infection_window",
                    "Infection temperature window",
                    missing or ["humidity", "mean temperature"],
                )
            ),
        ],
        explanation=(
            (
                f"{INSUFFICIENT}: {', '.join(missing)} unavailable from the weather "
                "provider, so disease pressure could not be assessed."
            )
            if missing
            else (
                f"Humidity and temperature patterns place disease pressure at "
                f"{level.value} for the coming week."
            )
        ),
    )


def _soil_assessment(env: EnvironmentSnapshot, crop: Crop | None) -> SoilAssessment:
    soil = env.soil

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
    env: EnvironmentSnapshot, crop: Crop | None, planting, stage: GrowthStage
) -> CropHealth:
    today = env.today
    has_crop = planting is not None

    # Read the vegetation series the snapshot already holds, so the health panel and
    # GET /vegetation can never disagree. With no series there is no NDVI: 0.0 would
    # claim bare ground, which is a measurement we did not make.
    ndvi_now = env.vegetation[-1][1] if env.vegetation else None
    ndvi_then = (
        next(
            (
                value
                for sample_date, value in reversed(env.vegetation)
                if sample_date <= today - timedelta(days=30)
            ),
            env.vegetation[0][1],
        )
        if env.vegetation
        else None
    )

    if ndvi_now is not None and ndvi_then:
        change = (ndvi_now - ndvi_then) / abs(ndvi_then) * 100
        trend = "improving" if change > 5 else "declining" if change < -5 else "stable"
    else:
        change = 0.0
        trend = None

    ndvi_score = _clamp(ndvi_now / 0.85 * 100, 0, 100) if ndvi_now is not None else None
    trend_score = _clamp(65 + change, 0, 100) if ndvi_now is not None else None

    days_since_planting = None
    days_to_harvest = None
    gdd_accumulated = None
    if planting is not None and planting.planting_date is not None:
        days_since_planting = max(0, (today - planting.planting_date).days)
        # Accumulate over the overlap between the growing period and the snapshot
        # window. A crop planted before the window starts reports the GDD actually
        # observed rather than an extrapolation.
        since_planting = env.between(planting.planting_date, today)
        base = crop.base_temp_c if crop and crop.base_temp_c is not None else 10.0
        gdd_accumulated = round(
            sum(
                max(0.0, mean - base)
                for d in since_planting
                if (mean := _reading(d, "temp_mean_c")) is not None
            ),
            1,
        )
    if planting is not None and planting.expected_harvest_date is not None:
        days_to_harvest = (planting.expected_harvest_date - today).days

    stress: list[str] = []
    if ndvi_now is None:
        stress.append(f"{INSUFFICIENT}: no vegetation index was available for this farm")
    else:
        if change < -5:
            stress.append(f"NDVI declined {abs(change):.0f}% over the last 30 days")
        if ndvi_now < 0.35:
            stress.append(f"Canopy vigour is low (NDVI {ndvi_now})")
        if not has_crop:
            stress.append("No crop is registered for this farm, so vigour reflects bare ground")

    # With no vegetation index there is nothing to score. The contract requires a
    # numeric score and a band, so the neutral placeholders are used and the
    # explanation carries the truth — the same convention `_unknown_factor` uses.
    if ndvi_score is None or trend_score is None:
        composite = 0.0
    else:
        composite = ndvi_score * 0.6 + trend_score * 0.4

    return CropHealth(
        score=int(round(composite)),
        band=ScoreBand.moderate if ndvi_score is None else _band_for(composite),
        current_ndvi=ndvi_now,
        ndvi_trend=trend,
        growth_stage=stage.value,
        days_since_planting=days_since_planting,
        days_to_expected_harvest=days_to_harvest,
        gdd_accumulated=gdd_accumulated,
        gdd_required=crop.gdd_to_maturity if crop else None,
        stress_indicators=stress,
        factors=[
            (
                ScoredFactor(
                    key="canopy_vigour",
                    label="Canopy vigour",
                    score=round(ndvi_score, 1),
                    weight=0.6,
                    band=_band_for(ndvi_score),
                    explanation=f"NDVI of {ndvi_now}.",
                )
                if ndvi_score is not None
                else _unknown_factor("canopy_vigour", "Canopy vigour", ["vegetation index"])
            ),
            (
                ScoredFactor(
                    key="vigour_trend",
                    label="Vigour trend",
                    score=round(trend_score, 1),
                    weight=0.4,
                    band=_band_for(trend_score),
                    explanation=f"NDVI is {trend} ({change:+.0f}% over 30 days).",
                )
                if trend_score is not None
                else _unknown_factor("vigour_trend", "Vigour trend", ["vegetation index"])
            ),
        ],
        explanation=(
            (
                f"{INSUFFICIENT}: no vegetation index was available, so crop health "
                "could not be assessed."
            )
            if ndvi_now is None
            else (
                f"Canopy vigour reads NDVI {ndvi_now} and is {trend}, giving "
                f"{_band_for(composite).value} crop health."
            )
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

    weather = _weather_risk(env, crop)
    water = _water_risk(env, record, crop, stage)
    disease = _disease_risk(env, record, crop)
    soil_assessment = _soil_assessment(env, crop)
    crop_health = _crop_health(env, crop, planting, stage)

    def _section(
        key: str,
        label: str,
        score: float,
        band: ScoreBand,
        explanation: str,
        weight: float,
        assessed: bool,
    ) -> ScoredFactor:
        """One top-level factor, carrying weight only if its section was assessed."""
        if assessed:
            return ScoredFactor(
                key=key,
                label=label,
                score=score,
                weight=weight,
                band=band,
                explanation=explanation,
            )
        return ScoredFactor(
            key=key,
            label=label,
            score=0.0,
            weight=0.0,
            band=ScoreBand.moderate,
            explanation=explanation,
        )

    # A section contributes to the composite only when *every* sub-factor was
    # assessed. Partial evidence must not move the overall score: water risk still
    # knows the farm's irrigation type when rainfall is missing, and letting that
    # alone carry the section would imply a balance nobody calculated.
    def _assessed(section) -> bool:
        return bool(section.factors) and all(f.weight > 0 for f in section.factors)

    factors = [
        _section(
            "weather_risk",
            "Weather risk",
            _clamp(100 - weather.score, 0, 100),
            _band_for(100 - weather.score),
            weather.explanation,
            0.2,
            _assessed(weather),
        ),
        _section(
            "water_risk",
            "Water availability",
            _clamp(100 - water.score, 0, 100),
            _band_for(100 - water.score),
            water.explanation,
            0.25,
            _assessed(water),
        ),
        _section(
            "disease_risk",
            "Disease pressure",
            _clamp(100 - disease.score, 0, 100),
            _band_for(100 - disease.score),
            disease.explanation,
            0.2,
            _assessed(disease),
        ),
        # Soil is always available in this phase, so it is always assessed.
        _section(
            "soil_suitability",
            "Soil suitability",
            float(soil_assessment.score),
            soil_assessment.band,
            soil_assessment.explanation,
            0.2,
            True,
        ),
        _section(
            "crop_health",
            "Crop health",
            float(crop_health.score),
            crop_health.band,
            crop_health.explanation,
            0.15,
            _assessed(crop_health),
        ),
    ]

    # Factors that could not be assessed carry weight 0.0, so the denominator is the
    # weight of what was actually measured. Guarded because every factor being
    # unknown would otherwise divide by zero.
    total_weight = sum(f.weight for f in factors)
    overall = sum(f.score * f.weight for f in factors) / total_weight if total_weight > 0 else 0.0

    # A run is partial when a factor could not be assessed at all. An optional
    # measurement that changed no factor leaves the run complete, so `partial`
    # keeps its meaning.
    unassessed = [f.label for f in factors if f.weight == 0.0]
    degraded = list(
        dict.fromkeys([*env.degraded_sources, *([env.weather_meta.source] if unassessed else [])])
    )
    status = AnalysisStatus.partial if unassessed else AnalysisStatus.complete

    run_id = uuid4()

    advisories = _advisories(record.id, run_id, started, weather, water, disease, soil_assessment)

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
