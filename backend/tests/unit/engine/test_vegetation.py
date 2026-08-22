"""Canopy vigour, trend, and crop development.

The trend is what most of this file defends. A single NDVI reading cannot say whether a
crop is greening up or dying back, so the baseline selection and the change calculation
are where the assessment earns its keep — including the case that used to break it, a
baseline of exactly 0.0.

Everything else is the missing-data discipline: an empty series means no measurement was
taken, never bare ground, and an unmeasured farm must not be reported as a dying one.
"""

from datetime import date, timedelta

import pytest

from app.engine import vegetation
from app.engine.context import AnalysisContext, CropParameters, DailyPoint, SoilPoint
from app.engine.scoring import INSUFFICIENT
from app.schemas.common import ScoreBand

TODAY = date(2026, 8, 22)

MAIZE = CropParameters(
    code="maize",
    category="cereal",
    base_temp_c=10.0,
    gdd_to_maturity=1400.0,
    parameters_source="crop",
)


def series(*points: tuple[int, float]) -> tuple[tuple[date, float], ...]:
    """`(days_ago, ndvi)` pairs, oldest first."""
    return tuple((TODAY - timedelta(days=ago), value) for ago, value in points)


#: Ninety days at five-day steps, matching what the snapshot actually supplies.
STEADY = series(*[(ago, 0.60) for ago in range(90, -1, -5)])


def a_day(offset: int, mean: float | None = 25.0) -> DailyPoint:
    return DailyPoint(
        day=TODAY + timedelta(days=offset),
        temp_min_c=18.0,
        temp_max_c=32.0,
        temp_mean_c=mean,
    )


def a_context(**overrides) -> AnalysisContext:
    values = {
        "farm_id": "f",
        "latitude": -21.1775,
        "longitude": -47.8103,
        "timezone": "UTC",
        "today": TODAY,
        "daily": tuple(a_day(n) for n in range(-40, 8)),
        "vegetation": STEADY,
        "soil": SoilPoint(texture_class="loam"),
        "crop": MAIZE,
        "growth_stage": "flowering",
        "irrigation_type": "rainfed",
    }
    values.update(overrides)
    return AnalysisContext(**values)


# --------------------------------------------------------------------------
# A normal assessment
# --------------------------------------------------------------------------


def test_a_healthy_steady_canopy_scores_well() -> None:
    result = vegetation.evaluate(a_context())

    assert result.sufficient
    assert result.current_ndvi == 0.60
    assert result.trend == "stable"
    assert result.band in {ScoreBand.good, ScoreBand.moderate, ScoreBand.excellent}
    assert 0.0 <= result.score <= 100.0


def test_the_current_reading_is_the_latest_sample() -> None:
    """Pinned to the whole series rather than a windowed slice, which is what keeps
    this identical to the value `GET /vegetation` reports."""
    rising = series((90, 0.30), (60, 0.40), (30, 0.50), (0, 0.72))

    assert vegetation.evaluate(a_context(vegetation=rising)).current_ndvi == 0.72


def test_an_unordered_series_is_read_in_date_order() -> None:
    """Providers do not guarantee ordering, and a shuffled series is the same series."""
    ordered = series((90, 0.30), (60, 0.40), (30, 0.50), (0, 0.72))
    shuffled = tuple(reversed(ordered))

    assert vegetation.evaluate(a_context(vegetation=shuffled)).current_ndvi == 0.72
    assert vegetation.evaluate(a_context(vegetation=shuffled)).trend == (
        vegetation.evaluate(a_context(vegetation=ordered)).trend
    )


# --------------------------------------------------------------------------
# Vigour level
# --------------------------------------------------------------------------


def test_a_canopy_at_the_ceiling_scores_full_vigour() -> None:
    assert vegetation.score_canopy_vigour(vegetation.HEALTHY_NDVI_CEILING) == 100.0


def test_vigour_above_the_ceiling_is_capped() -> None:
    """The index saturates: above roughly this point it stops discriminating."""
    assert vegetation.score_canopy_vigour(0.95) == 100.0


def test_vigour_rises_with_ndvi() -> None:
    scores = [vegetation.score_canopy_vigour(n / 10) for n in range(0, 9)]

    assert scores == sorted(scores)


