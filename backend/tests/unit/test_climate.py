"""The coarse global climate model.

Two things are under test. First, that the model is a well-behaved field: bounded,
continuous, defined everywhere, and free of randomness. Second — and this is the part
that matters most — that it was **not fitted to the validation sites**.

That second property cannot be established by pointing at passing assertions, because
a lookup table of the twelve P1-2 locations would pass every one of them. So it is
enforced structurally instead: regions must be too large to target a place, too few to
be a gazetteer, independent of one another, and free of any validation coordinate.
Those constraints make point-fitting mechanically impossible rather than merely
discouraged, and they fail loudly if someone later tries.
"""

import math
from pathlib import Path

import pytest

from app.services import climate
from app.services.climate import (
    REGIONS,
    annual_precipitation_mm,
    aridity_index,
    humid_index,
    normalise_longitude,
    zonal_baseline_mm,
)

# A coarse global sweep, used wherever a property must hold everywhere.
GLOBAL_SAMPLE = [
    (float(lat), float(lon)) for lat in range(-90, 91, 5) for lon in range(-180, 180, 5)
]


def _lipschitz_bound() -> float:
    """The steepest the aridity field can change per degree.

    `smoothstep` has a maximum slope of 1.5 over the feather width, and aridity is an
    arid layer minus a wet one, so two feathers can act at once.
    """
    return 2 * 1.5 * max(r.strength for r in REGIONS) / climate.FEATHER_DEG


# --------------------------------------------------------------------------
# The field is well-formed
# --------------------------------------------------------------------------


def test_indices_stay_in_range_everywhere() -> None:
    for lat, lon in GLOBAL_SAMPLE:
        assert 0.0 <= aridity_index(lat, lon) <= 1.0
        assert 0.0 <= humid_index(lat, lon) <= 1.0


def test_a_place_is_never_both_arid_and_humid() -> None:
    """The two indices are opposite ends of one axis, so at most one can be non-zero."""
    for lat, lon in GLOBAL_SAMPLE:
        assert aridity_index(lat, lon) == 0.0 or humid_index(lat, lon) == 0.0


def test_precipitation_is_defined_and_positive_everywhere() -> None:
    for lat, lon in GLOBAL_SAMPLE:
        value = annual_precipitation_mm(lat, lon)
        assert math.isfinite(value)
        assert value >= climate.MIN_ANNUAL_MM


def test_the_field_is_continuous() -> None:
    """Feathering exists so that a farm never sits on a cliff edge.

    The bound is the model's own steepest possible gradient rather than a number picked
    by hand: `smoothstep` peaks at a slope of 1.5, spread over `FEATHER_DEG` degrees and
    scaled by the strongest region. Aridity is a difference of two such layers — an arid
    feather can fade out exactly where a wet one fades in, as happens between the
    Kalahari and the Mozambique coast — so the worst case is twice that. Without
    feathering, a boundary step would be a full `strength`.
    """
    limit = _lipschitz_bound() + 1e-9

    for lat in range(-85, 86, 5):
        for lon in range(-180, 179, 1):
            here = aridity_index(float(lat), float(lon))
            neighbour = aridity_index(float(lat), float(lon + 1))
            assert abs(here - neighbour) <= limit, f"aridity jumps at ({lat}, {lon})"


def test_refining_the_step_shrinks_the_change() -> None:
    """Continuity itself, not just a gradient cap: across the sharpest transition in
    the model, halving the sampling step must halve the largest change. A step function
    would not do that — it would show the same jump at every resolution."""

    def largest_change(step: float) -> float:
        biggest = 0.0
        lon = 10.0
        while lon < 45.0:
            here = aridity_index(-28.0, lon)
            biggest = max(biggest, abs(aridity_index(-28.0, lon + step) - here))
            lon += step
        return biggest

    coarse = largest_change(1.0)
    fine = largest_change(0.5)
    finer = largest_change(0.25)

    assert fine < coarse
    assert finer < fine
    assert finer <= _lipschitz_bound() * 0.25 + 1e-9


