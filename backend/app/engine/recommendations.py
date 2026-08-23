"""What to plant, and what to change about how the ground is managed.

Two recommenders, both deterministic and both scored only against measurements that
were actually taken.

**Crop suitability** ranks the catalog against this site: pH inside the crop's tolerated
range, mean temperature inside its optimal band, texture among its preferences, and
observed rainfall against its seasonal requirement.

**Regenerative practices** rank a fixed set of interventions against the constraints
this farm actually has — low carbon, erosion risk, compaction, disease pressure, water
stress, acidity.

**The correction that motivated moving this into the engine.** Both recommenders used
to read `soil.ph`, `soil.texture_class`, `soil.sand_pct` and friends unconditionally.
That was safe only while soil was simulated and therefore never absent; once a real
survey could return an empty profile, an unavailable soil provider crashed the whole
analysis. Every property is now optional, and an unmeasured one is dropped from the
weighting rather than replaced with a neutral-looking number — a fabricated 70 is still
a fabrication, and it moves a farmer's planting decision.

**The catalog arrives on the context.** The engine cannot read the store or the
database, so an adapter resolves whichever catalog is configured and passes it in. The
previous code read the in-memory store directly, which is empty whenever a database is
configured — so every database-backed deployment silently offered zero crops.

Pure: no I/O, no clock, no randomness. Every value comes from the context.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.engine.context import AnalysisContext, CropParameters, DailyPoint, SoilPoint
from app.engine.disease import DiseaseAssessment
from app.engine.reasons import (
    CROP_PH_OUTSIDE_RANGE,
    CROP_PH_WITHIN_RANGE,
    CROP_TEMPERATURE_OPTIMAL,
    CROP_TEMPERATURE_OUTSIDE,
    CROP_TEXTURE_MATCH,
    CROP_TEXTURE_MISMATCH,
    CROP_WATER_SHORTFALL,
    CROP_WATER_SUFFICIENT,
    ParamValue,
    Reason,
)
from app.engine.scoring import INSUFFICIENT, clamp, factor, unknown_factor
from app.engine.soil import SoilAssessmentResult
from app.engine.water import WaterAssessment
from app.engine.weather import WeatherAssessment
from app.schemas.common import ScoredFactor

# --------------------------------------------------------------------------
# Crop suitability
# --------------------------------------------------------------------------

#: Score a crop earns where a property sits exactly where it wants it.
IDEAL_MATCH_SCORE = 95.0

#: Score lost per pH unit outside the crop's tolerated range.
PH_PENALTY_PER_UNIT = 30.0

#: Score lost per degree outside the crop's optimal temperature band.
TEMPERATURE_PENALTY_PER_DEGREE = 6.0

#: Texture is a membership test rather than a distance, so it has two outcomes.
TEXTURE_MATCH_SCORE = 92.0
TEXTURE_MISMATCH_SCORE = 52.0

#: Score lost per unit of relative distance between rainfall and seasonal requirement.
WATER_PENALTY_PER_RATIO = 55.0

#: Rainfall below this share of the requirement is worth raising as a constraint.
WATER_SHORTFALL_RATIO = 0.7

MINIMUM_COMPONENT_SCORE = 5.0

WEIGHT_PH = 0.3
WEIGHT_TEMPERATURE = 0.3
WEIGHT_TEXTURE = 0.2
WEIGHT_WATER = 0.2

DEFAULT_CROP_LIMIT = 5

DAYS_PER_YEAR = 365


@dataclass(frozen=True, slots=True)
class CropSuggestion:
    """One ranked crop."""

    code: str
    name: str
    category: str | None
    season: str | None
    score: float
    rank: int
    is_current_crop: bool
    water_requirement_mm: float | None
    planting_window: str | None
    strengths: tuple[str, ...]
    considerations: tuple[str, ...]
    factors: tuple[ScoredFactor, ...]
    rationale: str

    #: The numbers `strengths` and `considerations` were formatted from, as data.
    #:
    #: One per assessed component, so a consumer can state why this crop suits the site
    #: in a language this module does not speak. Empty for a component that could not be
    #: assessed — `factors` already reports that.
    reasons: tuple[Reason, ...] = ()


@dataclass(frozen=True, slots=True)
class SiteConditions:
    """The site reduced to what crop suitability depends on.

    `None` throughout means unmeasured. Derived once per run so every crop is judged
    against exactly the same summary of the site.
    """

    mean_temp_c: float | None = None
    seasonal_rainfall_mm: float | None = None
    soil_ph: float | None = None
    texture_class: str | None = None


def _numeric(value: object) -> float | None:
    """One usable measurement, or None. `bool` is excluded; it subclasses `int`."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _known(days: Sequence[DailyPoint], attr: str) -> list[float]:
    return [v for day in days if (v := _numeric(getattr(day, attr, None))) is not None]


