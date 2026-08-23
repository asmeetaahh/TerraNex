"""Crop suitability reason codes: the numbers behind the ranking.

A recommendation says *"Prefers pH 5.5-7.0; farm reads 5.8"*. All three figures were held
by the scorer and then formatted away — `CropRecommendation` publishes neither the site's
pH nor the crop's tolerated band, so a consumer wanting to say that in Hindi had to parse
the English or re-derive the comparison.

Eight keys, one pair per scoring component, matching the four `ScoredFactor` keys the
recommender already publishes. `factors[].score` gives the magnitude; these give the
direction and the measurements.

The rule these tests defend is that **a reason states nothing the prose does not**, and
that adding them moved no score, no rank and no sentence —
`test_scores_and_prose_are_unchanged_by_the_reason_layer` is the guard.
"""

from app.engine import recommendations as rec
from app.engine.context import CropParameters
from app.engine.reasons import (
    CROP_PH_OUTSIDE_RANGE,
    CROP_PH_WITHIN_RANGE,
    CROP_TEMPERATURE_OPTIMAL,
    CROP_TEMPERATURE_OUTSIDE,
    CROP_TEXTURE_MATCH,
    CROP_TEXTURE_MISMATCH,
    CROP_WATER_SHORTFALL,
    CROP_WATER_SUFFICIENT,
)

CROP_KEYS = {
    CROP_PH_WITHIN_RANGE,
    CROP_PH_OUTSIDE_RANGE,
    CROP_TEMPERATURE_OPTIMAL,
    CROP_TEMPERATURE_OUTSIDE,
    CROP_TEXTURE_MATCH,
    CROP_TEXTURE_MISMATCH,
    CROP_WATER_SUFFICIENT,
    CROP_WATER_SHORTFALL,
}


def a_crop(**overrides) -> CropParameters:
    base = {
        "code": "sorghum",
        "name": "Sorghum",
        "ph_min": 5.5,
        "ph_max": 7.5,
        "optimal_temp_min_c": 24.0,
        "optimal_temp_max_c": 32.0,
        "preferred_textures": ("loam", "sandy_loam"),
        "water_need_mm_season": 500.0,
    }
    base.update(overrides)
    return CropParameters(**base)


def a_site(**overrides) -> rec.SiteConditions:
    base = {
        "soil_ph": 6.5,
        "mean_temp_c": 28.0,
        "texture_class": "loam",
        "seasonal_rainfall_mm": 600.0,
    }
    base.update(overrides)
    return rec.SiteConditions(**base)


def only(reasons):
    assert len(reasons) == 1, f"expected one reason, got {len(reasons)}"
    return reasons[0]


# --------------------------------------------------------------------------
# pH
# --------------------------------------------------------------------------


def test_ph_inside_the_band_emits_the_within_key() -> None:
    _, _, _, reasons = rec._ph_component(a_crop(), a_site(soil_ph=6.5))

    reason = only(reasons)
    assert reason.key == CROP_PH_WITHIN_RANGE
    assert reason.params == {"site_ph": 6.5, "crop_ph_min": 5.5, "crop_ph_max": 7.5}


def test_ph_outside_the_band_emits_the_outside_key_with_the_same_params() -> None:
    """Direction changes; the measurements do not. A consumer renders both from one
    template family."""
    _, _, _, reasons = rec._ph_component(a_crop(), a_site(soil_ph=4.2))

    reason = only(reasons)
    assert reason.key == CROP_PH_OUTSIDE_RANGE
    assert reason.params == {"site_ph": 4.2, "crop_ph_min": 5.5, "crop_ph_max": 7.5}


def test_no_soil_ph_emits_no_reason() -> None:
    """`unknown_factor` already reports the missing evidence. A reason with nothing in it
    would look like a finding."""
    _, _, _, reasons = rec._ph_component(a_crop(), a_site(soil_ph=None))

    assert reasons == ()


def test_a_crop_without_a_ph_range_emits_no_reason() -> None:
    _, _, _, reasons = rec._ph_component(a_crop(ph_min=None, ph_max=None), a_site())

    assert reasons == ()


# --------------------------------------------------------------------------
# Temperature
# --------------------------------------------------------------------------