def test_model_is_pure() -> None:
    """No RNG, no clock: the same coordinates must always give the same answer."""
    for lat, lon in [(24.0, 45.0), (-3.0, -60.0), (55.5, 12.25)]:
        assert annual_precipitation_mm(lat, lon) == annual_precipitation_mm(lat, lon)
        assert aridity_index(lat, lon) == aridity_index(lat, lon)


# --------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------


def test_longitude_wraps_at_the_antimeridian() -> None:
    assert normalise_longitude(180.0) == -180.0
    assert normalise_longitude(-180.0) == -180.0
    assert normalise_longitude(190.0) == pytest.approx(-170.0)
    assert normalise_longitude(-190.0) == pytest.approx(170.0)


def test_equivalent_longitudes_describe_one_place() -> None:
    """+185 deg and -175 deg are the same meridian and must not be two climates."""
    for lat in (-40.0, 0.0, 40.0):
        assert annual_precipitation_mm(lat, 185.0) == pytest.approx(
            annual_precipitation_mm(lat, -175.0)
        )


def test_the_poles_are_defined() -> None:
    for lon in (-180.0, 0.0, 179.9):
        for lat in (-90.0, 90.0):
            assert math.isfinite(annual_precipitation_mm(lat, lon))


# --------------------------------------------------------------------------
# The baseline has the shape circulation gives it
# --------------------------------------------------------------------------


def test_baseline_peaks_at_the_equator() -> None:
    assert zonal_baseline_mm(0.0) > zonal_baseline_mm(20.0) > zonal_baseline_mm(28.0)


def test_baseline_has_a_subtropical_minimum_and_a_midlatitude_recovery() -> None:
    """The descending branch of the Hadley cell, then the storm tracks."""
    subtropical = zonal_baseline_mm(30.0)
    midlatitude = zonal_baseline_mm(45.0)

    assert subtropical < zonal_baseline_mm(15.0)
    assert midlatitude > subtropical


def test_baseline_declines_towards_the_poles() -> None:
    """Cold air holds less moisture, so the high latitudes dry out."""
    assert zonal_baseline_mm(50.0) > zonal_baseline_mm(70.0) > zonal_baseline_mm(88.0)


def test_baseline_is_symmetric_across_the_equator() -> None:
    for lat in (5.0, 25.0, 45.0, 70.0):
        assert zonal_baseline_mm(lat) == zonal_baseline_mm(-lat)


# --------------------------------------------------------------------------
# The regions describe the world's climate, not a test fixture
# --------------------------------------------------------------------------


def test_known_deserts_are_arid() -> None:
    """Sampled at the centre of each desert, not at any town in it."""
    cores = {
        "Sahara (central Libya)": (24.0, 15.0),
        "Rub al Khali": (20.0, 50.0),
        "Taklamakan": (39.0, 83.0),
        "Atacama": (-23.0, -70.0),
        "Namib": (-24.0, 15.0),
        "Australian interior": (-25.0, 130.0),
        "Great Basin": (39.0, -117.0),
    }
    for name, (lat, lon) in cores.items():
        assert aridity_index(lat, lon) > 0.7, f"{name} is not arid"


def test_known_rainforests_are_not_arid() -> None:
    cores = {
        "Amazon": (-3.0, -62.0),
        "Congo": (0.0, 22.0),
        "Borneo": (1.0, 114.0),
        "New Guinea": (-5.0, 142.0),
    }
    for name, (lat, lon) in cores.items():
        assert aridity_index(lat, lon) == 0.0, f"{name} came out arid"
        assert annual_precipitation_mm(lat, lon) > 1500, f"{name} is too dry"


def test_both_hemispheres_have_deserts() -> None:
    """A northern-only model would be a familiar kind of bug."""
    northern = [r for r in REGIONS if r.kind == "arid" and r.lat_max > 0]
    southern = [r for r in REGIONS if r.kind == "arid" and r.lat_min < 0]

    assert len(northern) >= 4
    assert len(southern) >= 3


