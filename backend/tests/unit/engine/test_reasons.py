"""Disease reason codes: the same evidence, as data.

`triggering_conditions` says *"20 consecutive hours at 10-24 °C with relative humidity at
or above 90%"*. Every number in that sentence was held by the engine and then formatted
away, so a consumer wanting to say it in Hindi had to parse English or re-derive the
agronomy. A `Reason` keeps the numbers beside the sentence.

The rule these tests defend is that **a reason states nothing the prose does not**. It
carries `rule_id`, the hours the matcher counted, and the rule's own thresholds — no new
threshold, no recomputed probability, no agronomic judgement that was not already made.
`test_reasons_add_no_agronomy_of_their_own` is the one that would catch a drift.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.engine import disease
from app.engine.context import (
    AnalysisContext,
    CropParameters,
    DiseaseRule,
    HourlyPoint,
    Range,
    RuleCondition,
)
from app.engine.reasons import (
    DISEASE_CONSECUTIVE_HOURS_MET,
    DISEASE_GROWTH_STAGE_MET,
    DISEASE_TOTAL_HOURS_MET,
    Reason,
)

START = datetime(2026, 8, 20, 0, tzinfo=UTC)


def blight_rule(**overrides) -> DiseaseRule:
    base = {
        "id": "late_blight",
        "name": "Late blight",
        "pathogen": "Phytophthora infestans",
        "crops": ("potato",),
        "conditions": (
            RuleCondition(
                "consecutive_hours", 10, temp_c=Range(10.0, 24.0), humidity_pct=Range(90.0, None)
            ),
        ),
        "threshold_hours": 10,
        "saturation_hours": 24,
    }
    base.update(overrides)
    return DiseaseRule(**base)


def humid_hours(count: int, temp: float = 18.0, humidity: float = 95.0):
    return [
        HourlyPoint(at=START + timedelta(hours=n), temperature_c=temp, humidity_pct=humidity)
        for n in range(count)
    ]


def a_context(*, hourly, rules, stage: str = "flowering", crop_code: str | None = "potato"):
    return AnalysisContext(
        farm_id="farm",
        latitude=-1.29,
        longitude=36.82,
        timezone="UTC",
        today=START.date(),
        hourly=hourly,
        disease_rules=rules,
        crop=CropParameters(code=crop_code, name="Potato"),
        growth_stage=stage,
    )


def only_reason(assessment) -> Reason:
    assert assessment.items, "expected a matched rule"
    reasons = assessment.items[0].reasons
    assert len(reasons) == 1, f"expected one reason, got {len(reasons)}"
    return reasons[0]


# --------------------------------------------------------------------------
# The key
# --------------------------------------------------------------------------


def test_a_matched_consecutive_hours_rule_emits_its_key() -> None:
    result = disease.evaluate(a_context(hourly=humid_hours(20), rules=(blight_rule(),)))

    assert only_reason(result).key == DISEASE_CONSECUTIVE_HOURS_MET


def test_a_total_hours_rule_emits_a_different_key() -> None:
    """The key names *which* condition was met, so two clause types cannot share one."""
    rule = blight_rule(
        conditions=(RuleCondition("total_hours", 4, precipitation_mm=Range(0.2, None)),),
        threshold_hours=4,
    )
    wet = [
        HourlyPoint(
            at=START + timedelta(hours=n),
            temperature_c=18.0,
            humidity_pct=95.0,
            precipitation_mm=1.0,
        )
        for n in range(10)
    ]

    assert only_reason(disease.evaluate(a_context(hourly=wet, rules=(rule,)))).key == (
        DISEASE_TOTAL_HOURS_MET
    )


def test_a_conjunction_emits_one_reason_per_clause() -> None:
    """A rule is a conjunction, and the prose lists every clause that matched. The
    structured form must not summarise where the sentence enumerates."""
    rule = blight_rule(
        conditions=(
            RuleCondition(
                "consecutive_hours", 10, temp_c=Range(10.0, 24.0), humidity_pct=Range(90.0, None)
            ),
            RuleCondition("growth_stage_at_least", stage="vegetative"),
        )
    )

    result = disease.evaluate(a_context(hourly=humid_hours(20), rules=(rule,)))
    keys = [r.key for r in result.items[0].reasons]

    assert keys == [DISEASE_CONSECUTIVE_HOURS_MET, DISEASE_GROWTH_STAGE_MET]


def test_every_key_is_dotted_domain_condition() -> None:
    """Keys are a public vocabulary a client binds translations to."""
    result = disease.evaluate(a_context(hourly=humid_hours(20), rules=(blight_rule(),)))

    for reason in result.items[0].reasons:
        assert reason.key.startswith("disease."), reason.key
        assert reason.key.count(".") == 1, reason.key
        assert reason.key.islower()


def test_an_unknown_clause_type_yields_no_reason() -> None:
    """A key a client cannot translate is worse than no key, so an unrecognised clause
    produces nothing rather than a guess."""
    from app.engine.reasons import DISEASE_CONDITION_KEYS

    assert DISEASE_CONDITION_KEYS.get("some_future_clause") is None


# --------------------------------------------------------------------------
# The params
# --------------------------------------------------------------------------


def test_params_carry_the_structured_evidence() -> None:
    """`rule_id` and `matched_hours` exist on the engine item and used to be dropped at
    the schema boundary. They are the whole point."""
    reason = only_reason(
        disease.evaluate(a_context(hourly=humid_hours(20), rules=(blight_rule(),)))
    )

    assert reason.params["rule_id"] == "late_blight"
    assert reason.params["pathogen"] == "Phytophthora infestans"
    assert reason.params["matched_hours"] == 20
    assert reason.params["required_hours"] == 10
    assert reason.params["threshold_hours"] == 10
    assert reason.params["saturation_hours"] == 24


def test_params_carry_the_condition_bounds() -> None:
    """The window the hours had to fall inside — the numbers the sentence quotes."""
    reason = only_reason(
        disease.evaluate(a_context(hourly=humid_hours(20), rules=(blight_rule(),)))
    )

    assert reason.params["temp_min_c"] == 10.0
    assert reason.params["temp_max_c"] == 24.0
    assert reason.params["humidity_min_pct"] == 90.0
    assert reason.params["humidity_max_pct"] is None, "an open end stays open"


def test_an_absent_measurement_bound_is_omitted_not_nulled() -> None:
    """A rule with no rain clause must not claim a rain bound of None — that reads as
    'measured, unbounded' rather than 'not part of this rule'."""
    reason = only_reason(
        disease.evaluate(a_context(hourly=humid_hours(20), rules=(blight_rule(),)))
    )

    assert "precipitation_min_mm" not in reason.params


def test_a_growth_stage_clause_reports_both_stages() -> None:
    rule = blight_rule(conditions=(RuleCondition("growth_stage_at_least", stage="vegetative"),))

    reason = only_reason(disease.evaluate(a_context(hourly=humid_hours(20), rules=(rule,))))

    assert reason.key == DISEASE_GROWTH_STAGE_MET
    assert reason.params["required_stage"] == "vegetative"
    assert reason.params["growth_stage"] == "flowering"
    assert "matched_hours" not in reason.params, "a stage gate has no hours"


def test_every_param_value_is_a_scalar() -> None:
    """The published type admits float/int/str/None only. A nested value would push a
    client back to walking a structure instead of filling a sentence."""
    result = disease.evaluate(a_context(hourly=humid_hours(20), rules=(blight_rule(),)))

    for reason in result.items[0].reasons:
        for name, value in reason.params.items():
            assert isinstance(value, float | int | str | type(None)), f"{name}={value!r}"


def test_matched_hours_agrees_with_the_item() -> None:
    """Two representations of one fact must not diverge."""
    result = disease.evaluate(a_context(hourly=humid_hours(17), rules=(blight_rule(),)))
    item = result.items[0]

    assert only_reason(result).params["matched_hours"] == item.matched_hours


# --------------------------------------------------------------------------
# What reasons must NOT do
# --------------------------------------------------------------------------


def test_no_match_means_no_reasons() -> None:
    """Nine hours against a ten-hour rule. Nothing matched, so there is nothing to say."""
    result = disease.evaluate(a_context(hourly=humid_hours(9), rules=(blight_rule(),)))

    assert result.items == ()


def test_a_wrong_crop_produces_no_reasons() -> None:
    """The engine refuses to name a potato pathogen over a maize field; the reason layer
    must not reintroduce it."""
    result = disease.evaluate(
        a_context(hourly=humid_hours(20), rules=(blight_rule(),), crop_code="maize")
    )

    assert result.items == ()


def test_reasons_add_no_agronomy_of_their_own() -> None:
    """The guard against drift.

    Every param must be traceable to the rule or to the hours the matcher counted. A
    value appearing here that is neither would be a second, unreviewed source of
    agronomy — which is exactly what the deterministic engine exists to prevent.
    """
    rule = blight_rule()
    result = disease.evaluate(a_context(hourly=humid_hours(20), rules=(rule,)))
    params = only_reason(result).params

    permitted = {
        "rule_id": rule.id,
        "pathogen": rule.pathogen,
        "matched_hours": result.items[0].matched_hours,
        "required_hours": rule.conditions[0].hours,
        "threshold_hours": rule.threshold_hours,
        "saturation_hours": rule.saturation_hours,
        "temp_min_c": rule.conditions[0].temp_c.low,
        "temp_max_c": rule.conditions[0].temp_c.high,
        "humidity_min_pct": rule.conditions[0].humidity_pct.low,
        "humidity_max_pct": rule.conditions[0].humidity_pct.high,
    }

    assert params == permitted, "a param appeared that the rule and matcher do not supply"


def test_probability_and_level_are_untouched_by_the_reason_layer() -> None:
    """Phase 1 carries evidence; it does not score. These are pinned so a future edit to
    `_rule_reasons` cannot quietly move a risk level."""
    rule = blight_rule()
    result = disease.evaluate(a_context(hourly=humid_hours(20), rules=(rule,)))
    item = result.items[0]

    # Asserted against the engine's own function rather than a formula restated here:
    # a copy of the arithmetic in the test would pass while both drifted together.
    assert item.probability == disease.probability_for(rule, 20)
    assert item.matched_hours == 20


def test_the_prose_still_reads_exactly_as_before() -> None:
    """Reasons sit beside `triggering_conditions`; they do not replace or reword it."""
    result = disease.evaluate(a_context(hourly=humid_hours(20), rules=(blight_rule(),)))

    assert result.items[0].triggering_conditions == (
        "20 consecutive hours at 10-24 °C with relative humidity at or above 90%",
    )