def test_temperature_inside_the_band() -> None:
    _, _, _, reasons = rec._temperature_component(a_crop(), a_site(mean_temp_c=28.0))

    reason = only(reasons)
    assert reason.key == CROP_TEMPERATURE_OPTIMAL
    assert reason.params == {
        "site_mean_temp_c": 28.0,
        "crop_optimal_min_c": 24.0,
        "crop_optimal_max_c": 32.0,
    }


def test_temperature_outside_the_band() -> None:
    _, _, _, reasons = rec._temperature_component(a_crop(), a_site(mean_temp_c=12.0))

    assert only(reasons).key == CROP_TEMPERATURE_OUTSIDE


def test_unmeasured_temperature_emits_no_reason() -> None:
    _, _, _, reasons = rec._temperature_component(a_crop(), a_site(mean_temp_c=None))

    assert reasons == ()


# --------------------------------------------------------------------------
# Texture
# --------------------------------------------------------------------------


def test_a_preferred_texture_emits_the_match_key() -> None:
    _, _, _, reasons = rec._texture_component(a_crop(), a_site(texture_class="loam"))

    reason = only(reasons)
    assert reason.key == CROP_TEXTURE_MATCH
    assert reason.params["site_texture_class"] == "loam"


def test_an_unpreferred_texture_emits_the_mismatch_key() -> None:
    _, _, _, reasons = rec._texture_component(a_crop(), a_site(texture_class="clay"))

    assert only(reasons).key == CROP_TEXTURE_MISMATCH


def test_preferred_textures_travel_as_machine_codes_not_display_text() -> None:
    """`params` admits scalars only, so the tuple is joined — but as codes, so a consumer
    maps each to its own word. Joining the display strings would have been untranslatable.
    """
    _, _, _, reasons = rec._texture_component(a_crop(), a_site(texture_class="clay"))

    preferred = only(reasons).params["crop_preferred_textures"]
    assert preferred == "loam,sandy_loam"
    assert " " not in preferred, "underscored codes, not humanised labels"


def test_unmeasured_texture_emits_no_reason() -> None:
    _, _, _, reasons = rec._texture_component(a_crop(), a_site(texture_class=None))

    assert reasons == ()


def test_a_crop_without_texture_preferences_emits_no_reason() -> None:
    _, _, _, reasons = rec._texture_component(a_crop(preferred_textures=()), a_site())

    assert reasons == ()


# --------------------------------------------------------------------------
# Water
# --------------------------------------------------------------------------


def test_rainfall_covering_the_requirement_emits_sufficient() -> None:
    _, _, _, reasons = rec._water_component(a_crop(), a_site(seasonal_rainfall_mm=600.0))

    reason = only(reasons)
    assert reason.key == CROP_WATER_SUFFICIENT
    assert reason.params == {
        "seasonal_rainfall_mm": 600.0,
        "crop_water_need_mm_season": 500.0,
    }


def test_rainfall_below_the_shortfall_threshold_emits_shortfall() -> None:
    _, _, _, reasons = rec._water_component(a_crop(), a_site(seasonal_rainfall_mm=200.0))

    assert only(reasons).key == CROP_WATER_SHORTFALL


def test_the_middle_band_emits_no_reason_because_the_prose_does_not() -> None:
    """Between the shortfall threshold and parity the existing code raises neither a
    strength nor a consideration — enough rain to be worth no warning, not enough to be
    worth a claim. The structured form mirrors that silence rather than inventing a
    verdict the prose declines to give.
    """
    _, _, strengths, considerations = (
        rec._water_component(a_crop(), a_site(seasonal_rainfall_mm=400.0))[i] for i in (0, 3, 1, 2)
    )
    _, _, _, reasons = rec._water_component(a_crop(), a_site(seasonal_rainfall_mm=400.0))

    ratio = 400.0 / 500.0
    assert rec.WATER_SHORTFALL_RATIO <= ratio < 1.0, "this is the middle band"
    assert reasons == ()


def test_unmeasured_rainfall_emits_no_reason() -> None:
    _, _, _, reasons = rec._water_component(a_crop(), a_site(seasonal_rainfall_mm=None))

    assert reasons == ()


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------