def test_longitude_changes_the_answer() -> None:
    """The defect this whole module exists to fix: two points on one parallel, on
    different continents, must not have the same climate."""
    arabian = annual_precipitation_mm(24.0, 46.0)
    south_china = annual_precipitation_mm(24.0, 113.0)

    assert south_china > arabian * 3


# --------------------------------------------------------------------------
# Anti-tuning guards
#
# These do not check that the model is *right*. They check that it cannot have been
# made right by memorising the places it is tested against.
# --------------------------------------------------------------------------


def test_no_region_is_small_enough_to_target_a_place() -> None:
    """A region fitted to one town would be a degree or two across. Every region here
    spans at least 6 deg on each axis and 100 square degrees — hundreds of kilometres,
    a continental feature rather than a pin."""
    for region in REGIONS:
        lat_span = region.lat_max - region.lat_min
        lon_span = region.lon_max - region.lon_min

        assert lat_span >= 6.0, f"{region.name} spans only {lat_span} deg of latitude"
        assert lon_span >= 6.0, f"{region.name} spans only {lon_span} deg of longitude"
        assert lat_span * lon_span >= 100.0, f"{region.name} is too small to be a climate region"


def test_the_region_table_cannot_grow_into_a_gazetteer() -> None:
    """The model is a description of world climate, not a lookup of interesting
    coordinates. If this needs raising, the approach needs revisiting instead."""
    assert len(REGIONS) <= 20


def test_every_region_is_named_and_justified() -> None:
    """A reader must be able to check each entry against an atlas."""
    for region in REGIONS:
        assert region.name.strip()
        assert len(region.note.strip()) > 20, f"{region.name} has no explanation"
        assert 0.0 < region.strength <= 1.0


def test_no_validation_coordinate_appears_in_the_model() -> None:
    """The strongest guard available in source form: if the twelve P1-2 sites had been
    fitted, their coordinates would be here."""
    from tests.fixtures.open_meteo import SITES

    source = (Path(__file__).resolve().parents[2] / "app" / "services" / "climate.py").read_text()

    for site in SITES:
        for value in (site.latitude, site.longitude):
            # Four decimal places identify a town; the region table deals in whole
            # degrees, so any such literal would be a fitted value.
            assert f"{value:.4f}" not in source, f"{site.name}'s coordinates are in climate.py"
            assert f"{value:.3f}" not in source, f"{site.name}'s coordinates are in climate.py"


def test_every_region_acts_only_where_it_is(monkeypatch) -> None:
    """Each region's influence must be local to its own extent.

    Regions may legitimately overlap — the Arabian and Iranian deserts are contiguous
    across the Persian Gulf, and drawing them as two rectangles that meet is honest
    geography. What must never happen is a region reaching somewhere it does not
    describe, which is how a table starts compensating for its own errors elsewhere.
    So this removes each region in turn and checks that nothing changed beyond its own
    boundary plus the feather width.
    """
    probes = [
        (float(lat), float(lon)) for lat in range(-80, 81, 10) for lon in range(-180, 180, 10)
    ]
    baseline = {p: annual_precipitation_mm(*p) for p in probes}

    for removed in REGIONS:
        monkeypatch.setattr(climate, "REGIONS", tuple(r for r in REGIONS if r is not removed))
        try:
            for probe in probes:
                lat, lon = probe
                lat_gap = max(removed.lat_min - lat, lat - removed.lat_max, 0.0)
                lon_gap = climate._longitude_gap(
                    climate.normalise_longitude(lon), removed.lon_min, removed.lon_max
                )
                if math.hypot(lat_gap, lon_gap) < climate.FEATHER_DEG:
                    continue  # inside its own reach; it is supposed to matter here
                assert annual_precipitation_mm(lat, lon) == baseline[probe], (
                    f"removing {removed.name} changed ({lat}, {lon}), which is outside its reach"
                )
        finally:
            monkeypatch.setattr(climate, "REGIONS", REGIONS)


