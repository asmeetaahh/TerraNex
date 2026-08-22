"""Soil suitability scoring.

Two properties carry most of these tests. First, pH is judged against the *crop's*
range, so the same soil must suit one crop and not another. Second, organic carbon is
judged against what the *texture* can hold — the correction that stops every sandy soil
being reported as degraded.

The rest is missing-data discipline. The assessment this replaces read `soil.ph`
unconditionally; a real survey returns partial profiles, so absence has to be a
first-class outcome rather than a crash or a fabricated average.
"""

from datetime import date

import pytest

from app.engine import soil
from app.engine.context import AnalysisContext, CropParameters, SoilPoint
from app.engine.scoring import INSUFFICIENT

TODAY = date(2026, 8, 22)

# Maize tolerates 5.5-7.5 and prefers medium textures.
MAIZE = CropParameters(
    code="maize",
    category="cereal",
    ph_min=5.5,
    ph_max=7.5,
    preferred_textures=("loam", "silt_loam", "sandy_loam"),
    parameters_source="crop",
)

# Rice tolerates acidity maize does not, and prefers heavy ground maize does not.
RICE = CropParameters(
    code="rice",
    category="cereal",
    ph_min=5.0,
    ph_max=6.5,
    preferred_textures=("clay", "clay_loam", "silty_clay"),
)

LOAM = SoilPoint(
    ph=6.4,
    organic_carbon_pct=1.8,
    texture_class="loam",
    sand_pct=40.0,
    silt_pct=40.0,
    clay_pct=20.0,
    water_holding_capacity_mm=51.0,
)


def a_context(**overrides) -> AnalysisContext:
    values = {
        "farm_id": "f",
        "latitude": -21.1775,
        "longitude": -47.8103,
        "timezone": "UTC",
        "today": TODAY,
        "soil": LOAM,
        "crop": MAIZE,
        "growth_stage": "flowering",
        "irrigation_type": "rainfed",
    }
    values.update(overrides)
    return AnalysisContext(**values)


# --------------------------------------------------------------------------
# pH against the crop's range
# --------------------------------------------------------------------------


def test_ph_inside_the_range_is_optimal() -> None:
    score, status = soil.score_ph(6.4, (5.5, 7.5))

    assert status == "optimal"
    assert score == soil.IDEAL_SCORE


@pytest.mark.parametrize("ph", [5.5, 7.5])
def test_the_range_is_inclusive_at_both_edges(ph: float) -> None:
    _, status = soil.score_ph(ph, (5.5, 7.5))

    assert status == "optimal"


def test_ph_below_the_range_is_too_acidic() -> None:
    score, status = soil.score_ph(4.5, (5.5, 7.5))

    assert status == "too_acidic"
    assert score < soil.IDEAL_SCORE


def test_ph_above_the_range_is_too_alkaline() -> None:
    score, status = soil.score_ph(8.6, (5.5, 7.5))

    assert status == "too_alkaline"
    assert score < soil.IDEAL_SCORE


def test_the_penalty_grows_with_distance() -> None:
    """A status word alone cannot separate slightly acidic from severely acidic."""
    near, _ = soil.score_ph(5.0, (5.5, 7.5))
    far, _ = soil.score_ph(3.5, (5.5, 7.5))

    assert far < near < soil.IDEAL_SCORE


def test_ph_never_falls_below_its_floor() -> None:
    """pH is the one property here a farmer can amend, so it is never a zero."""
    score, _ = soil.score_ph(1.0, (5.5, 7.5))

    assert score == soil.PH_FLOOR


def test_the_same_soil_suits_one_crop_and_not_another() -> None:
    """The comparison a fixed pH window cannot make. At 5.2 the soil is inside rice's
    tolerance and below maize's."""
    acidic = SoilPoint(ph=5.2, organic_carbon_pct=1.8, texture_class="clay_loam")

    for_rice = soil.evaluate(a_context(soil=acidic, crop=RICE))
    for_maize = soil.evaluate(a_context(soil=acidic, crop=MAIZE))

    assert for_rice.ph_status == "optimal"
    assert for_maize.ph_status == "too_acidic"
    assert for_rice.score > for_maize.score


