"""Crop-relative weather thresholds and thermal time.

The point of this module is that a threshold belongs to a crop. Most of these tests
therefore hold the weather fixed and vary the crop, which is the comparison a constant
threshold cannot survive.

The hemisphere tests matter more than they look: the engine reads only temperatures and
rainfall, never a month, so identical weather must score identically at 45°N and 45°S.
A single `if month in (6, 7, 8)` anywhere would break them.
"""

from datetime import date, timedelta

import pytest

from app.engine import weather
from app.engine.context import AnalysisContext, CropParameters, DailyPoint, SoilPoint
from app.engine.scoring import INSUFFICIENT

TODAY = date(2026, 8, 22)

# Maize: hot above 32, base 10 -> cold damage at max(2, 10-8) = 2 C.
MAIZE = CropParameters(
    code="maize",
    category="cereal",
    base_temp_c=10.0,
    optimal_temp_min_c=18.0,
    optimal_temp_max_c=32.0,
    gdd_to_maturity=1400.0,
    parameters_source="crop",
)

# Sorghum tolerates more heat than maize on identical weather.
SORGHUM = CropParameters(
    code="sorghum", category="cereal", base_temp_c=10.0, optimal_temp_max_c=38.0
)

# Cassava carries a tropical base temperature, so it is injured well above freezing:
# max(2, 13-8) = 5 C.
CASSAVA = CropParameters(code="cassava", category="tuber", base_temp_c=13.0)

# Barley is temperate: base 0 -> max(2, -8) = 2 C, the frost advisory.
BARLEY = CropParameters(code="barley", category="cereal", base_temp_c=0.0)


def a_day(offset: int, **kw) -> DailyPoint:
    values = {
        "temp_min_c": 16.0,
        "temp_max_c": 28.0,
        "temp_mean_c": 22.0,
        "precipitation_mm": 3.0,
        "wind_kmh": 10.0,
        "et0_mm": 4.0,
    }
    values.update(kw)
    return DailyPoint(day=TODAY + timedelta(days=offset), **values)


def a_context(days=None, **overrides) -> AnalysisContext:
    values = {
        "farm_id": "f",
        "latitude": -21.1775,
        "longitude": -47.8103,
        "timezone": "UTC",
        "today": TODAY,
        "daily": tuple(days if days is not None else [a_day(n) for n in range(0, 7)]),
        "soil": SoilPoint(texture_class="loam"),
        "crop": MAIZE,
        "growth_stage": "flowering",
        "irrigation_type": "rainfed",
    }
    values.update(overrides)
    return AnalysisContext(**values)


# --------------------------------------------------------------------------
# Crop-relative heat
# --------------------------------------------------------------------------


def test_the_heat_threshold_comes_from_the_crop() -> None:
    assert weather.heat_threshold_for(38.0) == (38.0, "crop")


def test_the_heat_threshold_falls_back_when_there_is_no_crop() -> None:
    assert weather.heat_threshold_for(None) == (weather.GENERIC_HEAT_C, "generic")


def test_identical_weather_stresses_maize_but_not_sorghum() -> None:
    """The comparison a constant threshold cannot make. 35 C every day is above maize's
    optimum and below sorghum's, so the same week is a heat event for one crop only."""
    days = [a_day(n, temp_max_c=35.0) for n in range(0, 7)]

    maize = weather.evaluate(a_context(days=days, crop=MAIZE))
    sorghum = weather.evaluate(a_context(days=days, crop=SORGHUM))

    assert maize.heat_stress_days == 7
    assert sorghum.heat_stress_days == 0
    assert maize.score > sorghum.score


def test_a_generic_threshold_is_declared_as_generic() -> None:
    """An unplanted farm still gets an assessment, but must not imply it was tuned to
    a crop that does not exist."""
    days = [a_day(n, temp_max_c=35.0) for n in range(0, 7)]

    result = weather.evaluate(a_context(days=days, crop=CropParameters()))

    assert result.thresholds_source == "generic"
    assert any("generic" in driver for driver in result.drivers)
    heat = next(f for f in result.factors if f.key == "heat_stress")
    assert "no crop is planted" in heat.explanation


