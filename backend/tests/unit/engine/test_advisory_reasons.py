"""Advisory reason codes: the numbers behind the advice.

An irrigation advisory says *"84 mm depleted from a 120 mm root zone, past the 66 mm
readily-available threshold"*. Those three figures came off the FAO-56 run and reached no
published field — `water_risk` carries `deficit_mm` and `recommended_irrigation_mm`, but
not the reservoir the advice was computed against. A voice assistant answering in Hindi
had to parse the English or re-derive the balance.

Three advisories are covered and three deliberately are not. A disease advisory cites the
rule that `disease_risk.risks[].reasons` already publishes; a soil advisory cites
`limitations`/`score`/`band`, all on `soil_assessment`; the all-clear cites three section
scores that are each published on their own section. Emitting codes for those would
duplicate evidence and give the copies a chance to disagree —
`test_only_the_three_uncovered_advisories_carry_reasons` is what holds that line.
"""

from app.engine import composite
from app.engine.disease import DiseaseAssessment, DiseaseItem
from app.engine.reasons import (
    WATER_IRRIGATION_DEFICIT,
    WEATHER_COLD_THRESHOLD_EXCEEDED,
    WEATHER_HEAT_THRESHOLD_EXCEEDED,
)
from app.engine.soil import SoilAssessmentResult
from app.engine.water import WaterAssessment
from app.engine.weather import WeatherAssessment
from app.schemas.common import RiskLevel, ScoreBand


def thirsty_water(**overrides) -> WaterAssessment:
    """A farm past its readily-available threshold, so irrigation is advised."""
    base = {
        "sufficient": True,
        "score": 78.0,
        "level": RiskLevel.high,
        "applied_irrigation_mm": 84.0,
        "depletion_mm": 76.0,
        "taw_mm": 120.0,
        "raw_mm": 66.0,
        "application_efficiency": 0.9,
        "parameters_source": "crop",
    }
    base.update(overrides)
    return WaterAssessment(**base)


def hot_weather(**overrides) -> WeatherAssessment:
    base = {
        "score": 60.0,
        "level": RiskLevel.moderate,
        "heat_stress_days": 5,
        "heat_threshold_c": 32.0,
        "thresholds_source": "crop",
    }
    base.update(overrides)
    return WeatherAssessment(**base)


def cold_weather(**overrides) -> WeatherAssessment:
    base = {
        "score": 55.0,
        "level": RiskLevel.moderate,
        "frost_risk_days": 2,
        "cold_threshold_c": 4.0,
        "thresholds_source": "crop",
    }
    base.update(overrides)
    return WeatherAssessment(**base)


def only_reason(draft):
    assert draft is not None, "expected an advisory"
    assert len(draft.reasons) == 1, f"expected one reason, got {len(draft.reasons)}"
    return draft.reasons[0]


# --------------------------------------------------------------------------
# Irrigation
# --------------------------------------------------------------------------


def test_irrigation_emits_its_key() -> None:
    assert only_reason(composite._irrigation_advisory(thirsty_water())).key == (
        WATER_IRRIGATION_DEFICIT
    )


def test_irrigation_params_are_the_assessment_values() -> None:
    """Read straight off the assessment, unrounded. The prose rounds for readability;
    a consumer formatting for its own locale starts from what the engine computed."""
    water = thirsty_water()

    params = only_reason(composite._irrigation_advisory(water)).params

    assert params == {
        "applied_irrigation_mm": water.applied_irrigation_mm,
        "depletion_mm": water.depletion_mm,
        "taw_mm": water.taw_mm,
        "raw_mm": water.raw_mm,
        "application_efficiency": water.application_efficiency,
        "parameters_source": water.parameters_source,
    }


def test_a_rainfed_farm_omits_the_efficiency_rather_than_nulling_it() -> None:
    """A rainfed farm has no application efficiency at all, which is a different
    statement from one whose efficiency is unknown. `null` would say the latter."""
    params = only_reason(
        composite._irrigation_advisory(thirsty_water(application_efficiency=None))
    ).params

    assert "application_efficiency" not in params
    assert params["applied_irrigation_mm"] == 84.0, "the rest of the evidence survives"


