"""The overall score, and the advisories derived from it.

Two properties carry this file.

**Renormalisation.** A farm whose soil survey came back empty must be scored on what
was measured, at full marks if those are good. The failure mode is subtle: treat a
missing section as a zero and every partially-observed farm looks worse than it is,
which is exactly the kind of wrong number a farmer would act on.

**Evidence.** Every advisory has to cite something an engine actually produced. The
tests below check the two claims the previous version made without support — a flat
"2 °C" frost threshold regardless of crop, and a generic "foliar disease" when no rule
had matched.
"""

from datetime import date

import pytest

from app.engine import composite
from app.engine.context import AnalysisContext, CropParameters, SoilPoint
from app.engine.disease import DiseaseAssessment, DiseaseItem
from app.engine.scoring import factor, unknown_factor
from app.engine.soil import SoilAssessmentResult
from app.engine.vegetation import VegetationAssessment
from app.engine.water import WaterAssessment
from app.engine.weather import WeatherAssessment
from app.schemas.common import RiskLevel, ScoreBand
from app.schemas.enums import AdvisoryCategory, AdvisoryPriority

TODAY = date(2026, 8, 22)


def a_context(**overrides) -> AnalysisContext:
    values = {
        "farm_id": "f",
        "latitude": -21.1775,
        "longitude": -47.8103,
        "timezone": "UTC",
        "today": TODAY,
        "soil": SoilPoint(texture_class="loam"),
        "crop": CropParameters(code="maize", category="cereal", parameters_source="crop"),
        "growth_stage": "flowering",
        "irrigation_type": "drip",
    }
    values.update(overrides)
    return AnalysisContext(**values)


def scored(*keys: str) -> tuple:
    return tuple(factor(k, k.title(), 80.0, 1.0 / len(keys), "measured") for k in keys)


def unscored(*keys: str) -> tuple:
    return tuple(unknown_factor(k, k.title(), ["evidence"]) for k in keys)


def a_weather(score: float = 10.0, assessed: bool = True, **kw) -> WeatherAssessment:
    values = {
        "score": score,
        "level": RiskLevel.low,
        "heat_threshold_c": 32.0,
        "cold_threshold_c": 2.0,
        "thresholds_source": "crop",
        "factors": scored("heat_stress") if assessed else unscored("heat_stress"),
        "explanation": "weather",
    }
    values.update(kw)
    return WeatherAssessment(**values)


def a_water(score: float = 10.0, assessed: bool = True, **kw) -> WaterAssessment:
    values = {
        "sufficient": assessed,
        "score": score,
        "level": RiskLevel.low,
        "taw_mm": 170.0,
        "raw_mm": 85.0,
        "depletion_mm": 20.0,
        "applied_irrigation_mm": 0.0,
        "application_efficiency": 0.9,
        "parameters_source": "crop",
        "factors": scored("water_balance") if assessed else unscored("water_balance"),
        "explanation": "water",
    }
    values.update(kw)
    return WaterAssessment(**values)


def a_disease(score: float = 10.0, assessed: bool = True, **kw) -> DiseaseAssessment:
    values = {
        "sufficient": assessed,
        "score": score,
        "level": RiskLevel.low,
        "factors": scored("humidity_hours") if assessed else unscored("humidity_hours"),
        "explanation": "disease",
        "conditions_summary": "conditions",
    }
    values.update(kw)
    return DiseaseAssessment(**values)


def a_soil(score: float = 80.0, assessed: bool = True, **kw) -> SoilAssessmentResult:
    values = {
        "sufficient": assessed,
        "score": score,
        "band": ScoreBand.good,
        "factors": scored("soil_ph") if assessed else unscored("soil_ph"),
        "explanation": "soil",
    }
    values.update(kw)
    return SoilAssessmentResult(**values)


def a_vegetation(score: float = 80.0, assessed: bool = True, **kw) -> VegetationAssessment:
    values = {
        "sufficient": assessed,
        "score": score,
        "band": ScoreBand.good,
        "factors": scored("canopy_vigour") if assessed else unscored("canopy_vigour"),
        "explanation": "vegetation",
    }
    values.update(kw)
    return VegetationAssessment(**values)


def evaluate(**kw) -> composite.CompositeAssessment:
    return composite.evaluate(
        kw.pop("context", a_context()),
        kw.pop("weather", a_weather()),
        kw.pop("water", a_water()),
        kw.pop("disease", a_disease()),
        kw.pop("soil", a_soil()),
        kw.pop("vegetation", a_vegetation()),
    )