def test_a_crop_without_a_declared_range_uses_a_generic_one_and_says_so() -> None:
    window, source = soil.ph_window_for(None, None)

    assert window == (soil.GENERIC_PH_MIN, soil.GENERIC_PH_MAX)
    assert source == "generic"

    result = soil.evaluate(a_context(crop=CropParameters()))
    ph_factor = next(f for f in result.factors if f.key == "soil_ph")

    assert result.ph_window_source == "generic"
    assert "generic range" in ph_factor.explanation


# --------------------------------------------------------------------------
# Texture
# --------------------------------------------------------------------------


def test_a_preferred_texture_scores_well() -> None:
    score, status = soil.score_texture("loam", ("loam", "silt_loam"))

    assert status == "preferred"
    assert score == soil.IDEAL_SCORE


def test_an_unpreferred_texture_is_a_handicap_not_a_barrier() -> None:
    """Many crops list only two or three textures; an omission is not a prohibition."""
    score, status = soil.score_texture("clay", ("loam", "silt_loam"))

    assert status == "not_preferred"
    assert 0 < score < soil.IDEAL_SCORE


def test_no_stated_preference_scores_neutral_rather_than_perfect() -> None:
    """An absent preference is not evidence of a good match."""
    score, status = soil.score_texture("clay", ())

    assert status == "no_preference"
    assert score == soil.NO_PREFERENCE_TEXTURE_SCORE
    assert score < soil.IDEAL_SCORE


def test_an_unpreferred_texture_is_listed_as_a_limitation() -> None:
    heavy = SoilPoint(ph=6.4, organic_carbon_pct=2.6, texture_class="clay")

    result = soil.evaluate(a_context(soil=heavy, crop=MAIZE))

    assert any("not preferred" in item for item in result.limitations)


def test_the_same_texture_suits_rice_and_not_maize() -> None:
    heavy = SoilPoint(ph=6.2, organic_carbon_pct=2.6, texture_class="clay")

    for_rice = soil.evaluate(a_context(soil=heavy, crop=RICE))
    for_maize = soil.evaluate(a_context(soil=heavy, crop=MAIZE))

    assert for_rice.score > for_maize.score


# --------------------------------------------------------------------------
# Organic carbon, judged by texture
# --------------------------------------------------------------------------


def test_carbon_at_the_texture_expectation_is_adequate() -> None:
    score, status = soil.score_organic_carbon(1.8, expected_pct=1.8)

    assert status == "adequate"
    assert score == pytest.approx(soil.IDEAL_SCORE)


def test_the_same_carbon_is_good_in_sand_and_poor_in_clay() -> None:
    """The correction this scoring exists for. Judging both against one number reports
    half the world's soils as degraded for no reason."""
    carbon = 1.0

    in_sand, sand_status = soil.score_organic_carbon(carbon, soil.expected_organic_carbon("sand"))
    in_clay, clay_status = soil.score_organic_carbon(carbon, soil.expected_organic_carbon("clay"))

    assert in_sand > in_clay
    assert sand_status in {"adequate", "high"}
    assert clay_status == "low"


def test_a_sandy_soil_is_not_penalised_for_being_sandy() -> None:
    sandy = SoilPoint(ph=6.4, organic_carbon_pct=0.9, texture_class="sand")

    result = soil.evaluate(a_context(soil=sandy))

    assert result.organic_matter_status != "low"
    assert not any("Organic carbon is low" in item for item in result.limitations)


def test_a_genuinely_depleted_clay_is_flagged() -> None:
    depleted = SoilPoint(ph=6.4, organic_carbon_pct=0.6, texture_class="clay")

    result = soil.evaluate(a_context(soil=depleted))

    assert result.organic_matter_status == "low"
    assert any("Organic carbon is low" in item for item in result.limitations)


def test_carbon_far_above_expectation_is_capped() -> None:
    """Carbon beyond what the texture protects is not evidence of a better soil."""
    score, status = soil.score_organic_carbon(20.0, expected_pct=1.8)

    assert score == 100.0
    assert status == "high"


def test_an_unknown_texture_still_permits_a_carbon_judgement() -> None:
    assert soil.expected_organic_carbon(None) == soil.DEFAULT_EXPECTED_ORGANIC_CARBON_PCT
    assert soil.expected_organic_carbon("not_a_texture") == (
        soil.DEFAULT_EXPECTED_ORGANIC_CARBON_PCT
    )


# --------------------------------------------------------------------------
# Available water
# --------------------------------------------------------------------------