def test_the_threshold_is_exclusive() -> None:
    """A day exactly at the optimum is not yet stress."""
    at_threshold = [a_day(n, temp_max_c=32.0) for n in range(0, 7)]
    just_over = [a_day(n, temp_max_c=32.01) for n in range(0, 7)]

    assert weather.evaluate(a_context(days=at_threshold)).heat_stress_days == 0
    assert weather.evaluate(a_context(days=just_over)).heat_stress_days == 7


# --------------------------------------------------------------------------
# Crop-relative cold
# --------------------------------------------------------------------------


def test_a_temperate_crop_uses_the_frost_advisory() -> None:
    """Barley's base temperature is zero, so the advisory floor governs — dropping
    below it would report no cold risk for the crops most exposed to frost."""
    assert weather.cold_threshold_for(0.0) == (weather.FROST_ADVISORY_C, "crop")


def test_a_tropical_crop_is_injured_well_above_freezing() -> None:
    """Cassava suffers chilling injury at temperatures barley shrugs off."""
    threshold, source = weather.cold_threshold_for(13.0)

    assert threshold == 5.0
    assert source == "crop"
    assert threshold > weather.FROST_ADVISORY_C


def test_no_crop_falls_back_to_the_frost_advisory() -> None:
    assert weather.cold_threshold_for(None) == (weather.FROST_ADVISORY_C, "generic")


def test_the_same_cold_night_harms_cassava_but_not_barley() -> None:
    days = [a_day(n, temp_min_c=4.0, temp_max_c=18.0) for n in range(0, 7)]

    cassava = weather.evaluate(a_context(days=days, crop=CASSAVA))
    barley = weather.evaluate(a_context(days=days, crop=BARLEY))

    assert cassava.frost_risk_days == 7
    assert barley.frost_risk_days == 0


def test_hard_frost_is_flagged_for_every_crop() -> None:
    days = [a_day(n, temp_min_c=-6.0, temp_max_c=2.0) for n in range(0, 7)]

    for crop in (MAIZE, BARLEY, CASSAVA, CropParameters()):
        result = weather.evaluate(a_context(days=days, crop=crop))

        assert result.frost_risk_days == 7, crop.code


def test_polar_conditions_score_severe_risk() -> None:
    days = [a_day(n, temp_min_c=-25.0, temp_max_c=-10.0, precipitation_mm=0.0) for n in range(0, 7)]

    result = weather.evaluate(a_context(days=days, latitude=68.9678))

    assert result.frost_risk_days == 7
    assert result.level.value == "severe"
    assert result.min_temp_c == -25.0


# --------------------------------------------------------------------------
# Growing degree days
# --------------------------------------------------------------------------


def test_degree_days_are_the_mean_above_the_base() -> None:
    """(30 + 10) / 2 - 10 = 10."""
    assert weather.daily_growing_degrees(10.0, 30.0, base_temp_c=10.0) == pytest.approx(10.0)


def test_degree_days_are_never_negative() -> None:
    """A cold snap cannot un-grow a crop. Allowing a negative day would let one week
    erase a month of accumulated season."""
    assert weather.daily_growing_degrees(-10.0, 0.0, base_temp_c=10.0) == 0.0
    assert weather.daily_growing_degrees(-40.0, -30.0, base_temp_c=10.0) == 0.0


def test_an_upper_cutoff_caps_development() -> None:
    """Past its optimum a crop develops more slowly, not faster, so the maximum is
    capped before averaging."""
    uncapped = weather.daily_growing_degrees(20.0, 45.0, base_temp_c=10.0)
    capped = weather.daily_growing_degrees(20.0, 45.0, base_temp_c=10.0, upper_temp_c=32.0)

    assert capped < uncapped
    assert capped == pytest.approx((32.0 + 20.0) / 2 - 10.0)


def test_an_unmeasured_day_contributes_nothing_and_is_not_counted() -> None:
    assert weather.daily_growing_degrees(None, 30.0, base_temp_c=10.0) is None
    assert weather.daily_growing_degrees(10.0, None, base_temp_c=10.0) is None


def test_accumulation_sums_only_measured_days() -> None:
    days = [
        a_day(-3, temp_min_c=10.0, temp_max_c=30.0),
        DailyPoint(day=TODAY - timedelta(days=2)),
        a_day(-1, temp_min_c=10.0, temp_max_c=30.0),
    ]

    total, counted = weather.accumulate_growing_degrees(days, base_temp_c=10.0)

    assert total == pytest.approx(20.0)
    assert counted == 2


