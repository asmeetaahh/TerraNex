"""Disease rules matched against the hourly series.

The centre of this file is duration. A rule fires when conditions hold for a stated
number of *consecutive* hours, so the two cases that matter most are the threshold hour
and the hour before it — one is an infection period and the other is a humid night that
came to nothing. Everything else in the module is downstream of getting that boundary
right, which is why it is tested from both sides for every rule shipped.

The second theme is that nothing is invented. Probability comes from measured hours, not
from a rule's position in a list; a pathogen is named only when a rule for the planted
crop actually matched; and with no hourly humidity at all the result says so instead of
reporting calm.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.engine import disease
from app.engine.context import (
    AnalysisContext,
    CropParameters,
    DiseaseRule,
    HourlyPoint,
    Range,
    RuleCondition,
    SoilPoint,
)
from app.engine.scoring import INSUFFICIENT

TODAY = date(2026, 8, 22)
START = datetime(2026, 8, 22, 0, 0, tzinfo=UTC)

POTATO = CropParameters(code="potato", category="tuber", parameters_source="crop")
MAIZE = CropParameters(code="maize", category="cereal", parameters_source="crop")

#: A ten-hour late-blight rule, the shape most rules in the shipped set take.
LATE_BLIGHT = DiseaseRule(
    id="late_blight",
    name="Late blight",
    pathogen="Phytophthora infestans",
    crops=("potato", "tomato"),
    conditions=(
        RuleCondition(
            type="consecutive_hours",
            hours=10,
            temp_c=Range(10.0, 24.0),
            humidity_pct=Range(90.0, None),
        ),
    ),
    threshold_hours=10,
    saturation_hours=48,
    preventive_actions=("Apply a protectant fungicide",),
    scouting_advice="Inspect lower leaves at dawn.",
)


def hours(
    count: int,
    *,
    temp: float | None = 18.0,
    humidity: float | None = 95.0,
    rain: float = 0.0,
    start: datetime = START,
) -> list[HourlyPoint]:
    return [
        HourlyPoint(
            at=start + timedelta(hours=n),
            temperature_c=temp,
            humidity_pct=humidity,
            precipitation_mm=rain,
        )
        for n in range(count)
    ]


def a_context(hourly=None, **overrides) -> AnalysisContext:
    values = {
        "farm_id": "f",
        "latitude": -21.1775,
        "longitude": -47.8103,
        "timezone": "UTC",
        "today": TODAY,
        "hourly": tuple(hourly if hourly is not None else hours(48)),
        "soil": SoilPoint(texture_class="loam"),
        "crop": POTATO,
        "growth_stage": "flowering",
        "irrigation_type": "rainfed",
        "disease_rules": (LATE_BLIGHT,),
    }
    values.update(overrides)
    return AnalysisContext(**values)


# --------------------------------------------------------------------------
# The threshold, from both sides
# --------------------------------------------------------------------------


def test_the_rule_fires_at_exactly_the_threshold_hour() -> None:
    """Ten hours is the requirement, so ten hours meets it."""
    result = disease.evaluate(a_context(hourly=hours(10)))

    assert result.sufficient
    assert [item.rule_id for item in result.items] == ["late_blight"]


def test_the_rule_does_not_fire_one_hour_before_the_threshold() -> None:
    """Nine hours is a humid night that came to nothing. Reporting an infection here
    would be a fabricated detection — the single failure this engine must not have."""
    result = disease.evaluate(a_context(hourly=hours(9)))

    assert result.sufficient
    assert result.items == ()
    assert result.score == 0.0


def test_the_boundary_holds_for_every_shipped_rule() -> None:
    """Asserted against the real ruleset rather than a fixture, so an edit to
    `diseases.yaml` that moves a threshold is caught here."""
    from app.rules.registry import disease_rules

    for rule in disease_rules():
        window = rule.threshold_hours
        band = rule.conditions[0]
        temp = (band.temp_c.low + band.temp_c.high) / 2
        humidity = band.humidity_pct.low

        context_kwargs = {
            "crop": CropParameters(code=rule.crops[0], parameters_source="crop"),
            "disease_rules": (rule,),
            "growth_stage": "fruiting",
        }

        at = disease.evaluate(
            a_context(
                hourly=hours(window, temp=temp, humidity=humidity, rain=1.0), **context_kwargs
            )
        )
        before = disease.evaluate(
            a_context(
                hourly=hours(window - 1, temp=temp, humidity=humidity, rain=1.0), **context_kwargs
            )
        )

        assert [i.rule_id for i in at.items] == [rule.id], f"{rule.id} did not fire at threshold"
        assert before.items == (), f"{rule.id} fired one hour early"


def test_an_interrupted_run_does_not_count() -> None:
    """Consecutive means consecutive. Twenty humid hours split by one dry hour is not a
    ten-hour infection period, and summing them would invent one."""
    broken = (
        hours(9)
        + hours(1, humidity=40.0, start=START + timedelta(hours=9))
        + hours(9, start=START + timedelta(hours=10))
    )

    assert disease.evaluate(a_context(hourly=broken)).items == ()


def test_a_run_resumes_after_an_interruption() -> None:
    dry_then_wet = hours(3, humidity=40.0) + hours(12, start=START + timedelta(hours=3))

    result = disease.evaluate(a_context(hourly=dry_then_wet))

    assert [item.rule_id for item in result.items] == ["late_blight"]


@pytest.mark.parametrize("temp", [9.9, 24.1])
def test_an_hour_outside_the_temperature_band_breaks_the_run(temp: float) -> None:
    assert disease.evaluate(a_context(hourly=hours(20, temp=temp))).items == ()


@pytest.mark.parametrize("temp", [10.0, 24.0])
def test_the_temperature_band_is_inclusive(temp: float) -> None:
    assert disease.evaluate(a_context(hourly=hours(10, temp=temp))).items != ()


def test_the_humidity_threshold_is_inclusive() -> None:
    assert disease.evaluate(a_context(hourly=hours(10, humidity=90.0))).items != ()
    assert disease.evaluate(a_context(hourly=hours(10, humidity=89.9))).items == ()


# --------------------------------------------------------------------------
# Probability comes from measured hours
# --------------------------------------------------------------------------


def test_probability_is_at_its_floor_at_the_threshold() -> None:
    result = disease.evaluate(a_context(hourly=hours(10)))

    assert result.items[0].probability == pytest.approx(disease.THRESHOLD_PROBABILITY)


def test_probability_reaches_one_at_saturation() -> None:
    result = disease.evaluate(a_context(hourly=hours(48)))

    assert result.items[0].probability == pytest.approx(1.0)


def test_probability_rises_with_measured_hours() -> None:
    """The property that makes this deterministic rather than positional: more measured
    infection hours, higher likelihood, every time."""
    probabilities = [
        disease.evaluate(a_context(hourly=hours(n))).items[0].probability
        for n in (10, 20, 30, 40, 48)
    ]

    assert probabilities == sorted(probabilities)
    assert len(set(probabilities)) == len(probabilities)


def test_probability_never_exceeds_one() -> None:
    result = disease.evaluate(a_context(hourly=hours(200)))

    assert result.items[0].probability == 1.0


def test_two_rules_are_ordered_by_probability_not_by_position() -> None:
    """The defect this replaces scored by list index, so the first pathogen named was
    always the most likely one regardless of the weather."""
    weak = DiseaseRule(
        id="weak",
        name="Weak",
        pathogen="Test weak",
        crops=("potato",),
        conditions=(RuleCondition("consecutive_hours", 10, Range(10.0, 24.0), Range(90.0, None)),),
        threshold_hours=10,
        saturation_hours=400,
    )
    strong = DiseaseRule(
        id="strong",
        name="Strong",
        pathogen="Test strong",
        crops=("potato",),
        conditions=(RuleCondition("consecutive_hours", 10, Range(10.0, 24.0), Range(90.0, None)),),
        threshold_hours=10,
        saturation_hours=20,
    )

    # `weak` is listed first, so a positional scheme would rank it first.
    result = disease.evaluate(a_context(hourly=hours(20), disease_rules=(weak, strong)))

    assert [item.rule_id for item in result.items] == ["strong", "weak"]
    assert result.items[0].probability > result.items[1].probability


# --------------------------------------------------------------------------
# Rule clauses
# --------------------------------------------------------------------------


def test_a_growth_stage_clause_gates_the_rule() -> None:
    gated = DiseaseRule(
        id="gated",
        name="Gated",
        pathogen="Test",
        crops=("potato",),
        conditions=(
            RuleCondition("consecutive_hours", 10, Range(10.0, 24.0), Range(90.0, None)),
            RuleCondition("growth_stage_at_least", stage="flowering"),
        ),
        threshold_hours=10,
        saturation_hours=48,
    )

    flowering = disease.evaluate(
        a_context(hourly=hours(12), disease_rules=(gated,), growth_stage="flowering")
    )
    seedling = disease.evaluate(
        a_context(hourly=hours(12), disease_rules=(gated,), growth_stage="seedling")
    )

    assert flowering.items != ()
    assert seedling.items == ()


def test_a_total_hours_clause_need_not_be_consecutive() -> None:
    """Rain hours accumulate; leaf wetness does not have to be unbroken to matter."""
    rule = DiseaseRule(
        id="wet",
        name="Wet",
        pathogen="Test",
        crops=("potato",),
        conditions=(RuleCondition("total_hours", 4, precipitation_mm=Range(0.2, None)),),
        threshold_hours=4,
        saturation_hours=20,
    )
    scattered = [
        HourlyPoint(
            at=START + timedelta(hours=n),
            temperature_c=18.0,
            humidity_pct=95.0,
            precipitation_mm=(1.0 if n % 4 == 0 else 0.0),
        )
        for n in range(20)
    ]

    assert disease.evaluate(a_context(hourly=scattered, disease_rules=(rule,))).items != ()


def test_every_clause_must_match() -> None:
    """A rule with two clauses is a conjunction, not a menu."""
    both = DiseaseRule(
        id="both",
        name="Both",
        pathogen="Test",
        crops=("potato",),
        conditions=(
            RuleCondition("consecutive_hours", 10, Range(10.0, 24.0), Range(90.0, None)),
            RuleCondition("total_hours", 4, precipitation_mm=Range(0.2, None)),
        ),
        threshold_hours=10,
        saturation_hours=48,
    )

    dry = disease.evaluate(a_context(hourly=hours(20, rain=0.0), disease_rules=(both,)))
    wet = disease.evaluate(a_context(hourly=hours(20, rain=1.0), disease_rules=(both,)))

    assert dry.items == ()
    assert wet.items != ()


def test_the_matched_clauses_are_reported_in_the_item() -> None:
    """`triggering_conditions` must state what actually matched, in measured terms."""
    result = disease.evaluate(a_context(hourly=hours(14)))
    conditions = result.items[0].triggering_conditions

    assert any("14 consecutive hours" in c for c in conditions)
    assert any("90" in c for c in conditions)


# --------------------------------------------------------------------------
# No fabricated pathogens
# --------------------------------------------------------------------------


def test_only_rules_for_the_planted_crop_apply() -> None:
    result = disease.evaluate(a_context(hourly=hours(48), crop=MAIZE))

    assert result.items == ()


def test_an_unplanted_farm_names_no_pathogen() -> None:
    """The behaviour this replaces invented a generic `fungal_leaf_spot` when no crop
    was planted, which is a diagnosis for a plant that does not exist."""
    result = disease.evaluate(a_context(hourly=hours(48), crop=CropParameters()))

    assert result.items == ()
    assert result.sufficient
    assert "no crop" in result.conditions_summary.lower()


def test_conditions_are_still_measured_without_a_crop() -> None:
    """The weather is real even when there is nothing planted in it."""
    result = disease.evaluate(a_context(hourly=hours(48), crop=CropParameters()))

    assert result.humid_hours > 0
    assert next(f for f in result.factors if f.key == "humidity_hours").weight > 0


def test_no_rules_at_all_is_not_an_error() -> None:
    result = disease.evaluate(a_context(hourly=hours(48), disease_rules=()))

    assert result.sufficient
    assert result.items == ()


# --------------------------------------------------------------------------
# Aggregate score
# --------------------------------------------------------------------------


def test_no_matching_rule_scores_no_pressure() -> None:
    assert disease.evaluate(a_context(hourly=hours(9))).score == 0.0


def test_two_active_pathogens_raise_pressure_above_either_alone() -> None:
    """Combined as independent events, so a second pathogen adds pressure without the
    total ever passing 100."""
    second = DiseaseRule(
        id="second",
        name="Second",
        pathogen="Test",
        crops=("potato",),
        conditions=(RuleCondition("consecutive_hours", 10, Range(10.0, 24.0), Range(90.0, None)),),
        threshold_hours=10,
        saturation_hours=48,
    )

    one = disease.evaluate(a_context(hourly=hours(20)))
    two = disease.evaluate(a_context(hourly=hours(20), disease_rules=(LATE_BLIGHT, second)))

    assert two.score > one.score
    assert two.score <= 100.0


def test_the_score_stays_within_range_under_many_rules() -> None:
    many = tuple(
        DiseaseRule(
            id=f"r{n}",
            name=f"R{n}",
            pathogen="Test",
            crops=("potato",),
            conditions=(
                RuleCondition("consecutive_hours", 10, Range(10.0, 24.0), Range(90.0, None)),
            ),
            threshold_hours=10,
            saturation_hours=12,
        )
        for n in range(12)
    )

    result = disease.evaluate(a_context(hourly=hours(48), disease_rules=many))

    assert 0.0 <= result.score <= 100.0


# --------------------------------------------------------------------------
# Missing data
# --------------------------------------------------------------------------


def test_no_hourly_series_is_insufficient() -> None:
    result = disease.evaluate(a_context(hourly=[]))

    assert not result.sufficient
    assert INSUFFICIENT in result.explanation
    assert INSUFFICIENT in result.conditions_summary


def test_missing_humidity_is_insufficient_not_calm() -> None:
    """The silent-corruption case: an absent reading must not read as a dry week."""
    result = disease.evaluate(a_context(hourly=hours(48, humidity=None)))

    assert not result.sufficient
    assert result.items == ()
    humidity = next(f for f in result.factors if f.key == "humidity_hours")
    assert humidity.weight == 0.0
    assert "humidity" in humidity.explanation


def test_missing_temperature_leaves_the_infection_window_unassessed() -> None:
    result = disease.evaluate(a_context(hourly=hours(48, temp=None)))

    window = next(f for f in result.factors if f.key == "infection_window")
    assert window.weight == 0.0


def test_a_non_numeric_reading_is_treated_as_unknown() -> None:
    junk = [
        HourlyPoint(at=START + timedelta(hours=n), temperature_c=18.0, humidity_pct="wet")  # type: ignore[arg-type]
        for n in range(48)
    ]

    result = disease.evaluate(a_context(hourly=junk))

    assert not result.sufficient
    assert result.items == ()


def test_an_hour_missing_a_reading_breaks_a_run() -> None:
    """Unknown is not "conditions held". Bridging the gap would manufacture an
    infection period out of a provider outage."""
    gapped = (
        hours(6)
        + [HourlyPoint(at=START + timedelta(hours=6), temperature_c=18.0)]
        + hours(6, start=START + timedelta(hours=7))
    )

    assert disease.evaluate(a_context(hourly=gapped)).items == ()


# --------------------------------------------------------------------------
# Determinism, geography, purity
# --------------------------------------------------------------------------


def test_the_same_context_produces_an_identical_result() -> None:
    context = a_context()

    assert disease.evaluate(context) == disease.evaluate(context)


def test_repeated_evaluation_never_varies() -> None:
    """No RNG anywhere: the assessment this replaces jittered each pathogen's score."""
    first = disease.evaluate(a_context())
    repeats = [disease.evaluate(a_context()) for _ in range(8)]

    assert all(run == first for run in repeats)
    assert len({run.items[0].probability for run in repeats}) == 1


@pytest.mark.parametrize(
    "latitude",
    [
        -21.1775,
        45.0453,
        68.9678,
        19.997,
        34.7578,
        -29.1211,
        24.0908,
        11.5936,
        29.6103,
        24.1917,
        3.5833,
        24.6877,
    ],
)
def test_identical_hours_score_identically_at_every_brics_latitude(latitude: float) -> None:
    """Infection depends on the hours, not on where they happened."""
    baseline = disease.evaluate(a_context(latitude=-21.1775))
    here = disease.evaluate(a_context(latitude=latitude))

    assert here.score == baseline.score
    assert [i.rule_id for i in here.items] == [i.rule_id for i in baseline.items]


def test_the_engine_module_imports_nothing_impure() -> None:
    from tests.unit.engine.test_engine_is_pure import violations

    source = disease.__file__ or ""
    assert source
    with open(source, encoding="utf-8") as handle:
        assert violations(handle.read()) == []