def test_a_low_canopy_is_flagged_as_stress() -> None:
    sparse = series(*[(ago, 0.20) for ago in range(90, -1, -5)])

    result = vegetation.evaluate(a_context(vegetation=sparse))

    assert any("Canopy vigour is low" in item for item in result.stress_indicators)


def test_a_canopy_at_the_low_threshold_is_not_flagged() -> None:
    """The threshold is exclusive: exactly at it is not yet low."""
    at_threshold = series(*[(ago, vegetation.LOW_NDVI_THRESHOLD) for ago in range(90, -1, -5)])

    result = vegetation.evaluate(a_context(vegetation=at_threshold))

    assert not any("Canopy vigour is low" in item for item in result.stress_indicators)


def test_a_healthy_canopy_reports_no_stress() -> None:
    assert vegetation.evaluate(a_context()).stress_indicators == ()


# --------------------------------------------------------------------------
# Trend
# --------------------------------------------------------------------------


def test_a_rising_canopy_is_improving() -> None:
    rising = series((90, 0.30), (60, 0.35), (30, 0.40), (0, 0.60))

    result = vegetation.evaluate(a_context(vegetation=rising))

    assert result.trend == "improving"
    assert result.trend_pct > vegetation.TREND_BAND_PCT


def test_a_falling_canopy_is_declining() -> None:
    falling = series((90, 0.75), (60, 0.70), (30, 0.65), (0, 0.40))

    result = vegetation.evaluate(a_context(vegetation=falling))

    assert result.trend == "declining"
    assert result.trend_pct < -vegetation.TREND_BAND_PCT
    assert any("NDVI declined" in item for item in result.stress_indicators)


def test_a_flat_canopy_is_stable() -> None:
    assert vegetation.evaluate(a_context()).trend == "stable"


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (5.01, "improving"),
        (5.0, "stable"),
        (0.0, "stable"),
        (-5.0, "stable"),
        (-5.01, "declining"),
    ],
)
def test_the_trend_band_edges(change: float, expected: str) -> None:
    """Exactly at the band edge is still flat; a hair beyond it is not."""
    assert vegetation.classify_trend(change) == expected


def test_the_baseline_is_the_most_recent_sample_past_the_lookback() -> None:
    stepped = series((90, 0.10), (45, 0.20), (31, 0.40), (10, 0.90), (0, 0.60))

    assert vegetation.baseline_ndvi(stepped, TODAY) == 0.40


def test_a_short_series_falls_back_to_its_oldest_sample() -> None:
    """A newly planted field has no month of history; refusing a trend would leave it
    with no direction at all, and a shorter baseline is still a real comparison."""
    young = series((10, 0.20), (5, 0.30), (0, 0.45))

    assert vegetation.baseline_ndvi(young, TODAY) == 0.20
    assert vegetation.evaluate(a_context(vegetation=young)).trend == "improving"


# --------------------------------------------------------------------------
# The zero-baseline bug
# --------------------------------------------------------------------------


def test_a_baseline_of_zero_is_a_measurement_not_a_gap() -> None:
    """Regression. The previous check tested the baseline for *truthiness*, so an NDVI
    of exactly 0.0 — legitimate over bare soil or water — was read as a missing
    baseline and the trend was silently dropped.
    """
    from_bare = series((90, 0.0), (60, 0.0), (30, 0.0), (0, 0.55))

    result = vegetation.evaluate(a_context(vegetation=from_bare))

    assert result.baseline_ndvi == 0.0
    assert result.trend == "improving", "a canopy growing from bare ground is improving"
    assert result.trend_pct > 0


def test_a_zero_baseline_does_not_divide_by_zero() -> None:
    """Percent change from zero is undefined, so the direction is reported at full
    magnitude rather than raising or being discarded."""
    assert vegetation.trend_change_pct(0.55, 0.0) == 100.0
    assert vegetation.trend_change_pct(-0.10, 0.0) == -100.0
    assert vegetation.trend_change_pct(0.0, 0.0) == 0.0