def test_degree_days_accumulate_from_the_planting_date() -> None:
    days = [a_day(n, temp_min_c=10.0, temp_max_c=30.0) for n in range(-20, 7)]

    result = weather.evaluate(
        a_context(days=days, planting_date=TODAY - timedelta(days=10), crop=MAIZE)
    )

    # Eleven elapsed days inclusive of today, 10 degree days each.
    assert result.gdd_days_counted == 11
    assert result.growing_degree_days == pytest.approx(110.0)


def test_the_forecast_is_not_counted_as_development_already_done() -> None:
    """Projecting thermal time forward would report growth the crop has not made."""
    days = [a_day(n, temp_min_c=10.0, temp_max_c=30.0) for n in range(-5, 7)]

    result = weather.evaluate(
        a_context(days=days, planting_date=TODAY - timedelta(days=5), crop=MAIZE)
    )

    assert result.gdd_days_counted == 6


def test_maturity_progress_is_reported_against_the_crops_requirement() -> None:
    days = [a_day(n, temp_min_c=10.0, temp_max_c=30.0) for n in range(-70, 7)]

    result = weather.evaluate(
        a_context(days=days, planting_date=TODAY - timedelta(days=70), crop=MAIZE)
    )

    assert result.gdd_to_maturity == 1400.0
    assert result.maturity_fraction == pytest.approx(result.growing_degree_days / 1400.0)
    assert any("maturity" in driver for driver in result.drivers)


def test_a_crop_past_its_requirement_exceeds_full_maturity() -> None:
    """Degree days keep accumulating after maturity; the fraction is not clamped, so a
    crop overdue for harvest is visible rather than pinned at 100%."""
    days = [a_day(n, temp_min_c=15.0, temp_max_c=32.0) for n in range(-200, 7)]

    result = weather.evaluate(
        a_context(days=days, planting_date=TODAY - timedelta(days=200), crop=MAIZE)
    )

    assert result.maturity_fraction is not None
    assert result.maturity_fraction > 1.0


def test_no_planting_date_means_no_degree_days() -> None:
    """Without a planting date there is no origin to accumulate from, and inventing one
    would report a season the crop never had."""
    result = weather.evaluate(a_context(planting_date=None))

    assert result.growing_degree_days is None
    assert result.maturity_fraction is None


def test_no_base_temperature_means_no_degree_days() -> None:
    result = weather.evaluate(
        a_context(crop=CropParameters(code="x"), planting_date=TODAY - timedelta(days=10))
    )

    assert result.growing_degree_days is None


# --------------------------------------------------------------------------
# Hemispheres
# --------------------------------------------------------------------------


def test_equivalent_weather_scores_equivalently_in_both_hemispheres() -> None:
    """No month is ever consulted, so latitude sign cannot change a score."""
    days = [a_day(n, temp_max_c=36.0, temp_min_c=1.0, precipitation_mm=0.0) for n in range(0, 7)]

    north = weather.evaluate(a_context(days=days, latitude=45.0, longitude=10.0))
    south = weather.evaluate(a_context(days=days, latitude=-45.0, longitude=10.0))

    assert north.score == south.score
    assert north.heat_stress_days == south.heat_stress_days
    assert north.frost_risk_days == south.frost_risk_days
    assert north.drivers == south.drivers


def test_degree_days_are_identical_across_hemispheres_for_identical_weather() -> None:
    days = [a_day(n, temp_min_c=12.0, temp_max_c=28.0) for n in range(-30, 7)]
    planted = TODAY - timedelta(days=30)

    north = weather.evaluate(a_context(days=days, latitude=52.0, planting_date=planted))
    south = weather.evaluate(a_context(days=days, latitude=-33.0, planting_date=planted))

    assert north.growing_degree_days == south.growing_degree_days


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
    result = weather.evaluate(a_context(latitude=latitude))

    assert 0.0 <= result.score <= 100.0
    assert result.explanation


# --------------------------------------------------------------------------
# Missing data
# --------------------------------------------------------------------------