def site_conditions(context: AnalysisContext) -> SiteConditions:
    """Summarise the site once, from whatever was actually observed."""
    history = context.history(len(context.daily)) or list(context.daily)

    temps = _known(history, "temp_mean_c")
    mean_temp = sum(temps) / len(temps) if temps else None

    # Annualise only the days that actually reported rainfall. Days with no reading are
    # excluded from both numerator and denominator rather than counted as dry, which
    # would bias every recommendation toward drought-tolerant crops.
    rain = _known(history, "precipitation_mm")
    seasonal_rain = (sum(rain) * (DAYS_PER_YEAR / len(rain))) if rain else None

    soil: SoilPoint = context.soil
    return SiteConditions(
        mean_temp_c=mean_temp,
        seasonal_rainfall_mm=seasonal_rain,
        soil_ph=_numeric(soil.ph),
        texture_class=soil.texture_class,
    )


def _params(**values: ParamValue) -> dict[str, ParamValue]:
    """Drop absent values rather than publishing them as null.

    The same convention the disease and advisory codes use: `null` reads as *measured,
    and unbounded*, which is a different claim from *not part of this comparison*.
    """
    return {name: value for name, value in values.items() if value is not None}


#: A component that could not be assessed emits no reason.
#:
#: `unknown_factor` already says the evidence was missing, and a reason whose params are
#: all absent would state nothing while looking like a finding.
NO_REASONS: tuple[Reason, ...] = ()


def _ph_component(
    crop: CropParameters, site: SiteConditions
) -> tuple[ScoredFactor, list[str], list[str], tuple[Reason, ...]]:
    strengths: list[str] = []
    considerations: list[str] = []

    if site.soil_ph is None:
        return (
            unknown_factor("ph_match", "pH match", ["soil pH"]),
            strengths,
            considerations,
            NO_REASONS,
        )
    if crop.ph_min is None or crop.ph_max is None:
        return (
            unknown_factor("ph_match", "pH match", ["this crop's tolerated pH range"]),
            strengths,
            considerations,
            NO_REASONS,
        )

    # The three values every branch below cites. They are formatted into the prose and
    # then discarded, and none of them reaches any other published field.
    params = _params(
        site_ph=site.soil_ph,
        crop_ph_min=crop.ph_min,
        crop_ph_max=crop.ph_max,
    )

    if crop.ph_min <= site.soil_ph <= crop.ph_max:
        score = IDEAL_MATCH_SCORE
        strengths.append(f"Tolerates the farm's pH of {site.soil_ph:g}")
        reasons = (Reason(key=CROP_PH_WITHIN_RANGE, params=params),)
    else:
        distance = min(abs(site.soil_ph - crop.ph_min), abs(site.soil_ph - crop.ph_max))
        score = clamp(
            IDEAL_MATCH_SCORE - distance * PH_PENALTY_PER_UNIT,
            MINIMUM_COMPONENT_SCORE,
            IDEAL_MATCH_SCORE,
        )
        considerations.append(
            f"Prefers pH {crop.ph_min:g}-{crop.ph_max:g}; farm reads {site.soil_ph:g}"
        )
        reasons = (Reason(key=CROP_PH_OUTSIDE_RANGE, params=params),)

    return (
        factor("ph_match", "pH match", score, WEIGHT_PH, f"Soil pH {site.soil_ph:g}."),
        strengths,
        considerations,
        reasons,
    )


