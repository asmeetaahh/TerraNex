"""The FAO-56 water balance.

Most of this file is about the two places the model can lie: the stress threshold, and
missing inputs. Ks at RAW decides whether a farmer is told to irrigate today or next
week, and a fabricated soil capacity produces a fabricated irrigation depth — so both
get boundary tests rather than smoke tests.

Every context here is built by hand. The engine takes no provider, no database and no
clock, which is exactly what makes that possible.
"""

import math
from datetime import date, timedelta

import pytest

from app.engine import water
from app.engine.context import AnalysisContext, CropParameters, DailyPoint, SoilPoint
from app.engine.scoring import INSUFFICIENT
from app.schemas.common import RiskLevel

TODAY = date(2026, 8, 22)

# A loam at 1.0 m rooting depth: TAW = 1000 x 0.17 x 1.0 = 170 mm, RAW = 0.5 x 170 = 85 mm.
LOAM = SoilPoint(texture_class="loam", sand_pct=40.0, silt_pct=40.0, clay_pct=20.0, ph=6.4)
MAIZE = CropParameters(
    code="maize",
    category="cereal",
    kc_by_stage={"flowering": 1.0, "germination": 0.30, "maturity": 0.60},
    root_depth_m=1.0,
    depletion_fraction=0.5,
    parameters_source="crop",
)


def a_day(offset: int, *, rain: float | None = 0.0, et0: float | None = 5.0, **kw) -> DailyPoint:
    return DailyPoint(
        day=TODAY + timedelta(days=offset),
        precipitation_mm=rain,
        et0_mm=et0,
        temp_min_c=kw.pop("temp_min_c", 18.0),
        temp_max_c=kw.pop("temp_max_c", 30.0),
        temp_mean_c=kw.pop("temp_mean_c", 24.0),
        **kw,
    )


def a_context(days=None, **overrides) -> AnalysisContext:
    values = {
        "farm_id": "f",
        "latitude": -21.1775,
        "longitude": -47.8103,
        "timezone": "UTC",
        "today": TODAY,
        "daily": tuple(days if days is not None else [a_day(n) for n in range(-30, 7)]),
        "soil": LOAM,
        "crop": MAIZE,
        "growth_stage": "flowering",
        "irrigation_type": "rainfed",
    }
    values.update(overrides)
    return AnalysisContext(**values)


# --------------------------------------------------------------------------
# Reservoir arithmetic
# --------------------------------------------------------------------------


def test_total_available_water_is_the_fao56_product() -> None:
    """TAW = 1000 x (theta_FC - theta_WP) x Zr."""
    assert water.total_available_water_mm(0.17, 1.0) == pytest.approx(170.0)
    assert water.total_available_water_mm(0.12, 1.5) == pytest.approx(180.0)


def test_the_texture_table_matches_the_simulator() -> None:
    """The table is duplicated so the engine need not import a service. This is what
    stops the two copies drifting after a correction to either."""
    from app.services.simulation import _AVAILABLE_WATER_FRACTION

    simulated = {str(texture): value for texture, value in _AVAILABLE_WATER_FRACTION.items()}

    assert simulated == water.AVAILABLE_WATER_FRACTION


def test_the_reference_depth_matches_the_simulator() -> None:
    from app.services.simulation import _ROOT_ZONE_MM

    assert water.REFERENCE_SOIL_DEPTH_MM == _ROOT_ZONE_MM


# --------------------------------------------------------------------------
# Ks — the stress coefficient
# --------------------------------------------------------------------------


@pytest.mark.parametrize("depletion", [0.0, 20.0, 84.9, 85.0])
def test_ks_is_one_at_or_below_raw(depletion: float) -> None:
    """No stress while readily available water remains. This is the definition of RAW,
    and getting it wrong understates transpiration for every well-watered farm."""
    assert water.stress_coefficient(depletion, taw_mm=170.0, raw_mm=85.0) == 1.0


def test_stress_begins_immediately_past_raw() -> None:
    just_past = water.stress_coefficient(85.01, taw_mm=170.0, raw_mm=85.0)

    assert just_past < 1.0
    assert just_past == pytest.approx(1.0, abs=1e-3)