def test_a_canopy_collapsing_to_zero_is_declining() -> None:
    collapsed = series((90, 0.70), (60, 0.65), (30, 0.60), (0, 0.0))

    result = vegetation.evaluate(a_context(vegetation=collapsed))

    assert result.current_ndvi == 0.0
    assert result.trend == "declining"
    assert result.sufficient, "zero is a reading; the farm was measured"


# --------------------------------------------------------------------------
# Missing data
# --------------------------------------------------------------------------


def test_an_empty_series_reports_no_ndvi_and_no_trend() -> None:
    result = vegetation.evaluate(a_context(vegetation=()))

    assert not result.sufficient
    assert result.current_ndvi is None
    assert result.trend is None
    assert INSUFFICIENT in result.explanation


def test_a_missing_index_is_never_catastrophic() -> None:
    """An unmeasured farm must not be reported as a dying one. Zero with a `critical`
    band would read as a dead crop rather than an absent measurement."""
    result = vegetation.evaluate(a_context(vegetation=()))

    assert result.score == 0.0
    assert result.band is ScoreBand.moderate


def test_a_missing_index_carries_zero_weight_factors() -> None:
    result = vegetation.evaluate(a_context(vegetation=()))

    assert {f.key for f in result.factors} == {"canopy_vigour", "vigour_trend"}
    assert all(f.weight == 0.0 for f in result.factors)
    assert all("vegetation index" in f.explanation for f in result.factors)


def test_a_missing_index_is_the_only_stress_reported() -> None:
    result = vegetation.evaluate(a_context(vegetation=()))

    assert len(result.stress_indicators) == 1
    assert INSUFFICIENT in result.stress_indicators[0]


def test_a_non_numeric_reading_is_treated_as_unknown() -> None:
    junk = ((TODAY, "green"),)  # type: ignore[var-annotated]

    result = vegetation.evaluate(a_context(vegetation=junk))

    assert not result.sufficient
    assert result.current_ndvi is None


# --------------------------------------------------------------------------
# Crop development
# --------------------------------------------------------------------------


def test_days_since_planting_counts_elapsed_days() -> None:
    result = vegetation.evaluate(a_context(planting_date=TODAY - timedelta(days=40)))

    assert result.days_since_planting == 40


def test_a_future_planting_date_never_reports_negative_days() -> None:
    result = vegetation.evaluate(a_context(planting_date=TODAY + timedelta(days=5)))

    assert result.days_since_planting == 0


def test_days_to_harvest_counts_forward() -> None:
    result = vegetation.evaluate(a_context(expected_harvest_date=TODAY + timedelta(days=35)))

    assert result.days_to_expected_harvest == 35


def test_an_overdue_harvest_reports_negative_days() -> None:
    """Information, not an error: the contract does not bound this field below zero."""
    result = vegetation.evaluate(a_context(expected_harvest_date=TODAY - timedelta(days=6)))

    assert result.days_to_expected_harvest == -6


def test_no_planting_means_no_development_figures() -> None:
    result = vegetation.evaluate(a_context(planting_date=None, expected_harvest_date=None))

    assert result.days_since_planting is None
    assert result.days_to_expected_harvest is None
    assert result.growing_degree_days is None


def test_the_growth_stage_passes_through() -> None:
    assert vegetation.evaluate(a_context(growth_stage="fruiting")).growth_stage == "fruiting"


def test_gdd_required_comes_from_the_crop() -> None:
    assert vegetation.evaluate(a_context()).gdd_required == 1400.0


# --------------------------------------------------------------------------
# Degree days — the existing CropHealth method, deliberately unchanged
# --------------------------------------------------------------------------


def test_degree_days_use_the_daily_mean_above_the_base() -> None:
    """`max(0, temp_mean_c - base)`, ten days at 25 °C over a base of 10 = 150."""
    days = tuple(a_day(n, mean=25.0) for n in range(-10, 1))

    total = vegetation.accumulate_degree_days(days, TODAY - timedelta(days=9), TODAY, 10.0)

    assert total == pytest.approx(150.0)