def _temperature_component(
    crop: CropParameters, site: SiteConditions
) -> tuple[ScoredFactor, list[str], list[str], tuple[Reason, ...]]:
    strengths: list[str] = []
    considerations: list[str] = []

    if site.mean_temp_c is None:
        return (
            unknown_factor("temperature_match", "Temperature match", ["mean temperature"]),
            strengths,
            considerations,
            NO_REASONS,
        )
    if crop.optimal_temp_min_c is None or crop.optimal_temp_max_c is None:
        return (
            unknown_factor(
                "temperature_match", "Temperature match", ["this crop's optimal temperature band"]
            ),
            strengths,
            considerations,
            NO_REASONS,
        )

    params = _params(
        site_mean_temp_c=site.mean_temp_c,
        crop_optimal_min_c=crop.optimal_temp_min_c,
        crop_optimal_max_c=crop.optimal_temp_max_c,
    )

    if crop.optimal_temp_min_c <= site.mean_temp_c <= crop.optimal_temp_max_c:
        score = IDEAL_MATCH_SCORE
        strengths.append(f"Mean temperature of {site.mean_temp_c:.0f} °C is in its optimal band")
        reasons = (Reason(key=CROP_TEMPERATURE_OPTIMAL, params=params),)
    else:
        distance = min(
            abs(site.mean_temp_c - crop.optimal_temp_min_c),
            abs(site.mean_temp_c - crop.optimal_temp_max_c),
        )
        score = clamp(
            IDEAL_MATCH_SCORE - distance * TEMPERATURE_PENALTY_PER_DEGREE,
            MINIMUM_COMPONENT_SCORE,
            IDEAL_MATCH_SCORE,
        )
        considerations.append(
            f"Optimal range is {crop.optimal_temp_min_c:.0f}-{crop.optimal_temp_max_c:.0f} °C"
        )
        reasons = (Reason(key=CROP_TEMPERATURE_OUTSIDE, params=params),)

    return (
        factor(
            "temperature_match",
            "Temperature match",
            score,
            WEIGHT_TEMPERATURE,
            f"Mean temperature {site.mean_temp_c:.0f} °C.",
        ),
        strengths,
        considerations,
        reasons,
    )


def _texture_component(
    crop: CropParameters, site: SiteConditions
) -> tuple[ScoredFactor, list[str], list[str], tuple[Reason, ...]]:
    strengths: list[str] = []
    considerations: list[str] = []

    if site.texture_class is None:
        return (
            unknown_factor("texture_match", "Texture match", ["soil texture"]),
            strengths,
            considerations,
            NO_REASONS,
        )
    if not crop.preferred_textures:
        return (
            unknown_factor("texture_match", "Texture match", ["this crop's texture preferences"]),
            strengths,
            considerations,
            NO_REASONS,
        )

    # The crop's preferences are a tuple, and `ReasonCode.params` admits scalars only —
    # so they travel as a comma-joined list of the machine codes. Joining the codes
    # rather than the display strings keeps the value translatable: a consumer maps each
    # code to its own word for that texture.
    params = _params(
        site_texture_class=site.texture_class,
        crop_preferred_textures=",".join(crop.preferred_textures),
    )

    readable = site.texture_class.replace("_", " ")
    if site.texture_class in crop.preferred_textures:
        score = TEXTURE_MATCH_SCORE
        strengths.append(f"Suits {readable} soils")
        reasons = (Reason(key=CROP_TEXTURE_MATCH, params=params),)
    else:
        score = TEXTURE_MISMATCH_SCORE
        preferred = ", ".join(t.replace("_", " ") for t in list(crop.preferred_textures)[:2])
        considerations.append(f"Prefers {preferred}")
        reasons = (Reason(key=CROP_TEXTURE_MISMATCH, params=params),)

    return (
        factor("texture_match", "Texture match", score, WEIGHT_TEXTURE, f"{readable} soil."),
        strengths,
        considerations,
        reasons,
    )