def test_a_fully_measured_site_emits_one_reason_per_component() -> None:
    _, _, _, _, reasons = rec.score_crop(a_crop(), a_site())

    assert len(reasons) == 4
    assert {r.key for r in reasons} == {
        CROP_PH_WITHIN_RANGE,
        CROP_TEMPERATURE_OPTIMAL,
        CROP_TEXTURE_MATCH,
        CROP_WATER_SUFFICIENT,
    }


def test_reasons_follow_component_order() -> None:
    """pH, temperature, texture, water — the order `factors` already uses, so the two
    forms line up."""
    _, _, _, _, reasons = rec.score_crop(a_crop(), a_site())

    assert [r.key.split(".")[1].split("_")[0] for r in reasons] == [
        "ph",
        "temperature",
        "texture",
        "water",
    ]


def test_an_unmeasured_component_drops_only_its_own_reason() -> None:
    _, _, _, _, reasons = rec.score_crop(a_crop(), a_site(soil_ph=None))

    keys = {r.key for r in reasons}
    assert CROP_PH_WITHIN_RANGE not in keys
    assert CROP_PH_OUTSIDE_RANGE not in keys
    assert len(reasons) == 3


def test_every_key_is_from_the_approved_vocabulary() -> None:
    _, _, _, _, reasons = rec.score_crop(a_crop(), a_site())

    for reason in reasons:
        assert reason.key in CROP_KEYS, reason.key
        assert reason.key.startswith("crop.")


def test_every_param_value_is_a_scalar() -> None:
    """The published type admits float/int/str/None only."""
    _, _, _, _, reasons = rec.score_crop(a_crop(), a_site())

    for reason in reasons:
        for name, value in reason.params.items():
            assert isinstance(value, float | int | str), f"{name}={value!r}"


def test_reasons_add_no_agronomy_of_their_own() -> None:
    """The guard against drift: every param must be traceable to the crop or the site."""
    crop, site = a_crop(), a_site()
    _, _, _, _, reasons = rec.score_crop(crop, site)
    params = {k: v for r in reasons for k, v in r.params.items()}

    assert params == {
        "site_ph": site.soil_ph,
        "crop_ph_min": crop.ph_min,
        "crop_ph_max": crop.ph_max,
        "site_mean_temp_c": site.mean_temp_c,
        "crop_optimal_min_c": crop.optimal_temp_min_c,
        "crop_optimal_max_c": crop.optimal_temp_max_c,
        "site_texture_class": site.texture_class,
        "crop_preferred_textures": ",".join(crop.preferred_textures),
        "seasonal_rainfall_mm": site.seasonal_rainfall_mm,
        "crop_water_need_mm_season": crop.water_need_mm_season,
    }


# --------------------------------------------------------------------------
# Backward compatibility
# --------------------------------------------------------------------------


def test_scores_and_prose_are_unchanged_by_the_reason_layer() -> None:
    """This phase carries evidence; it does not score. A moved composite would change
    every ranking in the product."""
    composite, factors, strengths, considerations, _ = rec.score_crop(a_crop(), a_site())

    # Pinned as a constant rather than restated as a formula: a formula copied into the
    # test would drift in step with the code it is meant to guard. 93.2 is
    # pH 95×0.3 + temp 95×0.3 + texture 92×0.2 + water 89×0.2, where water scores 89
    # because 600/500 = 1.2 costs 0.2 × WATER_PENALTY_PER_RATIO.
    assert composite == 93.2
    assert strengths == (
        "Tolerates the farm's pH of 6.5",
        "Mean temperature of 28 °C is in its optimal band",
        "Suits loam soils",
        "Observed rainfall covers its seasonal water requirement",
    )
    assert considerations == ()
    assert [f.key for f in factors] == [
        "ph_match",
        "temperature_match",
        "texture_match",
        "water_match",
    ]


def test_the_reason_agrees_with_the_consideration_it_sits_beside() -> None:
    """Two forms of one fact. If they disagree, the reason is wrong."""
    _, _, _, considerations, reasons = rec.score_crop(a_crop(), a_site(soil_ph=4.2))

    params = only([r for r in reasons if r.key == CROP_PH_OUTSIDE_RANGE]).params
    sentence = next(c for c in considerations if "pH" in c)

    assert f"{params['site_ph']:g}" in sentence
    assert f"{params['crop_ph_min']:g}" in sentence
    assert f"{params['crop_ph_max']:g}" in sentence