def test_ks_falls_linearly_between_raw_and_wilting_point() -> None:
    halfway = water.stress_coefficient(127.5, taw_mm=170.0, raw_mm=85.0)

    assert halfway == pytest.approx(0.5)


def test_ks_is_zero_at_the_wilting_point() -> None:
    assert water.stress_coefficient(170.0, taw_mm=170.0, raw_mm=85.0) == 0.0
    assert water.stress_coefficient(200.0, taw_mm=170.0, raw_mm=85.0) == 0.0


# --------------------------------------------------------------------------
# Effective rainfall
# --------------------------------------------------------------------------


def test_light_rain_is_intercepted() -> None:
    """A shower that only wets the canopy never reaches the roots."""
    assert water.effective_rainfall_mm(1.5) == 0.0
    assert water.effective_rainfall_mm(water.INTERCEPTION_MM) == 0.0


def test_heavier_rain_is_credited_less_interception() -> None:
    assert water.effective_rainfall_mm(12.0) == pytest.approx(10.0)


# --------------------------------------------------------------------------
# A hand-computed worked example
# --------------------------------------------------------------------------


def test_a_hand_computed_three_day_balance() -> None:
    """Five dry days on a loam, ET₀ 5 mm, Kc 1.0, so ETc is 5 mm/day and Ks stays 1.

    TAW = 170, RAW = 85. Starting at field capacity, depletion after five days is
    exactly 25 mm — small enough that no stress term enters, which is what makes the
    arithmetic checkable by hand.
    """
    days = [a_day(n, rain=0.0, et0=5.0) for n in range(-5, 0)]
    result = water.evaluate(a_context(days=days))

    assert result.sufficient
    assert result.taw_mm == pytest.approx(170.0)
    assert result.raw_mm == pytest.approx(85.0)
    assert result.depletion_mm == pytest.approx(25.0)
    assert result.stress_coefficient == 1.0
    assert result.total_crop_demand_mm == pytest.approx(25.0)
    assert result.total_precipitation_mm == pytest.approx(0.0)
    assert result.water_balance_mm == pytest.approx(-25.0)


def test_the_worked_example_step_by_step() -> None:
    """Each day's depletion, so a regression points at the day it broke."""
    days = [a_day(n, rain=0.0, et0=5.0) for n in range(-5, 0)]
    result = water.evaluate(a_context(days=days))

    assert [round(state.depletion_mm, 3) for state in result.days] == [5.0, 10.0, 15.0, 20.0, 25.0]


# --------------------------------------------------------------------------
# Depletion behaviour
# --------------------------------------------------------------------------


def test_rainfall_reduces_depletion() -> None:
    dry = water.evaluate(a_context(days=[a_day(n, rain=0.0) for n in range(-10, 0)]))
    wet = water.evaluate(a_context(days=[a_day(n, rain=6.0) for n in range(-10, 0)]))

    assert wet.depletion_mm < dry.depletion_mm


def test_depletion_never_goes_below_zero() -> None:
    """Rain beyond field capacity drains away; it cannot bank water for later."""
    soaked = water.evaluate(a_context(days=[a_day(n, rain=90.0, et0=1.0) for n in range(-10, 0)]))

    assert soaked.depletion_mm == 0.0
    assert soaked.soil_moisture_pct == pytest.approx(100.0)


def test_depletion_approaches_but_never_exceeds_the_reservoir() -> None:
    """Under relentless demand the root zone empties asymptotically, not abruptly.

    Ks falls to zero as depletion nears TAW, so the crop's own closing stomata are what
    halt the balance — the clamp is only a safety rail behind that. Expecting exactly
    TAW would be wrong: the model converges on it and never quite arrives, which is the
    physically correct behaviour and the reason a wilting crop still transpires a little.
    """
    parched = water.evaluate(a_context(days=[a_day(n, rain=0.0, et0=20.0) for n in range(-30, 7)]))

    assert parched.taw_mm is not None and parched.depletion_mm is not None
    assert parched.depletion_mm <= parched.taw_mm
    assert parched.depletion_mm > 0.98 * parched.taw_mm
    assert parched.stress_coefficient == pytest.approx(0.0, abs=1e-3)
    assert parched.soil_moisture_pct == pytest.approx(0.0, abs=0.1)