# --------------------------------------------------------------------------
# Hold-out validation
#
# Sixteen places with published climate normals, none of them among the twelve P1-2
# validation sites and none consulted while the region table was being authored. They
# are the empirical half of the anti-tuning argument: the structural guards above show
# the table *cannot* be a lookup of test coordinates, and this shows it generalises to
# places it has never been shown.
#
# Result when P2-1b landed: 14/16 within 2.5x, worst case 6.4x.
#
# The two misses are documented, not silently absorbed:
#
#   Perth  0.16x — sits inside the Australian interior rectangle, because south-west
#                  Australia's Mediterranean corner is diagonal to any rectangle.
#   Lima   5.9x  — hyper-arid at a tropical latitude. Full aridity leaves 3% of a
#                  ~1500 mm baseline, or ~45 mm; Lima records ~15 mm. Two layers cannot
#                  reach that without a floor low enough to break other places.
#
# Both are the coarse-rectangle and no-elevation limits the design review predicted.
# Fixing them means polygons or an elevation term, which is a later milestone; tuning
# a region to make one city pass is what the guards above exist to prevent.
# --------------------------------------------------------------------------

# (name, latitude, longitude, mean annual mm from published normals)
HOLD_OUT: tuple[tuple[str, float, float, float], ...] = (
    ("Phoenix", 33.45, -112.07, 200),
    ("Cairo", 30.04, 31.24, 25),
    ("Lima", -12.05, -77.04, 15),
    ("Alice Springs", -23.70, 133.88, 280),
    ("Ulaanbaatar", 47.89, 106.91, 260),
    ("Manaus", -3.12, -60.02, 2300),
    ("Kinshasa", -4.44, 15.27, 1450),
    ("Mumbai", 19.08, 72.88, 2200),
    ("Seattle", 47.61, -122.33, 950),
    ("Perth", -31.95, 115.86, 730),
    ("Reykjavik", 64.15, -21.94, 800),
    ("Windhoek", -22.56, 17.08, 360),
    ("Karachi", 24.86, 67.01, 170),
    ("Kolkata", 22.57, 88.36, 1600),
    ("Buenos Aires", -34.60, -58.38, 1200),
    ("Chicago", 41.88, -87.63, 970),
)


def _error_factor(modelled: float, observed: float) -> float:
    return max(modelled, observed) / min(modelled, observed)


def test_holdout_locations_are_mostly_within_a_factor_of_two_and_a_half() -> None:
    """A coarse model held to a coarse standard, on places it was not built against."""
    within = [
        name
        for name, lat, lon, observed in HOLD_OUT
        if _error_factor(annual_precipitation_mm(lat, lon), observed) <= 2.5
    ]

    assert len(within) >= 12, (
        f"only {len(within)}/{len(HOLD_OUT)} hold-out locations within 2.5x: "
        f"missed {[n for n, *_ in HOLD_OUT if n not in within]}"
    )


def test_no_holdout_location_is_wildly_wrong() -> None:
    """The two known misses sit at 5.9x and 6.4x. Anything past 8x would be a new
    failure mode rather than the documented coarseness."""
    for name, lat, lon, observed in HOLD_OUT:
        factor = _error_factor(annual_precipitation_mm(lat, lon), observed)
        assert factor <= 8.0, f"{name} is off by {factor:.1f}x"


def test_holdout_deserts_stay_drier_than_holdout_rainforests() -> None:
    """Ordering survives even where magnitudes do not — the property that matters most
    for a water-balance calculation."""
    driest = max(
        annual_precipitation_mm(lat, lon)
        for name, lat, lon, _ in HOLD_OUT
        if name in {"Cairo", "Lima", "Alice Springs", "Phoenix"}
    )
    wettest = min(
        annual_precipitation_mm(lat, lon)
        for name, lat, lon, _ in HOLD_OUT
        if name in {"Manaus", "Kinshasa", "Buenos Aires"}
    )

    assert driest < wettest


def test_strengths_are_coarse_values() -> None:
    """Strengths describe how dry a desert is, on a scale a person can defend. A
    strength like 0.8734 would be the fingerprint of a fit."""
    for region in REGIONS:
        assert region.strength == pytest.approx(round(region.strength, 2)), (
            f"{region.name} has a suspiciously precise strength: {region.strength}"
        )
