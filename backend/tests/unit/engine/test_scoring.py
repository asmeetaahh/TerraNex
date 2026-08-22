"""Scoring primitives, and their agreement with the code they were promoted from.

`band_for` and `risk_level_for` were private helpers in `analysis_service`. Step 2 of
Phase 4 points that module here and deletes its copies. Until then both exist, and the
tests at the bottom assert they agree at every boundary — because a silent one-point
drift between the two would move scores during the migration and look like a genuine
change in risk.
"""

import pytest

from app.engine.scoring import (
    INSUFFICIENT,
    band_for,
    clamp,
    factor,
    is_unknown,
    risk_level_for,
    unknown_factor,
)
from app.schemas.common import RiskLevel, ScoreBand

# --------------------------------------------------------------------------
# clamp
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-10, 0), (0, 0), (50, 50), (100, 100), (140, 100)],
)
def test_clamp_constrains_to_the_range(value: float, expected: float) -> None:
    assert clamp(value, 0, 100) == expected


# --------------------------------------------------------------------------
# Bands and levels
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100, ScoreBand.excellent),
        (80, ScoreBand.excellent),
        (79.9, ScoreBand.good),
        (65, ScoreBand.good),
        (64.9, ScoreBand.moderate),
        (45, ScoreBand.moderate),
        (44.9, ScoreBand.poor),
        (25, ScoreBand.poor),
        (24.9, ScoreBand.critical),
        (0, ScoreBand.critical),
    ],
)
def test_band_boundaries(score: float, expected: ScoreBand) -> None:
    assert band_for(score) == expected


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100, RiskLevel.severe),
        (70, RiskLevel.severe),
        (69.9, RiskLevel.high),
        (50, RiskLevel.high),
        (49.9, RiskLevel.moderate),
        (28, RiskLevel.moderate),
        (27.9, RiskLevel.low),
        (0, RiskLevel.low),
    ],
)
def test_risk_level_boundaries(score: float, expected: RiskLevel) -> None:
    assert risk_level_for(score) == expected


def test_the_two_scales_run_in_opposite_directions() -> None:
    """The distinction the two functions exist to preserve: 90 is an excellent farm
    and a severe risk. Collapsing them would invert half the dashboard."""
    assert band_for(90) == ScoreBand.excellent
    assert risk_level_for(90) == RiskLevel.severe
    assert band_for(10) == ScoreBand.critical
    assert risk_level_for(10) == RiskLevel.low


# --------------------------------------------------------------------------
# Factors
# --------------------------------------------------------------------------


def test_a_factor_derives_its_band_from_its_score() -> None:
    built = factor("soil_ph", "Soil pH", 72.0, 0.3, "pH 6.4 sits inside the crop window.")

    assert built.key == "soil_ph"
    assert built.score == 72.0
    assert built.weight == 0.3
    assert built.band == ScoreBand.good


def test_a_factor_clamps_rather_than_raising() -> None:
    """The contract bounds score to 0-100 and weight to 0-1. A sub-calculation that
    overshoots by a rounding error should not abort an entire analysis run."""
    assert factor("k", "L", 140.0, 3.0, "x").score == 100.0
    assert factor("k", "L", -20.0, -1.0, "x").score == 0.0
    assert factor("k", "L", 50.0, 3.0, "x").weight == 1.0


def test_an_unknown_factor_carries_zero_weight() -> None:
    """Weight zero is how "not assessed" is expressed, because the contract offers no
    unknown band. It removes the factor from the composite arithmetically."""
    built = unknown_factor("water_balance", "Water balance", ["precipitation"])

    assert built.weight == 0.0
    assert built.explanation.startswith(INSUFFICIENT)
    assert "precipitation" in built.explanation


def test_an_unknown_factor_is_distinguishable_from_a_zero_score() -> None:
    """The composite has to tell "scored badly" from "could not be scored", and the
    frozen contract gives it only these fields to do it with."""
    scored_zero = factor("k", "L", 0.0, 0.5, "Nothing survived the frost.")
    not_assessed = unknown_factor("k", "L", ["humidity"])

    assert is_unknown(not_assessed)
    assert not is_unknown(scored_zero)


def test_a_zero_weighted_factor_with_real_prose_is_not_mistaken_for_unknown() -> None:
    """`is_unknown` checks the marker as well as the weight, so a genuine zero-weight
    factor is not misread."""
    deliberate = factor("k", "L", 40.0, 0.0, "Excluded from this crop's composite.")

    assert not is_unknown(deliberate)


# --------------------------------------------------------------------------
# Agreement with the code being replaced
#
# These guard the *migration*, not the behaviour: while a duplicate of one of these
# helpers still lives in `analysis_service`, the two must agree, because a one-point
# drift between them would move scores and look like a genuine change in risk.
#
# The companion for `risk_level_for` has been retired: the last caller of the service's
# copy moved into the engine, the copy was deleted, and a test importing a function that
# no longer exists guards nothing. `band_for` is the last one still duplicated — the run
# summary and the composite band still use the service's copy — so its check stays until
# those move too.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score", [0, 10, 24.9, 25, 27.9, 28, 44.9, 45, 49.9, 50, 64.9, 65, 69.9, 70, 79.9, 80, 100]
)
def test_bands_match_the_service_they_were_promoted_from(score: float) -> None:
    from app.services.analysis_service import _band_for

    assert band_for(score) == _band_for(score)


def test_the_service_no_longer_keeps_its_own_risk_level_helper() -> None:
    """The migration this file was written to protect, asserted as finished."""
    from app.services import analysis_service

    assert not hasattr(analysis_service, "_risk_level_for")