def _water_component(
    crop: CropParameters, site: SiteConditions
) -> tuple[ScoredFactor, list[str], list[str], tuple[Reason, ...]]:
    strengths: list[str] = []
    considerations: list[str] = []

    if site.seasonal_rainfall_mm is None:
        return (
            unknown_factor("water_match", "Water availability", ["rainfall"]),
            strengths,
            considerations,
            NO_REASONS,
        )
    if not crop.water_need_mm_season:
        return (
            unknown_factor(
                "water_match", "Water availability", ["this crop's seasonal water requirement"]
            ),
            strengths,
            considerations,
            NO_REASONS,
        )

    params = _params(
        seasonal_rainfall_mm=site.seasonal_rainfall_mm,
        crop_water_need_mm_season=crop.water_need_mm_season,
    )

    ratio = site.seasonal_rainfall_mm / crop.water_need_mm_season
    score = clamp(100.0 - abs(1 - ratio) * WATER_PENALTY_PER_RATIO, MINIMUM_COMPONENT_SCORE, 100.0)
    reasons: tuple[Reason, ...] = NO_REASONS
    if ratio < WATER_SHORTFALL_RATIO:
        considerations.append(
            f"Needs about {crop.water_need_mm_season:.0f} mm/season; observed rainfall is "
            f"around {site.seasonal_rainfall_mm:.0f} mm"
        )
        reasons = (Reason(key=CROP_WATER_SHORTFALL, params=params),)
    elif ratio >= 1.0:
        strengths.append("Observed rainfall covers its seasonal water requirement")
        reasons = (Reason(key=CROP_WATER_SUFFICIENT, params=params),)
    # Between the shortfall threshold and parity the existing code says nothing — enough
    # rain to be worth no warning, not enough to be worth a claim. The structured form
    # mirrors that silence rather than inventing a verdict the prose declines to give.

    return (
        factor(
            "water_match",
            "Water availability",
            score,
            WEIGHT_WATER,
            f"Seasonal rainfall around {site.seasonal_rainfall_mm:.0f} mm.",
        ),
        strengths,
        considerations,
        reasons,
    )