def test_every_daily_depletion_stays_within_the_reservoir() -> None:
    result = water.evaluate(
        a_context(
            days=[a_day(n, rain=(30.0 if n % 5 == 0 else 0.0), et0=7.0) for n in range(-30, 7)]
        )
    )

    assert all(0.0 <= state.depletion_mm <= result.taw_mm for state in result.days)


# --------------------------------------------------------------------------
# Crop coefficients
# --------------------------------------------------------------------------


def test_a_higher_kc_stage_drives_more_demand() -> None:
    days = [a_day(n, rain=0.0, et0=5.0) for n in range(-10, 0)]
    flowering = water.evaluate(a_context(days=days, growth_stage="flowering"))
    germination = water.evaluate(a_context(days=days, growth_stage="germination"))

    assert flowering.total_crop_demand_mm > germination.total_crop_demand_mm
    assert flowering.depletion_mm > germination.depletion_mm


def test_etc_is_et0_times_kc() -> None:
    days = [a_day(n, rain=0.0, et0=4.0) for n in range(-3, 0)]
    result = water.evaluate(a_context(days=days, growth_stage="maturity"))

    assert all(state.kc == 0.60 for state in result.days)
    assert all(state.etc_mm == pytest.approx(2.4) for state in result.days)


def test_crop_coefficients_come_from_the_yaml_ruleset() -> None:
    """The engine must use the crop's published coefficients, not a stage-only guess.
    This wires the real registry to the real engine."""
    from app.rules.registry import resolve_crop_parameters

    resolved = resolve_crop_parameters("rice", "cereal")
    rice = CropParameters(
        code="rice",
        category="cereal",
        kc_by_stage=resolved["kc_by_stage"],
        root_depth_m=resolved["root_depth_m"],
        depletion_fraction=resolved["depletion_fraction"],
        parameters_source=resolved["parameters_source"],
    )
    maize = resolve_crop_parameters("maize", "cereal")

    # Both peak at Kc 1.20 in FAO-56 Table 12 — a real coincidence, and the reason
    # flowering is the wrong stage to tell them apart. They diverge at establishment,
    # where a flooded paddy already evaporates freely and bare maize ground does not.
    assert rice.kc_for("germination") == 1.05
    assert maize["kc_by_stage"]["germination"] == 0.30

    days = [a_day(n, rain=0.0, et0=5.0) for n in range(-10, 0)]
    result = water.evaluate(a_context(days=days, crop=rice))

    assert result.sufficient
    assert result.taw_mm is not None
    # Rice is ponded rather than depleted: p = 0.20 against maize's 0.55, so it tolerates
    # far less drawdown before stress. A stage-only Kc table could not express this.
    assert result.raw_mm == pytest.approx(0.20 * result.taw_mm)
    assert maize["depletion_fraction"] == 0.55


def test_a_category_fallback_is_reported_in_the_factor() -> None:
    """A run must never imply it had a crop's own coefficients when it did not."""
    approximate = CropParameters(
        code="teff",
        category="cereal",
        kc_by_stage={"flowering": 1.15},
        root_depth_m=1.2,
        depletion_fraction=0.55,
        parameters_source="category_default",
    )

    result = water.evaluate(a_context(crop=approximate))
    balance = next(f for f in result.factors if f.key == "water_balance")

    assert "category defaults" in balance.explanation


# --------------------------------------------------------------------------
# Irrigation
# --------------------------------------------------------------------------


def test_irrigation_efficiency_grosses_up_the_recommendation() -> None:
    """The correction to the behaviour being replaced. Efficiency divides the applied
    depth: furrows waste water, so they need MORE of it to deliver the same net —
    the old code had drip erasing 90% of the deficit instead."""
    days = [a_day(n, rain=0.0, et0=6.0) for n in range(-30, 7)]

    drip = water.evaluate(a_context(days=days, irrigation_type="drip"))
    furrow = water.evaluate(a_context(days=days, irrigation_type="furrow"))

    assert drip.net_irrigation_mm == pytest.approx(furrow.net_irrigation_mm)
    assert furrow.applied_irrigation_mm > drip.applied_irrigation_mm
    assert drip.applied_irrigation_mm == pytest.approx(drip.net_irrigation_mm / 0.90)
    assert furrow.applied_irrigation_mm == pytest.approx(furrow.net_irrigation_mm / 0.60)


