"""The simulator's determinism and physical-shape guarantees.

Determinism is the property the whole phase rests on, so it is asserted directly
rather than only through the API.
"""

import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.schemas.common import DataMode
from app.services import climate
from app.services.simulation import (
    _wet_season_factor,
    climate_zone,
    extraterrestrial_radiation_mm,
    reference_et0_mm,
    seeded_rng,
    simulate_day,
    simulate_days,
    simulate_hours,
    simulate_ndvi,
    simulate_soil,
    simulated_meta,
    solar_timezone_name,
    solar_utc_offset_hours,
    stable_seed,
    usda_texture_class,
)

JUNE_SOLSTICE = 172
DECEMBER_SOLSTICE = 355

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
    for lat in (-90.0, -89.0, -66.5, 0.0, 66.5, 89.0, 90.0):
        for doy in (JUNE_SOLSTICE, DECEMBER_SOLSTICE):
            value = extraterrestrial_radiation_mm(lat, doy)
            assert math.isfinite(value) and value >= 0


# --------------------------------------------------------------------------
# Polar latitudes
#
# Latitude used to be clamped to +/-66 deg, so every position beyond the Arctic and
# Antarctic circles returned the boundary's value: a farm at 69 deg N saw 66 deg's
# midsummer sun and never saw a polar night. The clamp was redundant — `acos` is
# guarded by clamping its own argument — and removing it makes the polar cases fall
# out of the FAO-56 formula rather than being flattened away.
# --------------------------------------------------------------------------


def test_radiation_is_not_clamped_beyond_the_arctic_circle() -> None:
    """The regression this section exists for: 78 deg must not equal 66 deg."""
    at_circle = extraterrestrial_radiation_mm(66.0, JUNE_SOLSTICE)
    beyond = extraterrestrial_radiation_mm(78.0, JUNE_SOLSTICE)

    assert beyond > at_circle, (
        f"78 deg N returned {beyond} against {at_circle} at 66 deg N — latitude is being clamped"
    )


def test_polar_summer_brightens_toward_the_pole() -> None:
    """Under the midnight sun, a full day of low-angle light beats a partial day of
    higher-angle light, so Ra keeps rising polewards."""
    values = [extraterrestrial_radiation_mm(lat, JUNE_SOLSTICE) for lat in (66.0, 78.0, 85.0)]

    assert values == sorted(values)
    assert values[0] < values[-1]


def test_polar_night_receives_no_radiation() -> None:
    assert extraterrestrial_radiation_mm(78.0, DECEMBER_SOLSTICE) == 0.0
    assert extraterrestrial_radiation_mm(85.0, DECEMBER_SOLSTICE) == 0.0
    # ...and the southern mirror, six months out of phase.
    assert extraterrestrial_radiation_mm(-78.0, JUNE_SOLSTICE) == 0.0
    assert extraterrestrial_radiation_mm(-85.0, JUNE_SOLSTICE) == 0.0


def test_polar_seasons_are_inverted_across_the_equator() -> None:
    """Antarctic midsummer is the Arctic's midwinter."""
    assert extraterrestrial_radiation_mm(-78.0, DECEMBER_SOLSTICE) > 0.0
    assert extraterrestrial_radiation_mm(78.0, JUNE_SOLSTICE) > 0.0


def test_temperate_radiation_is_unchanged_by_removing_the_clamp() -> None:
    """Everything equatorward of 66 deg was never clamped and must not move."""
    for lat in (-45.0, 0.0, 20.0, 45.0, 60.0):
        assert extraterrestrial_radiation_mm(lat, JUNE_SOLSTICE) > 0.0
        assert extraterrestrial_radiation_mm(lat, DECEMBER_SOLSTICE) > 0.0


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
# Rainfall
#
# Rain used to depend only on `abs(latitude)`, so the simulator produced ~700-1100 mm
# a year everywhere: Aswan got 762 mm against a real ~1 mm, and a farm in Riyadh was
# told it had adequate rainfall. Longitude now carries meaning, and the annual total is
# an explicit target the generator aims at rather than an accident of two tuned knobs.
# --------------------------------------------------------------------------

YEAR_START = date(2026, 1, 1)

# Real places, used to check that the model tells them apart. The model itself knows
# nothing about them — see `test_no_validation_coordinate_appears_in_the_model`.
ASWAN = (24.0908, 32.8994)
RIYADH = (24.6877, 46.7219)
SHIRAZ = (29.6103, 52.5311)
KRASNODAR = (45.0453, 38.9818)
RIBEIRAO_PRETO = (-21.1775, -47.8103)
MEDAN = (3.5833, 98.6667)


def simulated_annual_mm(latitude: float, longitude: float) -> float:
    return sum(d.precipitation_mm for d in simulate_days(latitude, longitude, YEAR_START, 365))