def test_degree_days_have_no_upper_cutoff() -> None:
    """Distinguishes this from `weather.py`, which caps the maximum before averaging.
    Preserved deliberately; the two are unified as a separate change."""
    hot = tuple(a_day(n, mean=45.0) for n in range(-1, 1))

    total = vegetation.accumulate_degree_days(hot, TODAY - timedelta(days=1), TODAY, 10.0)

    assert total == pytest.approx(70.0)


def test_a_cold_day_contributes_nothing_and_never_subtracts() -> None:
    cold = tuple(a_day(n, mean=2.0) for n in range(-5, 1))

    assert vegetation.accumulate_degree_days(cold, TODAY - timedelta(days=5), TODAY, 10.0) == 0.0


def test_degree_days_only_count_days_inside_the_growing_period() -> None:
    """A crop planted after some of the window began must not be credited with heat
    that arrived before it was in the ground."""
    days = tuple(a_day(n, mean=25.0) for n in range(-20, 1))

    whole = vegetation.accumulate_degree_days(days, TODAY - timedelta(days=20), TODAY, 10.0)
    recent = vegetation.accumulate_degree_days(days, TODAY - timedelta(days=5), TODAY, 10.0)

    assert recent < whole


def test_an_unmeasured_day_is_skipped_rather_than_counted_as_zero() -> None:
    partly = tuple(a_day(n, mean=(None if n % 2 else 25.0)) for n in range(-10, 1))

    total = vegetation.accumulate_degree_days(partly, TODAY - timedelta(days=10), TODAY, 10.0)

    assert 0.0 < total < 165.0


def test_a_crop_without_a_base_temperature_uses_the_documented_default() -> None:
    without = CropParameters(code="x")

    result = vegetation.evaluate(a_context(crop=without, planting_date=TODAY - timedelta(days=10)))
    expected = vegetation.accumulate_degree_days(
        tuple(a_day(n) for n in range(-40, 8)),
        TODAY - timedelta(days=10),
        TODAY,
        vegetation.DEFAULT_BASE_TEMP_C,
    )

    assert result.growing_degree_days == pytest.approx(expected)


# --------------------------------------------------------------------------
# Bare ground
# --------------------------------------------------------------------------


def test_an_unplanted_farm_says_the_vigour_is_bare_ground() -> None:
    result = vegetation.evaluate(a_context(crop=CropParameters()))

    assert any("bare ground" in item for item in result.stress_indicators)


def test_a_planted_farm_does_not_claim_bare_ground() -> None:
    assert not any(
        "bare ground" in item for item in vegetation.evaluate(a_context()).stress_indicators
    )


# --------------------------------------------------------------------------
# Determinism, range, geography, purity
# --------------------------------------------------------------------------


def test_the_same_context_produces_an_identical_result() -> None:
    context = a_context()

    assert vegetation.evaluate(context) == vegetation.evaluate(context)


def test_repeated_evaluation_never_varies() -> None:
    """No RNG in the engine. The index itself is simulated upstream, deterministically
    per coordinate and day, which is a separate concern from this module."""
    first = vegetation.evaluate(a_context())

    assert all(vegetation.evaluate(a_context()) == first for _ in range(8))


@pytest.mark.parametrize("ndvi", [-0.05, 0.0, 0.2, 0.5, 0.85, 0.95])
def test_scores_stay_in_range_across_the_index(ndvi: float) -> None:
    flat = series(*[(ago, ndvi) for ago in range(90, -1, -5)])

    result = vegetation.evaluate(a_context(vegetation=flat))

    assert 0.0 <= result.score <= 100.0
    assert all(0.0 <= f.score <= 100.0 for f in result.factors)


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
def test_identical_series_score_identically_at_every_brics_latitude(latitude: float) -> None:
    """The engine reads the index, never a coordinate — so the score cannot move with
    one. Seasonality lives in the simulator that produced the series."""
    baseline = vegetation.evaluate(a_context(latitude=-21.1775))
    here = vegetation.evaluate(a_context(latitude=latitude))

    assert here.score == baseline.score
    assert here.trend == baseline.trend


def test_the_engine_module_imports_nothing_impure() -> None:
    from tests.unit.engine.test_engine_is_pure import violations

    source = vegetation.__file__ or ""
    assert source
    with open(source, encoding="utf-8") as handle:
        assert violations(handle.read()) == []