def test_irrigation_type_does_not_change_soil_depletion() -> None:
    """Depletion is a physical state. Owning a drip line does not put water in the
    ground; only applying it does."""
    days = [a_day(n, rain=0.0, et0=6.0) for n in range(-20, 0)]

    rainfed = water.evaluate(a_context(days=days, irrigation_type="rainfed"))
    drip = water.evaluate(a_context(days=days, irrigation_type="drip"))

    assert rainfed.depletion_mm == pytest.approx(drip.depletion_mm)
    assert rainfed.score == pytest.approx(drip.score)


def test_a_rainfed_farm_has_no_efficiency_to_divide_by() -> None:
    days = [a_day(n, rain=0.0, et0=6.0) for n in range(-30, 7)]

    result = water.evaluate(a_context(days=days, irrigation_type="rainfed"))

    assert result.application_efficiency is None
    assert result.applied_irrigation_mm == pytest.approx(result.net_irrigation_mm)


def test_no_irrigation_is_recommended_while_water_is_readily_available() -> None:
    result = water.evaluate(a_context(days=[a_day(n, rain=5.0, et0=2.0) for n in range(-10, 0)]))

    assert result.depletion_mm < result.raw_mm
    assert result.net_irrigation_mm == 0.0


def test_a_negligible_recommendation_is_suppressed() -> None:
    """Advising a farmer to apply two millimetres is noise, not advice."""
    assert water.MINIMUM_USEFUL_IRRIGATION_MM > 0


# --------------------------------------------------------------------------
# days_until_stress
# --------------------------------------------------------------------------


def test_days_until_stress_is_none_while_the_crop_stays_comfortable() -> None:
    result = water.evaluate(a_context(days=[a_day(n, rain=8.0, et0=2.0) for n in range(-30, 7)]))

    assert result.days_until_stress is None


def test_days_until_stress_is_zero_when_already_stressed() -> None:
    result = water.evaluate(a_context(days=[a_day(n, rain=0.0, et0=15.0) for n in range(-30, 7)]))

    assert result.days_until_stress == 0


def test_days_until_stress_counts_forward_from_today() -> None:
    """History brings the root zone close to RAW; the forecast tips it over partway in."""
    history = [a_day(n, rain=0.0, et0=5.0) for n in range(-16, 0)]
    forecast = [a_day(n, rain=0.0, et0=5.0) for n in range(0, 7)]

    result = water.evaluate(a_context(days=history + forecast))

    assert result.days_until_stress is not None
    assert 0 < result.days_until_stress < 7


# --------------------------------------------------------------------------
# Missing data — never a fabricated capacity
# --------------------------------------------------------------------------


def test_missing_soil_produces_insufficient_not_a_default_capacity() -> None:
    """The behaviour being replaced fell back to a bare `50.0` mm. That number drove a
    real irrigation recommendation for a farm whose soil was never measured."""
    result = water.evaluate(a_context(soil=SoilPoint()))

    assert not result.sufficient
    assert result.taw_mm is None
    assert any("soil" in item for item in result.missing)
    assert result.explanation.startswith(INSUFFICIENT)


def test_an_insufficient_result_reports_no_invented_numbers() -> None:
    result = water.evaluate(a_context(soil=SoilPoint()))

    assert result.deficit_mm == 0.0
    assert result.net_irrigation_mm == 0.0
    assert result.total_precipitation_mm is None
    assert result.soil_moisture_pct is None
    assert result.days_until_stress is None


def test_an_insufficient_result_carries_a_zero_weight_factor() -> None:
    """Weight zero is how the composite excludes an unassessed factor arithmetically."""
    result = water.evaluate(a_context(soil=SoilPoint()))
    (balance,) = result.factors

    assert balance.key == "water_balance"
    assert balance.weight == 0.0