# --------------------------------------------------------------------------
# Weighting
# --------------------------------------------------------------------------


def test_the_weights_sum_to_one_when_everything_is_assessed() -> None:
    assert evaluate().assessed_weight == pytest.approx(1.0)


def test_all_five_sections_are_reported() -> None:
    keys = [f.key for f in evaluate().factors]

    assert keys == [
        "weather_risk",
        "water_risk",
        "disease_risk",
        "soil_suitability",
        "crop_health",
    ]


def test_risk_sections_are_inverted_into_health() -> None:
    """Weather, water and disease run high-is-bad; the composite runs high-is-good.
    Averaging one in the wrong direction would invert half the dashboard."""
    result = evaluate(weather=a_weather(score=70.0))

    weather = next(f for f in result.factors if f.key == "weather_risk")

    assert weather.score == pytest.approx(30.0)


def test_condition_sections_are_used_as_given() -> None:
    result = evaluate(soil=a_soil(score=41.0))

    assert next(f for f in result.factors if f.key == "soil_suitability").score == 41.0


def test_a_uniformly_good_farm_scores_well() -> None:
    result = evaluate(
        weather=a_weather(score=0.0),
        water=a_water(score=0.0),
        disease=a_disease(score=0.0),
        soil=a_soil(score=100.0),
        vegetation=a_vegetation(score=100.0),
    )

    assert result.score == pytest.approx(100.0)
    assert result.band is ScoreBand.excellent


def test_the_composite_is_the_weighted_mean() -> None:
    result = evaluate(
        weather=a_weather(score=100.0),  # health 0, weight .20
        water=a_water(score=0.0),  # health 100, weight .25
        disease=a_disease(score=0.0),  # health 100, weight .20
        soil=a_soil(score=50.0),  # 50, weight .20
        vegetation=a_vegetation(score=50.0),  # 50, weight .15
    )

    expected = (0 * 0.20 + 100 * 0.25 + 100 * 0.20 + 50 * 0.20 + 50 * 0.15) / 1.0

    assert result.score == pytest.approx(expected)


# --------------------------------------------------------------------------
# Renormalisation — the property that matters most
# --------------------------------------------------------------------------


def test_an_unassessed_section_carries_no_weight() -> None:
    result = evaluate(soil=a_soil(assessed=False))

    soil_factor = next(f for f in result.factors if f.key == "soil_suitability")

    assert soil_factor.weight == 0.0
    assert result.assessed_weight == pytest.approx(0.80)


def test_a_missing_section_does_not_drag_the_score_down() -> None:
    """The failure this exists to prevent: treating an unmeasured soil as a zero would
    make every partially-observed farm look worse than it is."""
    complete = evaluate()
    without_soil = evaluate(soil=a_soil(assessed=False))

    assert without_soil.score >= complete.score - 1e-9
    assert without_soil.score > 0


def test_a_farm_measured_only_on_good_sections_still_scores_full_marks() -> None:
    result = evaluate(
        weather=a_weather(score=0.0),
        water=a_water(score=0.0),
        disease=a_disease(assessed=False),
        soil=a_soil(assessed=False),
        vegetation=a_vegetation(assessed=False),
    )

    assert result.score == pytest.approx(100.0)
    assert result.assessed_weight == pytest.approx(0.45)


def test_remaining_sections_keep_their_relative_importance() -> None:
    """Renormalising must not reweight what survives — water stays worth more than
    weather after a section drops out."""
    result = evaluate(
        weather=a_weather(score=100.0),
        water=a_water(score=0.0),
        disease=a_disease(assessed=False),
        soil=a_soil(assessed=False),
        vegetation=a_vegetation(assessed=False),
    )

    expected = (0 * 0.20 + 100 * 0.25) / 0.45

    assert result.score == pytest.approx(expected)


def test_unassessed_sections_are_named() -> None:
    result = evaluate(soil=a_soil(assessed=False), disease=a_disease(assessed=False))

    assert set(result.unassessed) == {"Soil suitability", "Disease pressure"}