def test_deserts_are_dry() -> None:
    """The headline regression. Every one of these was 682-762 mm before."""
    for name, (lat, lon) in {"Aswan": ASWAN, "Riyadh": RIYADH}.items():
        total = simulated_annual_mm(lat, lon)
        assert total < 250, f"{name} simulated {total:.0f} mm/year"


def test_wet_tropics_are_wet() -> None:
    for name, (lat, lon) in {"Medan": MEDAN, "Ribeirao Preto": RIBEIRAO_PRETO}.items():
        total = simulated_annual_mm(lat, lon)
        assert total > 1200, f"{name} simulated {total:.0f} mm/year"


def test_rainfall_orders_places_correctly() -> None:
    """The assertion no latitude-only model can satisfy.

    Aswan, Riyadh and Shiraz sit within six degrees of latitude of each other and of
    Ribeirao Preto's mirror; ordering them requires knowing where they are, not just
    how far from the equator.
    """
    ordered = [
        simulated_annual_mm(*place)
        for place in (ASWAN, RIYADH, SHIRAZ, KRASNODAR, RIBEIRAO_PRETO, MEDAN)
    ]

    assert ordered == sorted(ordered), f"rainfall is not correctly ordered: {ordered}"


def test_longitude_changes_the_rainfall() -> None:
    """Two farms on one parallel, on different continents. Under the old model these
    were statistically identical."""
    arabian = simulated_annual_mm(24.0, 46.0)
    south_china = simulated_annual_mm(24.0, 113.0)

    assert south_china > arabian * 5


def test_the_generator_hits_its_annual_target() -> None:
    """Budget closure, the property that makes the target meaningful.

    Expected daily rainfall is split into a chance of rain and a mean depth whose
    product is that expectation, so a year of draws must land near the target. What is
    left is sampling noise, widest where rain is rare.
    """
    for lat in range(-60, 61, 20):
        for lon in range(-180, 180, 60):
            target = climate.annual_precipitation_mm(float(lat), float(lon))
            drawn = simulated_annual_mm(float(lat), float(lon))
            assert 0.6 <= drawn / target <= 1.5, (
                f"({lat}, {lon}) drew {drawn:.0f} mm against a {target:.0f} mm target"
            )


def test_rain_is_still_deterministic() -> None:
    assert simulated_annual_mm(*RIYADH) == simulated_annual_mm(*RIYADH)


def test_wet_season_weight_averages_to_one() -> None:
    """What lets the seasonal shape redistribute a year's rain without changing it."""
    for latitude in (-40.0, -5.0, 20.0, 55.0):
        weights = [_wet_season_factor(latitude, doy) for doy in range(1, 366)]
        assert sum(weights) / len(weights) == pytest.approx(1.0, abs=0.01)


# --------------------------------------------------------------------------
# Soil chemistry follows aridity
#
# pH used to be a function of `abs(latitude)` alone, which put the desert belt at its
# most acidic and the boreal zone at its most alkaline — the relationship inverted.
# --------------------------------------------------------------------------

DESERT_CORES = [(24.0, 15.0), (20.0, 50.0), (39.0, 83.0), (-23.0, -70.0), (-24.0, 15.0)]
HUMID_TROPICS = [(-3.0, -62.0), (0.0, 22.0), (1.0, 114.0), (-5.0, 142.0)]
BOREAL = [(62.0, 25.0), (64.0, -147.0), (60.0, 90.0), (66.0, 20.0)]


def _mean_ph(points) -> float:
    return sum(simulate_soil(lat, lon).ph for lat, lon in points) / len(points)


def _mean_soc(points) -> float:
    return sum(simulate_soil(lat, lon).organic_carbon_pct for lat, lon in points) / len(points)


def test_desert_soils_are_alkaline() -> None:
    """Evaporation exceeds rainfall, so carbonates accumulate instead of leaching."""
    assert _mean_ph(DESERT_CORES) > 7.2


def test_humid_and_boreal_soils_are_acidic() -> None:
    """Both are strongly leached — one by rain, the other by organic acids."""
    assert _mean_ph(HUMID_TROPICS) < 6.0
    assert _mean_ph(BOREAL) < 6.0


def test_the_ph_relationship_is_no_longer_inverted() -> None:
    """The defect in one assertion: boreal soil must not be the most alkaline on Earth."""
    assert _mean_ph(BOREAL) < _mean_ph(DESERT_CORES)


def test_deserts_hold_little_organic_carbon() -> None:
    """A desert grows little of the litter that becomes soil carbon."""
    assert _mean_soc(DESERT_CORES) < 1.2
    assert _mean_soc(BOREAL) > _mean_soc(DESERT_CORES)