def test_an_approximate_coefficient_source_is_reported() -> None:
    """The prose warns the depth is indicative when coefficients are not the crop's own.
    A consumer needs the same caveat as data to reproduce that warning."""
    params = only_reason(
        composite._irrigation_advisory(thirsty_water(parameters_source="category"))
    ).params

    assert params["parameters_source"] == "category"


def test_no_irrigation_advisory_means_no_reason() -> None:
    """Below the advisory threshold nothing is advised, so there is nothing to explain."""
    assert composite._irrigation_advisory(thirsty_water(score=10.0)) is None
    assert composite._irrigation_advisory(thirsty_water(applied_irrigation_mm=0.0)) is None
    assert composite._irrigation_advisory(thirsty_water(sufficient=False)) is None


# --------------------------------------------------------------------------
# Heat and cold
# --------------------------------------------------------------------------


def test_heat_emits_its_key_and_values() -> None:
    weather = hot_weather()

    reason = only_reason(composite._heat_advisory(weather))

    assert reason.key == WEATHER_HEAT_THRESHOLD_EXCEEDED
    assert reason.params == {
        "heat_stress_days": weather.heat_stress_days,
        "heat_threshold_c": weather.heat_threshold_c,
        "thresholds_source": weather.thresholds_source,
    }


def test_frost_emits_its_key_and_values() -> None:
    weather = cold_weather()

    reason = only_reason(composite._frost_advisory(weather))

    assert reason.key == WEATHER_COLD_THRESHOLD_EXCEEDED
    assert reason.params == {
        "frost_risk_days": weather.frost_risk_days,
        "cold_threshold_c": weather.cold_threshold_c,
        "thresholds_source": weather.thresholds_source,
    }


def test_heat_and_cold_are_distinguishable_despite_sharing_a_category() -> None:
    """Both advisories carry `category: "weather"`, so today a consumer cannot tell them
    apart. Distinct keys are what finally separates them."""
    heat = only_reason(composite._heat_advisory(hot_weather()))
    frost = only_reason(composite._frost_advisory(cold_weather()))

    assert heat.key != frost.key


def test_a_generic_threshold_source_is_reported() -> None:
    """With no crop planted the engine applies a generic threshold and the prose says so.
    The structured form must carry the same qualifier."""
    params = only_reason(composite._heat_advisory(hot_weather(thresholds_source="generic"))).params

    assert params["thresholds_source"] == "generic"


def test_no_weather_advisory_means_no_reason() -> None:
    assert composite._heat_advisory(hot_weather(heat_threshold_c=None)) is None
    assert composite._heat_advisory(hot_weather(heat_stress_days=0)) is None
    assert composite._frost_advisory(cold_weather(cold_threshold_c=None)) is None
    assert composite._frost_advisory(cold_weather(frost_risk_days=0)) is None


# --------------------------------------------------------------------------
# The line: three covered, three deliberately not
# --------------------------------------------------------------------------


def test_a_disease_advisory_carries_no_reasons() -> None:
    """Its evidence is already on `disease_risk.risks[].reasons`. Repeating it here
    would publish the same rule twice."""
    assessment = DiseaseAssessment(
        sufficient=True,
        score=70.0,
        level=RiskLevel.high,
        items=(
            DiseaseItem(
                rule_id="late_blight",
                name="Late blight",
                pathogen="Phytophthora infestans",
                crop_code="potato",
                probability=0.8,
                level=RiskLevel.high,
                matched_hours=20,
                triggering_conditions=("20 consecutive hours",),
                preventive_actions=(),
                scouting_advice=None,
            ),
        ),
    )

    assert composite._disease_advisory(assessment).reasons == ()


def test_a_soil_advisory_carries_no_reasons() -> None:
    """Its evidence — the limitation, the score, the band — is on `soil_assessment`."""
    soil = SoilAssessmentResult(
        sufficient=True,
        score=40.0,
        band=ScoreBand.poor,
        limitations=("Organic carbon is low",),
    )

    drafts = composite._soil_advisories(soil)

    assert drafts, "expected a soil advisory"
    assert all(draft.reasons == () for draft in drafts)