def test_everything_unassessed_does_not_divide_by_zero() -> None:
    result = evaluate(
        weather=a_weather(assessed=False),
        water=a_water(assessed=False),
        disease=a_disease(assessed=False),
        soil=a_soil(assessed=False),
        vegetation=a_vegetation(assessed=False),
    )

    assert result.score == 0.0
    assert result.assessed_weight == 0.0
    assert len(result.unassessed) == 5


def test_soil_is_no_longer_assumed_always_assessed() -> None:
    """Regression. Soil was hardcoded as always contributing, which held only while it
    was simulated and therefore never absent. A real survey can return an empty
    profile, and crediting an unmeasured soil is as wrong as penalising one."""
    result = evaluate(soil=a_soil(score=100.0, assessed=False))

    assert next(f for f in result.factors if f.key == "soil_suitability").weight == 0.0
    assert "Soil suitability" in result.unassessed


def test_a_partially_evidenced_section_does_not_contribute() -> None:
    """One scored sub-factor beside one unknown is not enough: water risk still knows
    the irrigation type when rainfall is missing, and that alone would imply a balance
    nobody calculated."""
    partial = a_water(factors=(*scored("irrigation"), *unscored("water_balance")))

    result = evaluate(water=partial)

    assert next(f for f in result.factors if f.key == "water_risk").weight == 0.0


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_the_same_inputs_produce_an_identical_result() -> None:
    assert evaluate() == evaluate()


def test_the_factor_order_is_stable() -> None:
    assert [f.key for f in evaluate().factors] == [f.key for f in evaluate().factors]


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
def test_location_does_not_change_the_composite(latitude: float) -> None:
    assert evaluate(context=a_context(latitude=latitude)).score == evaluate().score


# --------------------------------------------------------------------------
# Advisories — evidence only
# --------------------------------------------------------------------------


def advisories(**kw) -> tuple[composite.AdvisoryDraft, ...]:
    weather = kw.pop("weather", a_weather())
    water = kw.pop("water", a_water())
    disease = kw.pop("disease", a_disease())
    soil = kw.pop("soil", a_soil())
    vegetation = kw.pop("vegetation", a_vegetation())
    context = kw.pop("context", a_context())
    result = composite.evaluate(context, weather, water, disease, soil, vegetation)
    return composite.derive_advisories(context, weather, water, disease, soil, result)


def test_a_quiet_week_produces_one_low_priority_advisory() -> None:
    drafts = advisories()

    assert len(drafts) == 1
    assert drafts[0].priority is AdvisoryPriority.low
    assert drafts[0].category is AdvisoryCategory.planting


def test_the_all_clear_names_what_was_not_assessed() -> None:
    """A quiet week is quiet for the evidence available. Claiming more would be an
    all-clear nobody verified."""
    drafts = advisories(soil=a_soil(assessed=False))

    assert "Not assessed" in drafts[0].body
    assert "soil suitability" in drafts[0].body.lower()


def test_no_advisory_mentions_simulation() -> None:
    """The stale claim: the water advisory asserted a "simulated soil water balance"
    even once the balance ran on real provider data."""
    every = [
        *advisories(water=a_water(score=60.0, depletion_mm=120.0, applied_irrigation_mm=40.0)),
        *advisories(),
    ]

    assert not any("simulated" in draft.body.lower() for draft in every)
    assert not any("simulated" in draft.rationale.lower() for draft in every)


# ---- irrigation ----


def test_irrigation_is_advised_from_the_computed_balance() -> None:
    thirsty = a_water(
        score=60.0, depletion_mm=120.0, applied_irrigation_mm=44.0, application_efficiency=0.9
    )

    draft = next(d for d in advisories(water=thirsty) if d.category is AdvisoryCategory.irrigation)

    assert "44 mm" in draft.title
    assert "120 mm" in draft.rationale
    assert "170 mm" in draft.rationale
    assert "85 mm" in draft.rationale
    assert draft.priority is AdvisoryPriority.high


def test_no_irrigation_advisory_without_a_computed_balance() -> None:
    """No evidence, no advice. An unassessed balance cannot recommend a depth."""
    drafts = advisories(water=a_water(score=90.0, assessed=False, applied_irrigation_mm=50.0))

    assert not any(d.category is AdvisoryCategory.irrigation for d in drafts)


def test_no_irrigation_advisory_when_nothing_needs_applying() -> None:
    drafts = advisories(water=a_water(score=40.0, applied_irrigation_mm=0.0))

    assert not any(d.category is AdvisoryCategory.irrigation for d in drafts)


