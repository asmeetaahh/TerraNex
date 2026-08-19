"""The simulator's determinism and physical-shape guarantees.

Determinism is the property the whole phase rests on, so it is asserted directly
rather than only through the API.
"""

import math
from datetime import date

import pytest

from app.schemas.common import DataMode
from app.services.simulation import (
    climate_zone,
    extraterrestrial_radiation_mm,
    reference_et0_mm,
    seeded_rng,
    simulate_day,
    simulate_days,
    simulate_ndvi,
    simulate_soil,
    simulated_meta,
    stable_seed,
    usda_texture_class,
)

NAIROBI = (-1.2864, 36.8172)
IOWA = (42.0308, -93.6319)
DAY = date(2026, 6, 15)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_seed_is_stable_for_equal_inputs() -> None:
    assert stable_seed("weather", 1.5, "x") == stable_seed("weather", 1.5, "x")
    assert stable_seed("weather", 1.5, "x") != stable_seed("weather", 1.5, "y")


def test_seed_does_not_use_salted_string_hashing() -> None:
    """`hash()` is salted per process, which would silently break determinism across
    restarts. This pins the SHA-256 derivation instead.

    The literal is the value the current derivation produces; if it changes, every
    previously generated fixture changes with it.
    """
    assert stable_seed("terranex") == 16720280180480059666


def test_rng_streams_are_reproducible() -> None:
    a = [seeded_rng("x", 1).random() for _ in range(3)]
    b = [seeded_rng("x", 1).random() for _ in range(3)]
    assert a == b


def test_weather_is_identical_for_identical_inputs() -> None:
    first = simulate_day(*NAIROBI, DAY)
    second = simulate_day(*NAIROBI, DAY)

    assert (first.temp_mean_c, first.precipitation_mm, first.humidity_pct) == (
        second.temp_mean_c,
        second.precipitation_mm,
        second.humidity_pct,
    )


def test_weather_differs_across_days_and_places() -> None:
    here_today = simulate_day(*NAIROBI, DAY)
    here_tomorrow = simulate_day(*NAIROBI, date(2026, 6, 16))
    there_today = simulate_day(*IOWA, DAY)

    assert here_today.temp_mean_c != here_tomorrow.temp_mean_c
    assert here_today.temp_mean_c != there_today.temp_mean_c


def test_soil_is_stable_for_a_coordinate() -> None:
    """Soil is effectively static, so it must not vary between calls at all."""
    a, b = simulate_soil(*NAIROBI), simulate_soil(*NAIROBI)
    assert (a.ph, a.sand_pct, a.clay_pct, a.texture_class) == (
        b.ph,
        b.sand_pct,
        b.clay_pct,
        b.texture_class,
    )


def test_coordinate_rounding_groups_nearby_points() -> None:
    """Coordinates are rounded to three decimals (~110 m), so two points inside the
    same field share a soil profile — the same behaviour a cache key will have."""
    a = simulate_soil(-1.28641, 36.81721)
    b = simulate_soil(-1.28644, 36.81722)
    assert a.ph == b.ph


# --------------------------------------------------------------------------
# Physical shape
# --------------------------------------------------------------------------


def test_soil_fractions_sum_to_100() -> None:
    for lat, lon in [NAIROBI, IOWA, (0, 0), (-33.9, 18.4), (51.5, -0.1)]:
        soil = simulate_soil(lat, lon)
        assert soil.sand_pct + soil.silt_pct + soil.clay_pct == pytest.approx(100, abs=0.5)


def test_soil_values_stay_in_physical_range() -> None:
    for lat in range(-60, 61, 15):
        soil = simulate_soil(lat, 20.0)
        assert 3.9 <= soil.ph <= 8.8
        assert 0 < soil.organic_carbon_pct <= 6
        assert 0.9 <= soil.bulk_density_kg_dm3 <= 1.8
        assert soil.water_holding_capacity_mm > 0


@pytest.mark.parametrize(
    ("sand", "silt", "clay", "expected"),
    [
        (92, 5, 3, "sand"),
        (40, 40, 20, "loam"),
        (20, 60, 20, "silt_loam"),
        (10, 45, 45, "silty_clay"),
        (55, 10, 35, "sandy_clay"),
    ],
)
def test_usda_texture_triangle(sand: float, silt: float, clay: float, expected: str) -> None:
    assert usda_texture_class(sand, silt, clay).value == expected


def test_radiation_peaks_in_local_summer() -> None:
    """Ra is real physics — it must show opposite seasons across the equator."""
    northern_summer = extraterrestrial_radiation_mm(45.0, 172)
    northern_winter = extraterrestrial_radiation_mm(45.0, 355)
    assert northern_summer > northern_winter

    southern_summer = extraterrestrial_radiation_mm(-45.0, 355)
    southern_winter = extraterrestrial_radiation_mm(-45.0, 172)
    assert southern_summer > southern_winter


def test_radiation_is_finite_at_extreme_latitudes() -> None:
    for lat in (-89.0, -66.5, 0.0, 66.5, 89.0):
        value = extraterrestrial_radiation_mm(lat, 172)
        assert math.isfinite(value) and value >= 0


def test_et0_rises_with_temperature() -> None:
    ra = extraterrestrial_radiation_mm(0.0, 172)
    assert reference_et0_mm(30, 10, ra) > reference_et0_mm(10, 10, ra)


def test_tropics_are_warmer_than_high_latitudes() -> None:
    tropical = simulate_days(0.0, 36.0, DAY, 30)
    boreal = simulate_days(64.0, 20.0, DAY, 30)

    tropical_mean = sum(d.temp_mean_c for d in tropical) / len(tropical)
    boreal_mean = sum(d.temp_mean_c for d in boreal) / len(boreal)
    assert tropical_mean > boreal_mean


def test_seasons_are_inverted_across_the_equator() -> None:
    north_january = simulate_days(45.0, 10.0, date(2026, 1, 15), 30)
    north_july = simulate_days(45.0, 10.0, date(2026, 7, 15), 30)
    south_january = simulate_days(-45.0, 10.0, date(2026, 1, 15), 30)
    south_july = simulate_days(-45.0, 10.0, date(2026, 7, 15), 30)

    def mean(days):
        return sum(d.temp_mean_c for d in days) / len(days)

    assert mean(north_july) > mean(north_january)
    assert mean(south_january) > mean(south_july)


@pytest.mark.parametrize(
    ("latitude", "zone"),
    [(0, "tropical"), (20, "tropical"), (30, "subtropical"), (45, "temperate"), (65, "boreal")],
)
def test_climate_zones(latitude: float, zone: str) -> None:
    assert climate_zone(latitude) == zone
    assert climate_zone(-latitude) == zone


def test_ndvi_stays_in_range_and_responds_to_a_crop() -> None:
    bare = [simulate_ndvi(*NAIROBI, date(2026, m, 1), has_crop=False) for m in range(1, 13)]
    cropped = [simulate_ndvi(*NAIROBI, date(2026, m, 1), has_crop=True) for m in range(1, 13)]

    assert all(-1 <= v <= 1 for v in bare + cropped)
    assert sum(cropped) / 12 > sum(bare) / 12


# --------------------------------------------------------------------------
# Honesty
# --------------------------------------------------------------------------


def test_simulated_meta_can_only_report_simulated() -> None:
    """Centralising provenance is what makes it impossible for a call site to claim
    its generated values were observed."""
    meta = simulated_meta()

    assert meta.mode is DataMode.simulated
    assert meta.source == "simulated"
    assert meta.is_real is False
    assert meta.note