def test_the_all_clear_advisory_carries_no_reasons() -> None:
    """It cites three section scores, each published on its own section."""
    from app.engine.composite import CompositeAssessment

    draft = composite._all_clear_advisory(
        hot_weather(heat_stress_days=0),
        thirsty_water(score=10.0),
        DiseaseAssessment(sufficient=True, score=5.0, level=RiskLevel.low),
        CompositeAssessment(score=80.0, band=ScoreBand.excellent),
    )

    assert draft.reasons == ()


def test_only_the_three_uncovered_advisories_carry_reasons() -> None:
    """The whole-pipeline guard.

    Every advisory that fires is checked: exactly the irrigation, heat and cold ones
    carry evidence, and every key is from the approved vocabulary. A future advisory
    emitting a key nobody agreed to translate fails here.
    """
    from app.engine.composite import CompositeAssessment

    drafts = composite.derive_advisories(
        context=None,
        weather=WeatherAssessment(
            score=65.0,
            level=RiskLevel.high,
            heat_stress_days=5,
            heat_threshold_c=32.0,
            frost_risk_days=2,
            cold_threshold_c=4.0,
            thresholds_source="crop",
        ),
        water=thirsty_water(),
        disease=DiseaseAssessment(sufficient=True, score=5.0, level=RiskLevel.low),
        soil=SoilAssessmentResult(
            sufficient=True, score=40.0, band=ScoreBand.poor, limitations=("Low carbon",)
        ),
        composite=CompositeAssessment(score=50.0, band=ScoreBand.moderate),
    )

    permitted = {
        WATER_IRRIGATION_DEFICIT,
        WEATHER_HEAT_THRESHOLD_EXCEEDED,
        WEATHER_COLD_THRESHOLD_EXCEEDED,
    }
    with_reasons = {d.category.value for d in drafts if d.reasons}
    emitted = {r.key for d in drafts for r in d.reasons}

    assert emitted <= permitted, f"an unapproved key was emitted: {emitted - permitted}"
    assert with_reasons == {"irrigation", "weather"}
    assert {d.category.value for d in drafts if not d.reasons} >= {"soil"}


def test_every_param_value_is_a_scalar() -> None:
    """The published type admits float/int/str/None only."""
    for draft in (
        composite._irrigation_advisory(thirsty_water()),
        composite._heat_advisory(hot_weather()),
        composite._frost_advisory(cold_weather()),
    ):
        for reason in draft.reasons:
            for name, value in reason.params.items():
                assert isinstance(value, float | int | str), f"{name}={value!r}"


# --------------------------------------------------------------------------
# The prose is untouched
# --------------------------------------------------------------------------


def test_the_irrigation_prose_is_byte_for_byte_unchanged() -> None:
    draft = composite._irrigation_advisory(thirsty_water())

    assert draft.title == "Apply about 84 mm of irrigation"
    assert draft.rationale == (
        "76 mm depleted from a 120 mm root zone, past the 66 mm readily-available threshold."
    )
    assert draft.action_window == "within 48 hours"
    assert draft.confidence == 0.75


def test_the_weather_prose_is_byte_for_byte_unchanged() -> None:
    heat = composite._heat_advisory(hot_weather())
    frost = composite._frost_advisory(cold_weather())

    assert heat.title == "Prepare for 5 days of heat stress"
    assert heat.rationale == "5 forecast day(s) exceed 32 °C, this crop's optimal maximum."
    assert frost.title == "Cold damage expected on 2 day(s)"
    assert frost.rationale == (
        "2 forecast day(s) fall below 4 °C, this crop's cold-damage threshold."
    )


def test_priorities_and_ordering_are_unchanged() -> None:
    """Reasons are evidence, not input. Nothing about what fires or in what order moves."""
    assert composite._irrigation_advisory(thirsty_water()).priority == composite._priority_for(78.0)
    assert composite._frost_advisory(cold_weather()).priority.value == "critical"