def test_missing_temperature_does_not_become_zero_risk() -> None:
    """The silent-corruption case: an absent reading is unknown, not benign."""
    days = [
        DailyPoint(day=TODAY + timedelta(days=n), precipitation_mm=2.0, wind_kmh=8.0)
        for n in range(0, 7)
    ]

    result = weather.evaluate(a_context(days=days))

    assert result.max_temp_c is None
    assert result.min_temp_c is None
    assert result.heat_stress_days == 0
    heat = next(f for f in result.factors if f.key == "heat_stress")
    assert heat.weight == 0.0
    assert INSUFFICIENT in heat.explanation
    assert any(INSUFFICIENT in driver for driver in result.drivers)


def test_missing_rainfall_marks_both_rainfall_factors_unassessed() -> None:
    days = [
        DailyPoint(day=TODAY + timedelta(days=n), temp_min_c=15.0, temp_max_c=25.0)
        for n in range(0, 7)
    ]

    result = weather.evaluate(a_context(days=days))

    assert result.total_precipitation_mm is None
    for key in ("rainfall_extremes", "dry_spell"):
        assert next(f for f in result.factors if f.key == key).weight == 0.0


def test_an_unmeasured_day_breaks_a_dry_spell_rather_than_extending_it() -> None:
    days = [
        a_day(0, precipitation_mm=0.0),
        a_day(1, precipitation_mm=0.0),
        DailyPoint(day=TODAY + timedelta(days=2), temp_min_c=15.0, temp_max_c=25.0),
        a_day(3, precipitation_mm=0.0),
    ]

    assert weather.longest_dry_spell(days) == 2


def test_a_non_numeric_reading_is_treated_as_unknown() -> None:
    """A provider that sends `"hot"` has told us nothing comparable.

    Caught by the existing contract tests when this engine first went in: a string
    reaching a `>` against a float raises exactly where a `None` would, so both mean
    unknown. The adapter coerces at the boundary; this is the engine's own guard for
    contexts built by hand.
    """
    days = [
        DailyPoint(
            day=TODAY + timedelta(days=n),
            temp_max_c="hot",  # type: ignore[arg-type]
            temp_min_c=14.0,
            precipitation_mm=2.0,
        )
        for n in range(0, 7)
    ]

    result = weather.evaluate(a_context(days=days))

    assert result.heat_stress_days == 0
    assert result.max_temp_c is None
    assert next(f for f in result.factors if f.key == "heat_stress").weight == 0.0


def test_a_boolean_is_not_a_measurement() -> None:
    """`bool` subclasses `int`, so an unguarded check would read `True` as 1.0 °C."""
    assert weather.reading(DailyPoint(day=TODAY, temp_max_c=True), "temp_max_c") is None  # type: ignore[arg-type]


def test_an_empty_forecast_is_reported_not_scored() -> None:
    result = weather.evaluate(a_context(days=[]))

    assert result.score == 0.0
    assert INSUFFICIENT in result.explanation
    assert any("no daily forecast" in driver for driver in result.drivers)


def test_a_quiet_week_says_so_explicitly() -> None:
    result = weather.evaluate(a_context())

    assert result.score == 0.0
    assert result.drivers == ("No threshold exceedances in the forecast window",)


# --------------------------------------------------------------------------
# Determinism and purity
# --------------------------------------------------------------------------


def test_the_same_context_produces_an_identical_result() -> None:
    context = a_context()

    assert weather.evaluate(context) == weather.evaluate(context)


def test_two_equal_contexts_produce_equal_results() -> None:
    assert weather.evaluate(a_context()) == weather.evaluate(a_context())


def test_the_score_does_not_depend_on_day_ordering() -> None:
    """Providers do not guarantee ordering, and a shuffled response is the same week."""
    days = [a_day(n, temp_max_c=(36.0 if n % 2 else 20.0)) for n in range(0, 7)]

    forward = weather.evaluate(a_context(days=days))
    backward = weather.evaluate(a_context(days=list(reversed(days))))

    assert forward.score == backward.score
    assert forward.heat_stress_days == backward.heat_stress_days


def test_the_engine_module_imports_nothing_impure() -> None:
    from tests.unit.engine.test_engine_is_pure import violations

    source = weather.__file__ or ""
    assert source
    with open(source, encoding="utf-8") as handle:
        assert violations(handle.read()) == []
