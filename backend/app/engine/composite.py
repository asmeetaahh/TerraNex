"""The overall farm health score, and the advisories that follow from it.

**One rule shapes everything here: a farm is never marked down for what nobody
measured.** Each section contributes only when it was actually assessed, and the
weights renormalise over what remains — so a farm whose soil survey came back empty is
scored on its weather, water, disease and canopy, at full marks if those are good. The
gap is reported separately, in `unassessed`, rather than smuggled into the number as a
zero.

The advisories follow the same discipline in a sharper form. Every draft must cite
evidence the engines actually produced: a threshold one of them applied, a pathogen
whose rule matched, a depletion figure the water balance computed. Nothing is advised
about a condition that was not assessed, and no claim is made that no engine supports.

Pure: no I/O, no clock, no randomness. Identifiers and timestamps are attached by the
service layer, because those are the two things this module must not invent.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.engine.context import AnalysisContext
from app.engine.disease import DiseaseAssessment
from app.engine.reasons import (
    WATER_IRRIGATION_DEFICIT,
    WEATHER_COLD_THRESHOLD_EXCEEDED,
    WEATHER_HEAT_THRESHOLD_EXCEEDED,
    ParamValue,
    Reason,
)
from app.engine.scoring import band_for, clamp, risk_level_for
from app.engine.soil import SoilAssessmentResult
from app.engine.vegetation import VegetationAssessment
from app.engine.water import WaterAssessment
from app.engine.weather import WeatherAssessment
from app.schemas.common import ScoreBand, ScoredFactor
from app.schemas.enums import AdvisoryCategory, AdvisoryPriority

# --------------------------------------------------------------------------
# Weights
# --------------------------------------------------------------------------

#: Contribution of each section to the overall score, summing to one when everything is
#: assessed. Water leads because it is the constraint a farmer can most often act on
#: within a week; canopy health trails because it is a lagging indicator — by the time
#: NDVI falls, the cause has already happened and is visible in the other four.
WEIGHT_WEATHER = 0.20
WEIGHT_WATER = 0.25
WEIGHT_DISEASE = 0.20
WEIGHT_SOIL = 0.20
WEIGHT_CROP_HEALTH = 0.15

#: Section score at or below which its risk is worth advising on. Matches the
#: `low` -> `moderate` boundary in `scoring.risk_level_for`, so an advisory appears
#: exactly when the reported level stops being low.
ADVISORY_RISK_THRESHOLD = 28.0

#: Risk scores at which an advisory escalates.
HIGH_RISK_SCORE = 50.0
CRITICAL_RISK_SCORE = 70.0

#: Forecast days of heat before it is worth a separate advisory.
HEAT_ADVISORY_DAYS = 2
HEAT_ESCALATION_DAYS = 4

#: At most this many soil constraints are raised at once; beyond that the list stops
#: being a call to action and becomes a report.
MAX_SOIL_ADVISORIES = 2

_PRIORITY_ORDER = {
    AdvisoryPriority.critical: 0,
    AdvisoryPriority.high: 1,
    AdvisoryPriority.medium: 2,
    AdvisoryPriority.low: 3,
}


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompositeAssessment:
    """The weighted overall score and its breakdown."""

    score: float
    band: ScoreBand
    factors: tuple[ScoredFactor, ...] = field(default=())
    unassessed: tuple[str, ...] = ()
    assessed_weight: float = 0.0


@dataclass(frozen=True, slots=True)
class AdvisoryDraft:
    """One advisory, minus the identity and timestamp the service attaches.

    Deliberately not an `Advisory`: constructing one needs a UUID and a clock, and
    neither belongs in a pure engine. The service adds both.
    """

    category: AdvisoryCategory
    priority: AdvisoryPriority
    title: str
    body: str
    rationale: str
    action_window: str | None
    confidence: float

    #: The numbers the prose above was formatted from, kept as data.
    #:
    #: Populated only where the evidence reaches no published field: irrigation, heat and
    #: cold. A disease advisory cites the rule that `disease_risk.risks[].reasons` already
    #: publishes, and a soil advisory cites `limitations`/`score`/`band` on
    #: `soil_assessment` — restating either here would duplicate facts rather than
    #: recover them, and give the two copies a chance to disagree.
    reasons: tuple[Reason, ...] = ()


# --------------------------------------------------------------------------
# Section handling
# --------------------------------------------------------------------------


def is_assessed(factors: Sequence[ScoredFactor]) -> bool:
    """Whether a section had enough evidence to contribute.

    Requires *every* sub-factor to carry weight. Partial evidence must not move the
    overall score: water risk still knows the farm's irrigation type when rainfall is
    missing, and letting that alone carry the section would imply a balance nobody
    calculated.
    """
    return bool(factors) and all(f.weight > 0 for f in factors)


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
    if not assessed:
        return ScoredFactor(
            key=key,
            label=label,
            score=0.0,
            weight=0.0,
            band=ScoreBand.moderate,
            explanation=explanation,
        )
    bounded = clamp(score, 0.0, 100.0)
    return ScoredFactor(
        key=key,
        label=label,
        score=bounded,
        weight=weight,
        band=band,
        explanation=explanation,
    )


def _as_health(risk_score: float) -> float:
    """Turn a risk score into a health score.

    The three risk sections run high-is-bad and the two condition sections run
    high-is-good. Inverting here rather than at each call site is what stops one of
    them being averaged in the wrong direction.
    """
    return clamp(100.0 - risk_score, 0.0, 100.0)


def evaluate(
    context: AnalysisContext,
    weather: WeatherAssessment,
    water: WaterAssessment,
    disease: DiseaseAssessment,
    soil: SoilAssessmentResult,
    vegetation: VegetationAssessment,
) -> CompositeAssessment:
    """Combine the five sections into one explainable score."""
    weather_health = _as_health(weather.score)
    water_health = _as_health(water.score)
    disease_health = _as_health(disease.score)

    factors = (
        _section(
            "weather_risk",
            "Weather risk",
            weather_health,
            band_for(weather_health),
            weather.explanation,
            WEIGHT_WEATHER,
            is_assessed(weather.factors),
        ),
        _section(
            "water_risk",
            "Water availability",
            water_health,
            band_for(water_health),
            water.explanation,
            WEIGHT_WATER,
            is_assessed(water.factors),
        ),
        _section(
            "disease_risk",
            "Disease pressure",
            disease_health,
            band_for(disease_health),
            disease.explanation,
            WEIGHT_DISEASE,
            is_assessed(disease.factors),
        ),
        _section(
            "soil_suitability",
            "Soil suitability",
            soil.score,
            soil.band,
            soil.explanation,
            WEIGHT_SOIL,
            # Previously hardcoded as always assessed, which was true only while soil
            # was simulated and therefore never absent. A real survey can come back
            # empty, and crediting an unmeasured soil would be as wrong as penalising
            # one.
            is_assessed(soil.factors),
        ),
        _section(
            "crop_health",
            "Crop health",
            vegetation.score,
            vegetation.band,
            vegetation.explanation,
            WEIGHT_CROP_HEALTH,
            is_assessed(vegetation.factors),
        ),
    )

    # The denominator is the weight of what was actually measured, so the remaining
    # sections keep their relative importance and a missing one costs nothing.
    assessed_weight = sum(f.weight for f in factors)
    overall = (
        sum(f.score * f.weight for f in factors) / assessed_weight if assessed_weight > 0 else 0.0
    )

    return CompositeAssessment(
        score=overall,
        band=band_for(overall),
        factors=factors,
        unassessed=tuple(f.label for f in factors if f.weight == 0.0),
        assessed_weight=assessed_weight,
    )


# --------------------------------------------------------------------------
# Advisories
# --------------------------------------------------------------------------


def _priority_for(score: float) -> AdvisoryPriority:
    if score >= CRITICAL_RISK_SCORE:
        return AdvisoryPriority.critical
    if score >= HIGH_RISK_SCORE:
        return AdvisoryPriority.high
    return AdvisoryPriority.medium


def _params(**values: ParamValue) -> dict[str, ParamValue]:
    """Drop absent values rather than publishing them as null.

    `null` reads as *measured, and unbounded* — a rainfed farm has no application
    efficiency at all, which is a different statement from one whose efficiency is
    unknown. Omitting keeps the two apart, and matches how the disease codes already
    handle a rule clause that carries no bound.
    """
    return {name: value for name, value in values.items() if value is not None}


def _irrigation_advisory(water: WaterAssessment) -> AdvisoryDraft | None:
    """Advise irrigation only when a balance was actually computed.

    Every number in the rationale comes from the FAO-56 run: the depletion it tracked,
    the reservoir it derived from texture and rooting depth, and the depth it would
    take to refill — grossed up by the farm's own application efficiency.
    """
    if not water.sufficient or water.score < ADVISORY_RISK_THRESHOLD:
        return None
    if water.applied_irrigation_mm <= 0:
        return None

    efficiency = water.application_efficiency
    delivery = (
        f" at about {efficiency * 100:.0f}% application efficiency"
        if efficiency is not None
        else " by whatever means are available, as the farm is rainfed"
    )
    approximation = (
        ""
        if water.parameters_source == "crop"
        else " Crop coefficients are approximate for this crop, so treat the depth as indicative."
    )

    return AdvisoryDraft(
        category=AdvisoryCategory.irrigation,
        priority=_priority_for(water.score),
        title=f"Apply about {water.applied_irrigation_mm:.0f} mm of irrigation",
        body=(
            f"The root zone has drawn down past the point where the crop can take water "
            f"freely. Applying this depth{delivery} would return it to field capacity."
            f"{approximation}"
        ),
        rationale=(
            f"{water.depletion_mm:.0f} mm depleted from a {water.taw_mm:.0f} mm root zone, "
            f"past the {water.raw_mm:.0f} mm readily-available threshold."
        ),
        action_window=(
            "within 48 hours" if water.score >= HIGH_RISK_SCORE else "within the next week"
        ),
        confidence=0.75,
        reasons=(
            Reason(
                key=WATER_IRRIGATION_DEFICIT,
                # Read straight off the assessment, unrounded. The prose rounds for
                # readability; a consumer formatting for its own locale should start
                # from the value the engine computed, not from a rounded copy.
                params=_params(
                    applied_irrigation_mm=water.applied_irrigation_mm,
                    depletion_mm=water.depletion_mm,
                    taw_mm=water.taw_mm,
                    raw_mm=water.raw_mm,
                    # Absent for a rainfed farm, which has no application efficiency
                    # rather than an unknown one.
                    application_efficiency=efficiency,
                    parameters_source=water.parameters_source,
                ),
            ),
        ),
    )


def _disease_advisory(disease: DiseaseAssessment) -> AdvisoryDraft | None:
    """Advise scouting only for a pathogen whose rule actually matched.

    The behaviour this replaces fell back to a generic "foliar disease" whenever the
    score was high and the list was empty — a diagnosis with nothing behind it. No
    matched rule now means no advisory.
    """
    if not disease.sufficient or not disease.items:
        return None

    leader = disease.items[0]
    named = f"{leader.name} ({leader.pathogen})" if leader.pathogen else leader.name
    trigger = leader.triggering_conditions[0] if leader.triggering_conditions else ""

    return AdvisoryDraft(
        category=AdvisoryCategory.disease,
        priority=(
            AdvisoryPriority.high if disease.score >= HIGH_RISK_SCORE else AdvisoryPriority.medium
        ),
        title=f"Scout for {leader.name.lower()} this week",
        body=(
            (leader.scouting_advice or "Inspect the crop for early symptoms.")
            + " Scouting now is far cheaper than treating an established outbreak."
        ),
        rationale=(
            f"{named} met its infection requirement: {trigger}."
            if trigger
            else f"{named} met its infection requirement over {leader.matched_hours} hours."
        ),
        action_window="within 3 days",
        confidence=round(min(0.9, 0.5 + leader.probability * 0.4), 2),
    )


def _heat_advisory(weather: WeatherAssessment) -> AdvisoryDraft | None:
    """Advise on heat against the threshold the engine actually applied."""
    if weather.heat_threshold_c is None or weather.heat_stress_days < HEAT_ADVISORY_DAYS:
        return None

    against = (
        "this crop's optimal maximum"
        if weather.thresholds_source == "crop"
        else "a generic threshold, as no crop is planted"
    )

    return AdvisoryDraft(
        category=AdvisoryCategory.weather,
        priority=(
            AdvisoryPriority.high
            if weather.heat_stress_days >= HEAT_ESCALATION_DAYS
            else AdvisoryPriority.medium
        ),
        title=f"Prepare for {weather.heat_stress_days} days of heat stress",
        body="Shift irrigation to early morning and avoid canopy operations during peak heat.",
        rationale=(
            f"{weather.heat_stress_days} forecast day(s) exceed "
            f"{weather.heat_threshold_c:.0f} °C, {against}."
        ),
        action_window="this week",
        confidence=0.68,
        reasons=(
            Reason(
                key=WEATHER_HEAT_THRESHOLD_EXCEEDED,
                params=_params(
                    heat_stress_days=weather.heat_stress_days,
                    heat_threshold_c=weather.heat_threshold_c,
                    thresholds_source=weather.thresholds_source,
                ),
            ),
        ),
    )


def _frost_advisory(weather: WeatherAssessment) -> AdvisoryDraft | None:
    """Advise on cold against the crop's own damage threshold.

    The behaviour this replaces asserted a flat "below 2 °C" regardless of crop, which
    was wrong in both directions: it understated the risk to a tropical crop injured at
    five degrees, and overstated it for a temperate one that tolerates frost.
    """
    if weather.cold_threshold_c is None or weather.frost_risk_days <= 0:
        return None

    qualifier = (
        "this crop's cold-damage threshold"
        if weather.thresholds_source == "crop"
        else "the frost advisory threshold"
    )

    return AdvisoryDraft(
        category=AdvisoryCategory.weather,
        priority=AdvisoryPriority.critical,
        title=f"Cold damage expected on {weather.frost_risk_days} day(s)",
        body="Consider frost protection for sensitive growth stages.",
        rationale=(
            f"{weather.frost_risk_days} forecast day(s) fall below "
            f"{weather.cold_threshold_c:.0f} °C, {qualifier}."
        ),
        action_window="within 48 hours",
        confidence=0.72,
        reasons=(
            Reason(
                key=WEATHER_COLD_THRESHOLD_EXCEEDED,
                params=_params(
                    frost_risk_days=weather.frost_risk_days,
                    cold_threshold_c=weather.cold_threshold_c,
                    thresholds_source=weather.thresholds_source,
                ),
            ),
        ),
    )


#: `Advisory.title` is capped by the schema; a constraint longer than this is trimmed
#: rather than rejected.
MAX_TITLE_LENGTH = 200


def _soil_advisories(soil: SoilAssessmentResult) -> list[AdvisoryDraft]:
    """One advisory per measured soil constraint, and none when nothing was measured.

    Each carries the constraint itself as its title. A shared generic heading made two
    soil advisories indistinguishable in a prioritised list — same title, same
    rationale, with the only difference buried in the body — so the specific finding is
    promoted to where a reader actually scans.
    """
    if not soil.sufficient:
        return []

    drafts: list[AdvisoryDraft] = []
    for limitation in soil.limitations[:MAX_SOIL_ADVISORIES]:
        title = limitation if len(limitation) <= MAX_TITLE_LENGTH else limitation[:197] + "..."
        drafts.append(
            AdvisoryDraft(
                category=AdvisoryCategory.soil,
                priority=AdvisoryPriority.medium,
                title=title,
                body=(
                    f"{limitation}. Correcting this raises the ceiling on every other intervention."
                ),
                rationale=(
                    f"Soil suitability scored {soil.score:.0f}/100 ({soil.band.value}) "
                    f"against this crop's requirements."
                ),
                action_window="this season",
                confidence=0.6,
            )
        )
    return drafts


def _all_clear_advisory(
    weather: WeatherAssessment,
    water: WaterAssessment,
    disease: DiseaseAssessment,
    composite: CompositeAssessment,
) -> AdvisoryDraft:
    """What to say when nothing crossed a threshold.

    Careful to claim only what was checked: with sections unassessed, a quiet week is
    quiet for the evidence available, and the advisory says which evidence was missing
    rather than implying an all-clear nobody verified.
    """
    caveat = (
        f" Not assessed: {', '.join(composite.unassessed).lower()}." if composite.unassessed else ""
    )
    return AdvisoryDraft(
        category=AdvisoryCategory.planting,
        priority=AdvisoryPriority.low,
        title="Conditions are stable — maintain the current plan",
        body=f"No risk threshold was crossed over the assessed window.{caveat}",
        rationale=(
            f"Weather risk {weather.score:.0f}/100, water {water.score:.0f}/100, "
            f"disease {disease.score:.0f}/100."
        ),
        action_window=None,
        confidence=0.55,
    )


def derive_advisories(
    context: AnalysisContext,
    weather: WeatherAssessment,
    water: WaterAssessment,
    disease: DiseaseAssessment,
    soil: SoilAssessmentResult,
    composite: CompositeAssessment,
) -> tuple[AdvisoryDraft, ...]:
    """Advisory drafts, each citing evidence one of the engines produced.

    Ordered by priority. The sort is stable, so within a priority the order is the
    order the rules are written in — deterministic, and reviewable as a sequence rather
    than depending on score ties.
    """
    drafts: list[AdvisoryDraft] = []

    for candidate in (
        _frost_advisory(weather),
        _irrigation_advisory(water),
        _disease_advisory(disease),
        _heat_advisory(weather),
    ):
        if candidate is not None:
            drafts.append(candidate)

    drafts.extend(_soil_advisories(soil))

    if not drafts:
        drafts.append(_all_clear_advisory(weather, water, disease, composite))

    drafts.sort(key=lambda draft: _PRIORITY_ORDER[draft.priority])
    return tuple(drafts)


def overall_level(score: float) -> str:
    """The composite expressed on the risk scale, for narrative use."""
    return risk_level_for(_as_health(score)).value