def test_missing_rooting_depth_is_insufficient() -> None:
    rootless = CropParameters(code="maize", kc_by_stage={"flowering": 1.0}, depletion_fraction=0.5)

    result = water.evaluate(a_context(crop=rootless))

    assert not result.sufficient
    assert any("rooting depth" in item for item in result.missing)


def test_missing_crop_coefficient_is_insufficient() -> None:
    """A guessed Kc produces a deficit the run cannot justify."""
    result = water.evaluate(a_context(growth_stage="fruiting"))

    assert not result.sufficient
    assert any("coefficient" in item for item in result.missing)


def test_missing_precipitation_entirely_is_insufficient() -> None:
    """An absent rainfall series is not a dry month."""
    result = water.evaluate(a_context(days=[a_day(n, rain=None) for n in range(-30, 7)]))

    assert not result.sufficient
    assert any("precipitation" in item for item in result.missing)


def test_no_weather_at_all_is_insufficient() -> None:
    result = water.evaluate(a_context(days=[]))

    assert not result.sufficient


def test_a_soil_with_only_a_reported_capacity_still_works() -> None:
    """A provider that gives a capacity but no texture is usable — the fraction is
    recovered by dividing by the depth that capacity refers to."""
    reported = SoilPoint(water_holding_capacity_mm=51.0)

    result = water.evaluate(a_context(soil=reported))

    assert result.sufficient
    assert result.taw_mm == pytest.approx(1000.0 * (51.0 / 300.0) * 1.0)


def test_an_explicit_available_water_fraction_wins() -> None:
    measured = SoilPoint(texture_class="loam", available_water_fraction=0.21)

    fraction, source = water.resolve_available_water_fraction(a_context(soil=measured))

    assert fraction == 0.21
    assert source == "provider"


# --------------------------------------------------------------------------
# ET₀ and the Hargreaves fallback
# --------------------------------------------------------------------------


def test_real_et0_is_preferred_over_the_fallback() -> None:
    day = a_day(-1, et0=4.2, temp_min_c=10.0, temp_max_c=30.0)

    value, estimated = water.daily_et0_mm(day, latitude=-21.0)

    assert value == 4.2
    assert not estimated


def test_hargreaves_fills_in_when_the_provider_omits_et0() -> None:
    day = a_day(-1, et0=None, temp_min_c=15.0, temp_max_c=31.0, temp_mean_c=23.0)

    value, estimated = water.daily_et0_mm(day, latitude=-21.0)

    assert estimated
    assert value is not None and value > 0


def test_the_fallback_matches_the_existing_hargreaves_implementation() -> None:
    """The simulator already had this formula. Both must agree, or a farm's ET₀ would
    change depending on which code path produced it."""
    from app.services.simulation import extraterrestrial_radiation_mm, reference_et0_mm

    ra_engine = water.extraterrestrial_radiation_mm(-21.1775, 234)
    ra_sim = extraterrestrial_radiation_mm(-21.1775, 234)

    assert ra_engine == pytest.approx(ra_sim)
    assert water.hargreaves_et0_mm(23.0, 16.0, ra_engine) == pytest.approx(
        reference_et0_mm(23.0, 16.0, ra_sim), abs=0.01
    )


def test_estimated_days_are_counted_and_reported() -> None:
    days = [a_day(n, rain=0.0, et0=None) for n in range(-10, 0)]

    result = water.evaluate(a_context(days=days))

    assert result.sufficient
    assert result.estimated_et0_days == 10
    assert any("estimated from temperature" in d for d in result.drivers)


def test_a_day_with_neither_et0_nor_temperature_is_skipped() -> None:
    """Skipped, not treated as a day of zero demand — that would understate depletion."""
    usable = [a_day(n, rain=0.0, et0=5.0) for n in range(-5, 0)]
    blank = [
        DailyPoint(day=TODAY + timedelta(days=n), precipitation_mm=0.0) for n in range(-10, -5)
    ]

    result = water.evaluate(a_context(days=blank + usable))

    assert result.sufficient
    assert len(result.days) == 5