def score_crop(
    crop: CropParameters, site: SiteConditions
) -> tuple[
    float | None,
    tuple[ScoredFactor, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[Reason, ...],
]:
    """Suitability for one crop, or `None` when nothing about it could be assessed.

    The composite renormalises over the components that carried evidence, so a site
    with no soil survey is still ranked on climate rather than being scored against
    invented soil values.

    Reasons are collected alongside the prose, in component order, so the structured and
    written forms of the same finding always agree.
    """
    components = [
        _ph_component(crop, site),
        _temperature_component(crop, site),
        _texture_component(crop, site),
        _water_component(crop, site),
    ]

    factors = tuple(component[0] for component in components)
    strengths = tuple(s for component in components for s in component[1])
    considerations = tuple(c for component in components for c in component[2])
    reasons = tuple(r for component in components for r in component[3])

    assessed = [f for f in factors if f.weight > 0]
    if not assessed:
        return None, factors, strengths, considerations, reasons

    total_weight = sum(f.weight for f in assessed)
    composite = sum(f.score * f.weight for f in assessed) / total_weight
    return composite, factors, strengths, considerations, reasons


def crop_recommendations(
    context: AnalysisContext, *, limit: int = DEFAULT_CROP_LIMIT
) -> tuple[CropSuggestion, ...]:
    """Rank the catalog for this site, best first.

    An empty catalog yields no suggestions rather than an error: the engine proposes
    only what it was given, which is what makes the database-backed catalog work
    without the engine knowing a database exists.
    """
    site = site_conditions(context)
    current_code = context.crop.code

    scored: list[tuple[float, CropParameters, tuple, tuple, tuple, tuple]] = []
    for crop in context.catalog:
        if not crop.code:
            continue
        composite, factors, strengths, considerations, reasons = score_crop(crop, site)
        if composite is None:
            # Nothing about this crop could be compared against this site. Ranking it
            # would be ordering by nothing.
            continue
        scored.append((composite, crop, factors, strengths, considerations, reasons))

    # Score descending, then code ascending, so ties never reorder between identical
    # requests and the ordering does not depend on catalog insertion order.
    scored.sort(key=lambda entry: (-entry[0], entry[1].code or ""))

    return tuple(
        CropSuggestion(
            code=crop.code or "",
            name=crop.name or crop.code or "",
            category=crop.category,
            season=crop.season,
            score=score,
            rank=index,
            is_current_crop=crop.code == current_code,
            water_requirement_mm=crop.water_need_mm_season,
            planting_window=(
                f"{crop.season.replace('_', ' ').title()} window" if crop.season else None
            ),
            strengths=strengths or ("No standout advantages at this site",),
            considerations=considerations or ("No significant constraints identified",),
            factors=factors,
            rationale=(
                f"{crop.name or crop.code} scores {score:.0f}/100 against this farm's "
                "soil and climate."
            ),
            reasons=reasons,
        )
        for index, (score, crop, factors, strengths, considerations, reasons) in enumerate(
            scored[:limit], start=1
        )
    )


# --------------------------------------------------------------------------
# Regenerative practices
# --------------------------------------------------------------------------

#: Below this, organic carbon is low enough that carbon-building practices lead.
LOW_ORGANIC_CARBON_PCT = 1.5

#: Sand fraction above which erosion and droughtiness dominate management.
SANDY_THRESHOLD_PCT = 55.0

#: Bulk density above which compaction is limiting root growth.
COMPACTION_THRESHOLD_KG_DM3 = 1.5

#: Cation exchange below which the soil holds nutrients poorly.
LOW_CEC_CMOL_KG = 12.0

#: pH below which acidity is the binding constraint.
ACIDITY_THRESHOLD_PH = 5.5

#: A signal that is present but not binding still counts for something — these
#: practices are good agronomy generally, not only where a problem exists.
STRONG_SIGNAL = 100.0

#: Points added to a risk score so a moderate risk still registers as a driver.
RISK_SIGNAL_OFFSET = 20.0

#: Relevance points per forecast day of high wind.
WIND_SIGNAL_PER_DAY = 30.0

#: A farm already managed regeneratively has done much of this; the advice is still
#: sound but less urgent.
REGENERATIVE_PRACTICE_DISCOUNT = 10.0

DEFAULT_PRACTICE_LIMIT = 5

MAX_PRACTICE_CONSIDERATIONS = 2


@dataclass(frozen=True, slots=True)
class PracticeSuggestion:
    """One ranked regenerative practice."""

    code: str
    name: str
    rank: int
    relevance: float
    description: str
    benefits: tuple[str, ...]
    carbon_impact: str | None
    water_impact: str | None
    steps: tuple[str, ...]
    effort: str | None
    time_to_benefit: str | None
    considerations: tuple[str, ...]
    rationale: str


#: The practice set, with the site signals each one addresses.
#:
#: Agronomy content held as data rather than branching code, so a practice can be added
#: or reweighted without touching the ranking. It reads the same way in every field on
#: Earth: the signals are soil and weather measurements, never a place.
PRACTICES: tuple[dict[str, Any], ...] = (
    {
        "code": "cover_cropping",
        "name": "Cover cropping",
        "description": (
            "Keep living roots in the soil between cash crops using legume or grass covers."
        ),
        "benefits": ("Builds soil organic carbon", "Reduces erosion", "Suppresses weeds"),
        "carbon": "moderate increase over 3-5 seasons",
        "water": "improves infiltration and reduces surface runoff",
        "steps": (
            "Select a cover mix suited to the fallow window",
            "Drill or broadcast immediately after harvest",
            "Terminate two to three weeks before the next planting",
        ),
        "effort": "moderate",
        "time_to_benefit": "1-2 seasons",
        "targets": {"low_carbon": 30, "erosion": 20},
    },
    {
        "code": "reduced_tillage",
        "name": "Reduced or no-till",
        "description": (
            "Minimise soil disturbance so aggregates, fungal networks and residue cover "
            "stay intact."
        ),
        "benefits": ("Protects soil structure", "Retains moisture", "Cuts fuel use"),
        "carbon": "slow but durable increase",
        "water": "notably higher water retention on light soils",
        "steps": (
            "Start with a single field to build confidence",
            "Adjust the planter for residue",
            "Pair with a cover crop to manage weeds",
        ),
        "effort": "high",
        "time_to_benefit": "2-4 seasons",
        "targets": {"low_carbon": 25, "compaction": 25},
    },
    {
        "code": "compost_application",
        "name": "Compost and organic amendment",
        "description": (
            "Apply well-finished compost or manure to raise organic matter and feed soil biology."
        ),
        "benefits": (
            "Raises organic carbon quickly",
            "Improves nutrient holding",
            "Buffers pH",
        ),
        "carbon": "fast increase where application rates are sustained",
        "water": "raises available water capacity",
        "steps": (
            "Test the amendment for maturity",
            "Apply 5-10 t/ha before land preparation",
            "Incorporate shallowly to limit nitrogen loss",
        ),
        "effort": "moderate",
        "time_to_benefit": "1 season",
        "targets": {"low_carbon": 35, "low_cec": 20},
    },
    {
        "code": "crop_rotation",
        "name": "Diverse crop rotation",
        "description": (
            "Alternate crop families across seasons, including a legume, to break pest cycles."
        ),
        "benefits": (
            "Breaks disease and pest cycles",
            "Fixes nitrogen",
            "Spreads market risk",
        ),
        "carbon": "modest increase",
        "water": "varied rooting depths improve profile use",
        "steps": (
            "Plan a three-season sequence",
            "Include at least one legume",
            "Avoid consecutive seasons of the same family",
        ),
        "effort": "low",
        "time_to_benefit": "1-2 seasons",
        "targets": {"disease": 30, "low_carbon": 10},
    },
    {
        "code": "mulching",
        "name": "Surface mulching",
        "description": "Cover bare soil with crop residue or organic mulch to cut evaporation.",
        "benefits": (
            "Cuts evaporative loss",
            "Moderates soil temperature",
            "Suppresses weeds",
        ),
        "carbon": "gradual increase as mulch breaks down",
        "water": "strong reduction in evaporative loss",
        "steps": (
            "Retain residue rather than burning it",
            "Target 30% or more ground cover",
            "Top up before the dry period",
        ),
        "effort": "low",
        "time_to_benefit": "immediate",
        "targets": {"water_stress": 35, "sandy": 20},
    },
    {
        "code": "agroforestry",
        "name": "Agroforestry and windbreaks",
        "description": "Integrate trees or shrubs into field margins and alleys.",
        "benefits": (
            "Long-term carbon storage",
            "Wind protection",
            "Additional income",
        ),
        "carbon": "large increase over 5-10 years",
        "water": "reduces wind-driven evaporation",
        "steps": (
            "Map field margins and prevailing wind",
            "Select species suited to the local rainfall",
            "Protect seedlings for the first two seasons",
        ),
        "effort": "high",
        "time_to_benefit": "3-5 seasons",
        "targets": {"wind": 35, "erosion": 25},
    },
    {
        "code": "liming",
        "name": "Targeted liming",
        "description": "Raise pH on acidic ground so nutrients become available again.",
        "benefits": (
            "Unlocks phosphorus and molybdenum",
            "Reduces aluminium toxicity",
            "Improves legume nodulation",
        ),
        "carbon": "indirect, through better biomass",
        "water": "no direct effect",
        "steps": (
            "Test pH and buffer capacity before applying",
            "Apply well ahead of planting",
            "Re-test after one season",
        ),
        "effort": "moderate",
        "time_to_benefit": "1-2 seasons",
        "targets": {"acidity": 40},
    },
)


def site_signals(
    context: AnalysisContext,
    soil: SoilAssessmentResult,
    water: WaterAssessment,
    disease: DiseaseAssessment,
    weather: WeatherAssessment,
) -> dict[str, float | None]:
    """How strongly each constraint applies at this site.

    `None` means the measurement behind a signal was not taken. A missing signal drops
    out of the weighting rather than defaulting — an unmeasured soil is not a soil
    without problems, and the practices that address those problems must not be
    demoted for lack of evidence either way.
    """
    soil_point: SoilPoint = context.soil
    carbon = _numeric(soil_point.organic_carbon_pct)
    sand = _numeric(soil_point.sand_pct)
    density = _numeric(soil_point.bulk_density_kg_dm3)
    cec = _numeric(soil_point.cec_cmol_kg)
    ph = _numeric(soil_point.ph)

    def _threshold(value: float | None, limit: float, *, below: bool) -> float | None:
        if value is None:
            return None
        breached = value < limit if below else value > limit
        return STRONG_SIGNAL if breached else STRONG_SIGNAL * 0.35

    return {
        "low_carbon": _threshold(carbon, LOW_ORGANIC_CARBON_PCT, below=True),
        "erosion": _threshold(sand, SANDY_THRESHOLD_PCT, below=False),
        "compaction": _threshold(density, COMPACTION_THRESHOLD_KG_DM3, below=False),
        "low_cec": _threshold(cec, LOW_CEC_CMOL_KG, below=True),
        "sandy": _threshold(sand, SANDY_THRESHOLD_PCT, below=False),
        "acidity": _threshold(ph, ACIDITY_THRESHOLD_PH, below=True),
        # Risk signals come from assessments that know whether they had evidence, so an
        # unassessed section reports None rather than the zero its score would imply.
        "disease": (
            min(STRONG_SIGNAL, disease.score + RISK_SIGNAL_OFFSET) if disease.sufficient else None
        ),
        "water_stress": (
            min(STRONG_SIGNAL, water.score + RISK_SIGNAL_OFFSET) if water.sufficient else None
        ),
        "wind": (
            min(STRONG_SIGNAL, weather.high_wind_days * WIND_SIGNAL_PER_DAY + RISK_SIGNAL_OFFSET)
            if weather.factors
            else None
        ),
    }


def score_practice(
    practice: dict[str, Any], signals: dict[str, float | None], farming_practice: str | None
) -> float | None:
    """Relevance of one practice, or `None` when none of its signals were measured."""
    targets: dict[str, int] = practice["targets"]

    known = {
        key: (signals[key], weight)
        for key, weight in targets.items()
        if signals.get(key) is not None
    }
    if not known:
        return None

    total_weight = sum(weight for _, weight in known.values())
    relevance = sum(value * weight for value, weight in known.values()) / total_weight

    if farming_practice == "regenerative":
        relevance -= REGENERATIVE_PRACTICE_DISCOUNT

    return clamp(relevance, MINIMUM_COMPONENT_SCORE, 100.0)


def regenerative_recommendations(
    context: AnalysisContext,
    soil: SoilAssessmentResult,
    water: WaterAssessment,
    disease: DiseaseAssessment,
    weather: WeatherAssessment,
    *,
    limit: int = DEFAULT_PRACTICE_LIMIT,
) -> tuple[PracticeSuggestion, ...]:
    """Rank regenerative practices against this farm's measured constraints."""
    signals = site_signals(context, soil, water, disease, weather)

    scored: list[tuple[float, dict[str, Any]]] = []
    for practice in PRACTICES:
        relevance = score_practice(practice, signals, context.farming_practice)
        if relevance is None:
            continue
        scored.append((relevance, practice))

    scored.sort(key=lambda entry: (-entry[0], entry[1]["code"]))

    considerations = (
        tuple(soil.limitations[:MAX_PRACTICE_CONSIDERATIONS])
        if soil.sufficient and soil.limitations
        else ("No blocking soil constraints were measured",)
    )

    return tuple(
        PracticeSuggestion(
            code=practice["code"],
            name=practice["name"],
            rank=index,
            relevance=relevance,
            description=practice["description"],
            benefits=tuple(practice["benefits"]),
            carbon_impact=practice["carbon"],
            water_impact=practice["water"],
            steps=tuple(practice["steps"]),
            effort=practice["effort"],
            time_to_benefit=practice["time_to_benefit"],
            considerations=considerations,
            rationale=_practice_rationale(practice, relevance, context.soil, water),
        )
        for index, (relevance, practice) in enumerate(scored[:limit], start=1)
    )


def _practice_rationale(
    practice: dict[str, Any], relevance: float, soil: SoilPoint, water: WaterAssessment
) -> str:
    """Why this practice ranks here, citing only measurements that exist."""
    evidence: list[str] = []
    if soil.organic_carbon_pct is not None:
        evidence.append(f"{soil.organic_carbon_pct:g}% organic carbon")
    if soil.texture_class is not None:
        evidence.append(f"{soil.texture_class.replace('_', ' ')} texture")
    if water.sufficient:
        evidence.append(f"a water risk of {water.score:.0f}/100")

    if not evidence:
        return (
            f"{practice['name']} scores {relevance:.0f}/100 for this farm. "
            f"{INSUFFICIENT}: no soil measurements were available, so the ranking rests "
            "on weather alone."
        )
    return (
        f"{practice['name']} scores {relevance:.0f}/100 for this farm given {', '.join(evidence)}."
    )