def test_more_retained_water_scores_higher() -> None:
    thin, _ = soil.score_available_water(15.0)
    deep, _ = soil.score_available_water(60.0)

    assert deep > thin


def test_low_retention_is_listed_as_a_limitation() -> None:
    thin = SoilPoint(
        ph=6.4, organic_carbon_pct=1.0, texture_class="sand", water_holding_capacity_mm=12.0
    )

    result = soil.evaluate(a_context(soil=thin))

    assert result.fertility_status == "low"
    assert any("water retention" in item.lower() for item in result.limitations)


def test_absent_retention_simply_drops_out_of_the_composite() -> None:
    """Not every survey reports it, and its absence should not mark the soil unknown."""
    without = SoilPoint(ph=6.4, organic_carbon_pct=1.8, texture_class="loam")

    result = soil.evaluate(a_context(soil=without))

    assert result.sufficient
    assert not any(f.key == "available_water" for f in result.factors)


# --------------------------------------------------------------------------
# Missing data
# --------------------------------------------------------------------------


def test_an_entirely_missing_soil_is_insufficient() -> None:
    """Never invented. A fabricated profile drives real advice about a real field."""
    result = soil.evaluate(a_context(soil=SoilPoint()))

    assert not result.sufficient
    assert result.explanation.startswith(INSUFFICIENT)
    assert set(result.missing) == {"pH", "organic carbon", "texture"}
    assert all(f.weight == 0.0 for f in result.factors)


def test_an_insufficient_result_reports_no_invented_values() -> None:
    result = soil.evaluate(a_context(soil=SoilPoint()))

    assert result.texture_class is None
    assert result.ph_status is None
    assert result.organic_matter_status is None
    assert result.limitations == ()


@pytest.mark.parametrize(
    ("present", "expected_missing"),
    [
        ({"ph": 6.4}, {"organic carbon", "texture"}),
        ({"organic_carbon_pct": 1.8}, {"pH", "texture"}),
        ({"texture_class": "loam"}, {"pH", "organic carbon"}),
    ],
)
def test_a_partial_profile_scores_what_it_has(present: dict, expected_missing: set) -> None:
    """One measurement is enough to say something, and the rest is declared absent."""
    result = soil.evaluate(a_context(soil=SoilPoint(**present)))

    assert result.sufficient
    assert set(result.missing) == expected_missing
    assert INSUFFICIENT in result.explanation


def test_the_composite_renormalises_over_what_was_measured() -> None:
    """A farm must not score badly for the sin of having an incomplete survey."""
    full = soil.evaluate(a_context(soil=LOAM))
    ph_only = soil.evaluate(a_context(soil=SoilPoint(ph=6.4)))

    assert ph_only.score == pytest.approx(soil.IDEAL_SCORE)
    assert ph_only.score >= full.score


def test_missing_properties_carry_zero_weight() -> None:
    result = soil.evaluate(a_context(soil=SoilPoint(ph=6.4)))

    unmeasured = [f for f in result.factors if f.weight == 0.0]

    assert {f.key for f in unmeasured} == {"organic_carbon", "texture"}
    assert all(INSUFFICIENT in f.explanation for f in unmeasured)


# --------------------------------------------------------------------------
# Determinism, hemispheres, purity
# --------------------------------------------------------------------------


def test_the_same_context_produces_an_identical_result() -> None:
    context = a_context()

    assert soil.evaluate(context) == soil.evaluate(context)


def test_two_equal_contexts_produce_equal_results() -> None:
    assert soil.evaluate(a_context()) == soil.evaluate(a_context())


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
def test_the_assessment_runs_at_every_brics_latitude(latitude: float) -> None:
    """Soil scoring reads no coordinate at all, so the score must not move with one."""
    result = soil.evaluate(a_context(latitude=latitude))

    assert result.sufficient
    assert 0.0 <= result.score <= 100.0


def test_location_does_not_change_the_score() -> None:
    """Stated directly: identical soil scores identically anywhere on Earth."""
    north = soil.evaluate(a_context(latitude=52.0, longitude=13.0))
    south = soil.evaluate(a_context(latitude=-33.0, longitude=18.0))

    assert north.score == south.score
    assert north.factors == south.factors


def test_the_engine_module_imports_nothing_impure() -> None:
    from tests.unit.engine.test_engine_is_pure import violations

    source = soil.__file__ or ""
    assert source
    with open(source, encoding="utf-8") as handle:
        assert violations(handle.read()) == []