def test_a_rainfed_farm_is_told_it_has_no_system() -> None:
    rainfed = a_water(
        score=60.0, applied_irrigation_mm=40.0, application_efficiency=None, depletion_mm=120.0
    )

    draft = next(d for d in advisories(water=rainfed) if d.category is AdvisoryCategory.irrigation)

    assert "rainfed" in draft.body


def test_approximate_coefficients_are_disclosed() -> None:
    approximate = a_water(
        score=60.0,
        applied_irrigation_mm=40.0,
        depletion_mm=120.0,
        parameters_source="category_default",
    )

    draft = next(
        d for d in advisories(water=approximate) if d.category is AdvisoryCategory.irrigation
    )

    assert "approximate" in draft.body.lower()


# ---- disease ----


def an_item(**kw) -> DiseaseItem:
    values = {
        "rule_id": "late_blight",
        "name": "Late blight",
        "pathogen": "Phytophthora infestans",
        "crop_code": "potato",
        "probability": 0.8,
        "level": RiskLevel.high,
        "matched_hours": 14,
        "triggering_conditions": (
            "14 consecutive hours at 10-24 °C with humidity at or above 90%",
        ),
        "preventive_actions": (),
        "scouting_advice": "Inspect lower leaves at dawn.",
    }
    values.update(kw)
    return DiseaseItem(**values)


def test_the_disease_advisory_names_the_matched_pathogen() -> None:
    sick = a_disease(score=60.0, items=(an_item(),))

    draft = next(d for d in advisories(disease=sick) if d.category is AdvisoryCategory.disease)

    assert "late blight" in draft.title.lower()
    assert "Phytophthora infestans" in draft.rationale
    assert "14 consecutive hours" in draft.rationale
    assert "Inspect lower leaves at dawn." in draft.body


def test_no_disease_advisory_without_a_matched_rule() -> None:
    """The fabricated-diagnosis case: the previous version fell back to a generic
    "foliar disease" whenever the score was high and the list was empty."""
    drafts = advisories(disease=a_disease(score=90.0, items=()))

    assert not any(d.category is AdvisoryCategory.disease for d in drafts)
    assert not any("foliar disease" in d.title.lower() for d in drafts)


def test_no_disease_advisory_when_conditions_were_not_assessed() -> None:
    drafts = advisories(disease=a_disease(score=90.0, assessed=False, items=(an_item(),)))

    assert not any(d.category is AdvisoryCategory.disease for d in drafts)


def test_disease_confidence_tracks_the_measured_probability() -> None:
    weak = advisories(disease=a_disease(score=40.0, items=(an_item(probability=0.5),)))
    strong = advisories(disease=a_disease(score=40.0, items=(an_item(probability=1.0),)))

    weak_draft = next(d for d in weak if d.category is AdvisoryCategory.disease)
    strong_draft = next(d for d in strong if d.category is AdvisoryCategory.disease)

    assert strong_draft.confidence > weak_draft.confidence


# ---- weather ----


def test_the_cold_advisory_uses_the_crops_own_threshold() -> None:
    """The stale claim: a flat "below 2 °C" regardless of crop understated the risk to
    a tropical crop injured at five degrees."""
    tropical = a_weather(score=60.0, frost_risk_days=3, cold_threshold_c=5.0)

    draft = next(d for d in advisories(weather=tropical) if "Cold damage" in d.title)

    assert "5 °C" in draft.rationale
    assert "2 °C" not in draft.rationale
    assert draft.priority is AdvisoryPriority.critical


def test_the_heat_advisory_uses_the_crops_own_threshold() -> None:
    hot = a_weather(score=40.0, heat_stress_days=5, heat_threshold_c=38.0)

    draft = next(d for d in advisories(weather=hot) if "heat stress" in d.title)

    assert "38 °C" in draft.rationale
    assert "optimal maximum" in draft.rationale
    assert draft.priority is AdvisoryPriority.high


def test_a_generic_heat_threshold_is_declared_as_generic() -> None:
    unplanted = a_weather(score=40.0, heat_stress_days=3, thresholds_source="generic")

    draft = next(d for d in advisories(weather=unplanted) if "heat stress" in d.title)

    assert "generic" in draft.rationale


def test_one_hot_day_is_not_worth_an_advisory() -> None:
    drafts = advisories(weather=a_weather(score=20.0, heat_stress_days=1))

    assert not any("heat stress" in d.title for d in drafts)


