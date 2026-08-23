"""Disease pressure, from infection rules matched against the hourly series.

**Duration is the mechanism.** A pathogen needs a temperature band and a humidity
threshold to hold for a stated number of unbroken hours. A daily mean cannot express
that: a day averaging 70% humidity can contain a twelve-hour night above 95%, which is
an infection period, while a day averaging 80% with no sustained run is not. So every
clause here is matched against hourly observations and never against a daily summary.

**Nothing is invented.** Three properties enforce that, and each has a test naming the
defect it replaces:

* A pathogen is named only when a rule *for the planted crop* matched. The assessment
  this replaces reported a generic leaf spot for farms with nothing planted.
* Probability is derived from the hours actually measured, between the rule's threshold
  and its saturation. The old scoring was `score - index * 12 + random jitter`, so the
  first pathogen listed was always the likeliest and no two runs agreed.
* An hour with no reading breaks a run rather than bridging it. Treating unknown as
  "conditions held" would manufacture an infection period out of a provider outage.

Pure: no I/O, no clock, no randomness. Every value comes from the context.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.engine.context import AnalysisContext, DiseaseRule, HourlyPoint, RuleCondition
from app.engine.reasons import DISEASE_CONDITION_KEYS, Reason
from app.engine.scoring import INSUFFICIENT, factor, risk_level_for, unknown_factor
from app.schemas.common import RiskLevel, ScoredFactor

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: Likelihood reported when a rule's requirement is exactly met.
#:
#: Not 1.0 and not near zero: meeting the threshold means the weather permitted
#: infection, not that infection occurred. Halfway is the honest reading of "conditions
#: were sufficient", and it rises from here with every further measured hour.
THRESHOLD_PROBABILITY = 0.5

#: Relative humidity at which leaf wetness is assumed for the aggregate factor. Rules
#: carry their own thresholds; this one only describes the week.
HUMID_HOUR_PCT = 90.0

#: The temperature band most foliar pathogens in the shipped set operate within, used
#: for the summary factor rather than for any individual rule.
INFECTION_BAND_C = (15.0, 30.0)

WEIGHT_HUMIDITY = 0.5
WEIGHT_INFECTION_WINDOW = 0.5


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiseaseItem:
    """One pathogen whose rule matched."""

    rule_id: str
    name: str
    pathogen: str | None
    crop_code: str | None
    probability: float
    level: RiskLevel
    matched_hours: int
    triggering_conditions: tuple[str, ...]
    preventive_actions: tuple[str, ...]
    scouting_advice: str | None

    #: The same evidence as `triggering_conditions`, as data rather than sentences.
    #:
    #: One reason per matched clause, carrying the numbers the sentence was built from —
    #: nothing derived, nothing new. It exists so a consumer can state *why* in a
    #: language this module does not speak.
    reasons: tuple[Reason, ...] = ()


@dataclass(frozen=True, slots=True)
class DiseaseAssessment:
    """Everything the disease assessment determined."""

    sufficient: bool
    score: float
    level: RiskLevel

    items: tuple[DiseaseItem, ...] = ()
    humid_hours: int = 0
    infection_window_hours: int = 0
    observed_hours: int = 0

    missing: tuple[str, ...] = ()
    factors: tuple[ScoredFactor, ...] = field(default=())
    conditions_summary: str = ""
    explanation: str = ""


# --------------------------------------------------------------------------
# Reading hours
# --------------------------------------------------------------------------


def reading(hour: HourlyPoint, attr: str) -> float | None:
    """One usable numeric reading, or None.

    `bool` is excluded because it subclasses `int`; a string raises on comparison
    exactly where a `None` would, and both mean unknown.
    """
    value = getattr(hour, attr, None)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _hour_matches(hour: HourlyPoint, condition: RuleCondition) -> bool:
    """Whether one hour satisfies a clause's measurement windows.

    An unreadable measurement is a non-match, never a pass. The distinction matters:
    a run broken by an unknown hour is a run that was not observed to continue.
    """
    for attr, window in (
        ("temperature_c", condition.temp_c),
        ("humidity_pct", condition.humidity_pct),
        ("precipitation_mm", condition.precipitation_mm),
    ):
        if window is None:
            continue
        value = reading(hour, attr)
        if value is None or not window.contains(value):
            return False
    return True


def longest_run(hourly: Sequence[HourlyPoint], condition: RuleCondition) -> int:
    """The longest unbroken run of hours satisfying `condition`."""
    run = longest = 0
    for hour in sorted(hourly, key=lambda h: h.at):
        if _hour_matches(hour, condition):
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return longest


def total_matching(hourly: Sequence[HourlyPoint], condition: RuleCondition) -> int:
    """How many hours satisfy `condition`, consecutive or not."""
    return sum(1 for hour in hourly if _hour_matches(hour, condition))


#: Development order, so `growth_stage_at_least` can compare stages.
STAGE_ORDER: tuple[str, ...] = (
    "not_planted",
    "germination",
    "seedling",
    "vegetative",
    "flowering",
    "fruiting",
    "maturity",
    "harvested",
)


def _stage_reached(current: str | None, required: str) -> bool:
    if current is None or current not in STAGE_ORDER or required not in STAGE_ORDER:
        return False
    return STAGE_ORDER.index(current) >= STAGE_ORDER.index(required)


# --------------------------------------------------------------------------
# Matching a rule
# --------------------------------------------------------------------------


def _describe(condition: RuleCondition, measured: int) -> str:
    parts: list[str] = []
    if condition.temp_c is not None:
        parts.append(condition.temp_c.describe(" °C"))
    if condition.humidity_pct is not None:
        parts.append(f"relative humidity {condition.humidity_pct.describe('%')}")
    if condition.precipitation_mm is not None:
        parts.append(f"rainfall {condition.precipitation_mm.describe(' mm')}")

    window = " with ".join(parts) if parts else "the required conditions"
    if condition.type == "consecutive_hours":
        return f"{measured} consecutive hours at {window}"
    return f"{measured} hours at {window}"


def _rule_reasons(
    rule: DiseaseRule, matched_hours: int, growth_stage: str | None
) -> tuple[Reason, ...]:
    """The matched rule's clauses, as structured evidence.

    Called only after `match_rule` returned True, so every clause of this conjunction is
    known to have been satisfied — which is why each one becomes a reason without being
    re-tested here. Re-testing would be a second implementation of the same agronomy and
    a chance for the two to disagree.

    Every value comes from the rule or from the hours the matcher already counted.
    Nothing is derived: a reason states what the engine found, never more.
    """
    reasons: list[Reason] = []

    for condition in rule.conditions:
        key = DISEASE_CONDITION_KEYS.get(condition.type)
        if key is None:
            # An unrecognised clause type yields no reason rather than a fabricated key.
            # A consumer can translate a key it knows; it can do nothing with a guess.
            continue

        params: dict[str, float | int | str | None] = {
            "rule_id": rule.id,
            "pathogen": rule.pathogen,
        }

        if condition.type == "growth_stage_at_least":
            params["required_stage"] = condition.stage
            params["growth_stage"] = growth_stage
        else:
            params["matched_hours"] = matched_hours
            params["required_hours"] = condition.hours
            params["threshold_hours"] = rule.threshold_hours
            params["saturation_hours"] = rule.saturation_hours
            if condition.temp_c is not None:
                params["temp_min_c"] = condition.temp_c.low
                params["temp_max_c"] = condition.temp_c.high
            if condition.humidity_pct is not None:
                params["humidity_min_pct"] = condition.humidity_pct.low
                params["humidity_max_pct"] = condition.humidity_pct.high
            if condition.precipitation_mm is not None:
                params["precipitation_min_mm"] = condition.precipitation_mm.low
                params["precipitation_max_mm"] = condition.precipitation_mm.high

        reasons.append(Reason(key=key, params=params))

    return tuple(reasons)


def match_rule(
    rule: DiseaseRule, hourly: Sequence[HourlyPoint], growth_stage: str | None
) -> tuple[bool, int, tuple[str, ...]]:
    """Whether every clause of `rule` matched, the driving hours, and what matched.

    A rule is a conjunction: one unmet clause is enough to decline. The hours returned
    are those of the longest duration clause, which is what the probability scales on.
    """
    conditions: list[str] = []
    driving_hours = 0

    for condition in rule.conditions:
        if condition.type == "growth_stage_at_least":
            if not _stage_reached(growth_stage, condition.stage or ""):
                return False, 0, ()
            conditions.append(f"crop has reached {(condition.stage or '').replace('_', ' ')}")
            continue

        if condition.type == "consecutive_hours":
            measured = longest_run(hourly, condition)
        elif condition.type == "total_hours":
            measured = total_matching(hourly, condition)
        else:
            # An unrecognised clause cannot be shown to hold, so it does not.
            return False, 0, ()

        if measured < condition.hours:
            return False, 0, ()

        conditions.append(_describe(condition, measured))
        driving_hours = max(driving_hours, measured)

    return True, driving_hours, tuple(conditions)


def probability_for(rule: DiseaseRule, measured_hours: int) -> float:
    """Likelihood from hours actually measured.

    Floor at the threshold, one at saturation, linear between. A degenerate rule whose
    saturation does not exceed its threshold reports the floor rather than dividing by
    zero.
    """
    span = rule.saturation_hours - rule.threshold_hours
    if span <= 0:
        return THRESHOLD_PROBABILITY
    beyond = (measured_hours - rule.threshold_hours) / span
    return min(1.0, THRESHOLD_PROBABILITY + (1.0 - THRESHOLD_PROBABILITY) * max(0.0, beyond))


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def evaluate(context: AnalysisContext) -> DiseaseAssessment:
    """Score disease pressure from the hourly series and the applicable rules."""
    hourly = list(context.hourly)

    humidity_known = any(reading(h, "humidity_pct") is not None for h in hourly)
    temperature_known = any(reading(h, "temperature_c") is not None for h in hourly)

    missing: list[str] = []
    if not humidity_known:
        missing.append("humidity")
    if not temperature_known:
        missing.append("temperature")

    if not hourly or not humidity_known:
        return _insufficient(missing or ["hourly observations"], bool(hourly), temperature_known)

    humid_hours = sum(
        1 for h in hourly if (v := reading(h, "humidity_pct")) is not None and v >= HUMID_HOUR_PCT
    )
    window_hours = sum(
        1
        for h in hourly
        if (t := reading(h, "temperature_c")) is not None
        and INFECTION_BAND_C[0] <= t <= INFECTION_BAND_C[1]
        and (v := reading(h, "humidity_pct")) is not None
        and v >= HUMID_HOUR_PCT
    )

    items: list[DiseaseItem] = []
    for rule in context.disease_rules:
        # Checked here as well as in the adapter that resolves the rules. Naming a
        # potato pathogen over a maize field is the exact failure this engine exists to
        # avoid, so the invariant is enforced where it is relied upon rather than
        # trusted to whoever assembled the context. An unplanted farm has no code, so
        # no rule can match it and no pathogen can be named.
        if context.crop.code is None or context.crop.code not in rule.crops:
            continue

        matched, driving_hours, conditions = match_rule(rule, hourly, context.growth_stage)
        if not matched:
            continue
        probability = probability_for(rule, driving_hours)
        items.append(
            DiseaseItem(
                rule_id=rule.id,
                name=rule.name,
                pathogen=rule.pathogen,
                crop_code=context.crop.code,
                probability=probability,
                level=risk_level_for(probability * 100),
                matched_hours=driving_hours,
                triggering_conditions=conditions,
                preventive_actions=rule.preventive_actions,
                scouting_advice=rule.scouting_advice,
                reasons=_rule_reasons(rule, driving_hours, context.growth_stage),
            )
        )

    # Ranked by likelihood, never by the order rules happen to be written in.
    items.sort(key=lambda item: (item.probability, item.rule_id), reverse=True)

    score = _combined_score(items)

    return DiseaseAssessment(
        sufficient=True,
        score=score,
        level=risk_level_for(score),
        items=tuple(items),
        humid_hours=humid_hours,
        infection_window_hours=window_hours,
        observed_hours=len(hourly),
        missing=tuple(missing),
        factors=_factors(humid_hours, window_hours, len(hourly), temperature_known, missing),
        conditions_summary=_summary(context, humid_hours, window_hours, len(hourly), items),
        explanation=_explanation(score, items),
    )


def _combined_score(items: Sequence[DiseaseItem]) -> float:
    """Pressure from every active pathogen, combined as independent events.

    `1 - prod(1 - p)` — a second pathogen raises pressure without the total ever
    exceeding 100, which simple addition would.
    """
    if not items:
        return 0.0
    survives = 1.0
    for item in items:
        survives *= 1.0 - item.probability
    return (1.0 - survives) * 100.0


def _insufficient(
    missing: Sequence[str], has_hours: bool, temperature_known: bool
) -> DiseaseAssessment:
    listed = ", ".join(missing)
    note = f"{INSUFFICIENT}: {listed} unavailable, so infection conditions could not be evaluated."
    factors = [unknown_factor("humidity_hours", "Humidity exposure", ["humidity"])]
    if temperature_known and has_hours:
        factors.append(
            unknown_factor("infection_window", "Infection temperature window", ["humidity"])
        )
    else:
        factors.append(
            unknown_factor(
                "infection_window", "Infection temperature window", list(missing) or ["humidity"]
            )
        )

    return DiseaseAssessment(
        sufficient=False,
        score=0.0,
        level=RiskLevel.low,
        missing=tuple(missing),
        factors=tuple(factors),
        conditions_summary=note,
        explanation=(
            f"{INSUFFICIENT}: {listed} unavailable from the weather provider, so disease "
            "pressure could not be assessed."
        ),
    )


def _factors(
    humid_hours: int,
    window_hours: int,
    observed: int,
    temperature_known: bool,
    missing: Sequence[str],
) -> tuple[ScoredFactor, ...]:
    humidity = factor(
        key="humidity_hours",
        label="Humidity exposure",
        # Scaled against the observed window rather than a fixed count, so a short
        # hourly series is not read as a calm one.
        score=100.0 - (humid_hours / max(observed, 1)) * 100.0,
        weight=WEIGHT_HUMIDITY,
        explanation=(
            f"{humid_hours} of {observed} observed hours at or above "
            f"{HUMID_HOUR_PCT:.0f}% relative humidity."
        ),
    )

    if not temperature_known:
        window = unknown_factor("infection_window", "Infection temperature window", ["temperature"])
    else:
        window = factor(
            key="infection_window",
            label="Infection temperature window",
            score=100.0 - (window_hours / max(observed, 1)) * 100.0,
            weight=WEIGHT_INFECTION_WINDOW,
            explanation=(
                f"{window_hours} of {observed} observed hours inside the "
                f"{INFECTION_BAND_C[0]:.0f}-{INFECTION_BAND_C[1]:.0f} °C infection window "
                "at high humidity."
            ),
        )

    return (humidity, window)


def _summary(
    context: AnalysisContext,
    humid_hours: int,
    window_hours: int,
    observed: int,
    items: Sequence[DiseaseItem],
) -> str:
    weather = (
        f"{humid_hours} of {observed} observed hours reach "
        f"{HUMID_HOUR_PCT:.0f}% relative humidity, with {window_hours} inside the "
        f"{INFECTION_BAND_C[0]:.0f}-{INFECTION_BAND_C[1]:.0f} °C infection window"
    )

    if context.crop.code is None:
        return f"{weather}. With no crop planted, no pathogen rules were evaluated."
    if not items:
        return f"{weather}. No infection rule for this crop was met."
    names = ", ".join(item.name.lower() for item in items)
    return f"{weather}, meeting the infection requirement for {names}."


def _explanation(score: float, items: Sequence[DiseaseItem]) -> str:
    if not items:
        return (
            "No pathogen rule for this crop met its infection requirement over the "
            "observed hours, placing disease pressure at low."
        )
    leader = items[0]
    return (
        f"{leader.name} met its infection requirement over {leader.matched_hours} hours, "
        f"placing disease pressure at {risk_level_for(score).value}."
    )