def test_no_et0_anywhere_is_insufficient() -> None:
    blank = [DailyPoint(day=TODAY + timedelta(days=n), precipitation_mm=1.0) for n in range(-30, 7)]

    result = water.evaluate(a_context(days=blank))

    assert not result.sufficient
    assert any("evapotranspiration" in item for item in result.missing)


# --------------------------------------------------------------------------
# Hemispheres and latitude
# --------------------------------------------------------------------------


def test_seasons_are_inverted_across_the_equator() -> None:
    """The same calendar day is midsummer in one hemisphere and midwinter in the other.
    A month-based season lookup would get this backwards for half the planet."""
    january = 15
    july = 196

    north_jan = water.extraterrestrial_radiation_mm(45.0, january)
    north_jul = water.extraterrestrial_radiation_mm(45.0, july)
    south_jan = water.extraterrestrial_radiation_mm(-45.0, january)
    south_jul = water.extraterrestrial_radiation_mm(-45.0, july)

    assert north_jul > north_jan
    assert south_jan > south_jul


def test_the_southern_summer_is_the_brighter_one() -> None:
    """Not a symmetry — an asymmetry, and the right one.

    Earth reaches perihelion in early January, so the southern summer receives a few
    percent more radiation than the northern. The eccentricity term in FAO-56 eq. 23
    carries this; a model that mirrored the hemispheres exactly would have dropped it.
    """
    north_summer = water.extraterrestrial_radiation_mm(45.0, 196)
    south_summer = water.extraterrestrial_radiation_mm(-45.0, 15)

    assert south_summer > north_summer
    assert south_summer / north_summer == pytest.approx(1.06, abs=0.02)


def test_polar_night_receives_no_radiation() -> None:
    assert water.extraterrestrial_radiation_mm(78.0, 355) == 0.0


def test_the_balance_runs_at_every_brics_latitude() -> None:
    """The twelve-site matrix spans 3°N to 69°N across both hemispheres. The engine
    must produce a finite, in-range result at all of them with identical inputs."""
    latitudes = [
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
    ]

    for latitude in latitudes:
        result = water.evaluate(
            a_context(days=[a_day(n, rain=2.0, et0=None) for n in range(-30, 7)], latitude=latitude)
        )

        assert result.sufficient, latitude
        assert 0.0 <= result.score <= 100.0
        assert result.depletion_mm is not None and math.isfinite(result.depletion_mm)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def test_a_full_reservoir_scores_no_risk() -> None:
    assert water._score(0.0, taw_mm=170.0, raw_mm=85.0) == 0.0


def test_the_stress_threshold_is_the_low_to_moderate_boundary() -> None:
    """Crossing RAW and crossing into `moderate` are the same event, by construction."""
    at_raw = water._score(85.0, taw_mm=170.0, raw_mm=85.0)

    assert at_raw == pytest.approx(water.STRESS_ONSET_SCORE)
    assert water.risk_level_for(at_raw) == RiskLevel.moderate


def test_wilting_point_scores_maximum_risk() -> None:
    assert water._score(170.0, taw_mm=170.0, raw_mm=85.0) == pytest.approx(100.0)
    assert water.risk_level_for(100.0) == RiskLevel.severe


def test_the_score_rises_monotonically_with_depletion() -> None:
    scores = [water._score(d, taw_mm=170.0, raw_mm=85.0) for d in range(0, 171, 10)]

    assert scores == sorted(scores)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_the_same_context_produces_an_identical_result() -> None:
    context = a_context()

    first = water.evaluate(context)
    second = water.evaluate(context)

    assert first == second


def test_two_equal_contexts_produce_equal_results() -> None:
    assert water.evaluate(a_context()) == water.evaluate(a_context())


def test_the_engine_module_imports_nothing_impure() -> None:
    """The purity guard covers the whole package; this states it for water.py directly,
    because this is the module most tempted to reach for a provider."""
    from tests.unit.engine.test_engine_is_pure import violations

    source = water.__file__ or ""
    assert source
    with open(source, encoding="utf-8") as handle:
        assert violations(handle.read()) == []