# ---- soil ----


def test_soil_limitations_become_advisories() -> None:
    constrained = a_soil(score=40.0, limitations=("pH 4.9 is below this crop's range",))

    draft = next(d for d in advisories(soil=constrained) if d.category is AdvisoryCategory.soil)

    assert "pH 4.9" in draft.body
    assert "40/100" in draft.rationale


def test_each_soil_advisory_names_its_own_constraint() -> None:
    """Two soil advisories once shared a generic title and an identical rationale, so
    they were indistinguishable in a prioritised list. The finding now leads."""
    two = a_soil(
        score=40.0,
        limitations=(
            "pH 4.9 is below this crop's range",
            "clay texture is not preferred by this crop",
        ),
    )

    drafts = [d for d in advisories(soil=two) if d.category is AdvisoryCategory.soil]

    assert len({d.title for d in drafts}) == 2
    assert "pH 4.9" in drafts[0].title
    assert "clay texture" in drafts[1].title


def test_a_long_constraint_is_trimmed_to_fit_the_title() -> None:
    """`Advisory.title` is capped by the schema; a long finding is trimmed, not
    rejected, so the advisory still reaches the farmer."""
    verbose = a_soil(score=40.0, limitations=("x" * 400,))

    draft = next(d for d in advisories(soil=verbose) if d.category is AdvisoryCategory.soil)

    assert len(draft.title) <= composite.MAX_TITLE_LENGTH
    assert draft.title.endswith("...")


def test_soil_advisories_are_capped() -> None:
    many = a_soil(score=30.0, limitations=tuple(f"limitation {n}" for n in range(6)))

    soil_drafts = [d for d in advisories(soil=many) if d.category is AdvisoryCategory.soil]

    assert len(soil_drafts) == composite.MAX_SOIL_ADVISORIES


def test_no_soil_advisory_without_a_measured_profile() -> None:
    drafts = advisories(soil=a_soil(assessed=False, limitations=("invented",)))

    assert not any(d.category is AdvisoryCategory.soil for d in drafts)


# ---- ordering and determinism ----


def test_advisories_are_ordered_by_priority() -> None:
    drafts = advisories(
        weather=a_weather(score=60.0, frost_risk_days=2, heat_stress_days=5),
        water=a_water(score=60.0, applied_irrigation_mm=40.0, depletion_mm=120.0),
        disease=a_disease(score=60.0, items=(an_item(),)),
        soil=a_soil(score=40.0, limitations=("a limitation",)),
    )

    order = [composite._PRIORITY_ORDER[d.priority] for d in drafts]

    assert order == sorted(order)
    assert drafts[0].priority is AdvisoryPriority.critical


def test_advisories_are_deterministic() -> None:
    """No RNG, no clock, no identity: the drafts are a pure function of the engines."""
    first = advisories(
        weather=a_weather(score=60.0, frost_risk_days=2),
        water=a_water(score=60.0, applied_irrigation_mm=40.0, depletion_mm=120.0),
    )

    for _ in range(5):
        assert (
            advisories(
                weather=a_weather(score=60.0, frost_risk_days=2),
                water=a_water(score=60.0, applied_irrigation_mm=40.0, depletion_mm=120.0),
            )
            == first
        )


def test_every_confidence_is_a_valid_probability() -> None:
    drafts = advisories(
        weather=a_weather(score=60.0, frost_risk_days=2, heat_stress_days=5),
        water=a_water(score=60.0, applied_irrigation_mm=40.0, depletion_mm=120.0),
        disease=a_disease(score=60.0, items=(an_item(),)),
        soil=a_soil(score=40.0, limitations=("a limitation",)),
    )

    assert all(0.0 <= d.confidence <= 1.0 for d in drafts)


def test_drafts_carry_no_identity_or_timestamp() -> None:
    """UUIDs and clocks belong to the service layer; a pure engine must not invent
    either."""
    fields = composite.AdvisoryDraft.__dataclass_fields__

    assert "id" not in fields
    assert "created_at" not in fields
    assert "farm_id" not in fields


def test_the_engine_module_imports_nothing_impure() -> None:
    from tests.unit.engine.test_engine_is_pure import violations

    source = composite.__file__ or ""
    assert source
    with open(source, encoding="utf-8") as handle:
        assert violations(handle.read()) == []