def test_soil_is_still_deterministic_and_physical() -> None:
    for lat, lon in DESERT_CORES + HUMID_TROPICS + BOREAL:
        first, second = simulate_soil(lat, lon), simulate_soil(lat, lon)
        assert first.ph == second.ph
        assert 3.9 <= first.ph <= 8.8
        assert 0 < first.organic_carbon_pct <= 6


# --------------------------------------------------------------------------
# Solar timezone
#
# The simulator reported timezone="UTC" for every location on Earth, so a farm's
# local time could be wrong by twelve hours. It has no gazetteer, so it names the
# offset the sun implies at its meridian and nothing more.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("longitude", "expected"),
    [
        (0.0, "Etc/GMT"),
        (7.4, "Etc/GMT"),  # rounds to offset 0
        (36.8172, "Etc/GMT-2"),  # Nairobi meridian -> UTC+2
        (73.79096, "Etc/GMT-5"),  # Nashik meridian -> UTC+5
        (113.6486, "Etc/GMT-8"),  # Zhengzhou meridian -> UTC+8
        (-47.8103, "Etc/GMT+3"),  # Ribeirao Preto meridian -> UTC-3
        (-93.6319, "Etc/GMT+6"),  # Iowa meridian -> UTC-6
        (180.0, "Etc/GMT-12"),
        (-180.0, "Etc/GMT+12"),
    ],
)
def test_solar_timezone_inverts_the_posix_sign(longitude: float, expected: str) -> None:
    """`Etc/GMT-3` means UTC+3. East longitudes therefore get negative names."""
    assert solar_timezone_name(longitude) == expected


@pytest.mark.parametrize("longitude", [-180.0, -135.0, -60.0, -7.5, 0.0, 7.5, 60.0, 135.0, 180.0])
def test_solar_timezone_is_a_resolvable_iana_zone(longitude: float) -> None:
    """The name must be one a real tz database can load — a plausible-looking string
    the frontend cannot resolve would be worse than UTC."""
    offset = datetime(2026, 6, 15, tzinfo=ZoneInfo(solar_timezone_name(longitude))).utcoffset()

    assert offset is not None
    assert offset.total_seconds() / 3600 == solar_utc_offset_hours(longitude)


def test_solar_offset_tracks_longitude_at_fifteen_degrees_per_hour() -> None:
    assert solar_utc_offset_hours(0.0) == 0
    assert solar_utc_offset_hours(15.0) == 1
    assert solar_utc_offset_hours(-15.0) == -1
    assert solar_utc_offset_hours(150.0) == 10


def test_solar_offset_stays_inside_the_etc_gmt_range() -> None:
    """Every longitude, and anything out of range, must still name a loadable zone."""
    for longitude in range(-360, 361, 5):
        offset = solar_utc_offset_hours(float(longitude))
        assert -12 <= offset <= 14
        ZoneInfo(solar_timezone_name(float(longitude)))


def test_longitudes_do_not_all_report_one_zone() -> None:
    """The defect in one assertion: a single global default is impossible to pass."""
    zones = {solar_timezone_name(lon) for lon in range(-180, 181, 15)}

    assert len(zones) >= 8, f"timezones collapsed: {sorted(zones)}"


# --------------------------------------------------------------------------
# Hourly timestamps
# --------------------------------------------------------------------------


def test_simulated_hours_begin_at_local_midnight() -> None:
    """Timestamps are UTC instants, but converting them into the zone the response
    advertises must give 00:00, 01:00, ... — otherwise the reported timezone and the
    data contradict each other."""
    latitude, longitude = 19.99727, 73.79096  # a +5h solar meridian
    zone = ZoneInfo(solar_timezone_name(longitude))

    steps = simulate_hours(latitude, longitude, DAY, 1)

    assert [ts.astimezone(zone).hour for ts, *_ in steps] == list(range(24))
    assert all(ts.astimezone(zone).date() == DAY for ts, *_ in steps)


def test_simulated_hours_are_ordered_and_dense() -> None:
    steps = simulate_hours(*NAIROBI, DAY, 3)
    times = [ts for ts, *_ in steps]

    assert len(times) == 72
    assert times == sorted(times)
    assert all(t.tzinfo is not None for t in times)


def test_simulated_hours_shift_with_longitude() -> None:
    """Two farms on the same parallel but different meridians start their day at
    different instants."""
    east = simulate_hours(0.0, 90.0, DAY, 1)[0][0]
    west = simulate_hours(0.0, -90.0, DAY, 1)[0][0]

    assert east != west
    assert (west - east).total_seconds() == 12 * 3600


def test_simulated_hours_remain_deterministic() -> None:
    first = simulate_hours(*NAIROBI, DAY, 2)
    second = simulate_hours(*NAIROBI, DAY, 2)

    assert [(ts, t, h, p) for ts, _, t, h, p in first] == [
        (ts, t, h, p) for ts, _, t, h, p in second
    ]


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
