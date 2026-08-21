"""A coarse global climate model: where the world is wet, and where it is dry.

**This is hand-authored coarse geography, not a published climate dataset.** Every
region below is a named, real feature of world climate — the Sahara, the Atacama, the
humid subtropical east coasts — drawn as a rectangle from general geography and
verifiable against any atlas. It is deliberately not a Koppen-Geiger raster, an
interpolated station archive, or anything else with a citation and a resolution. It is
accurate to the continent, not to the valley, and callers should treat it that way.

**Why it exists.** The simulator's only geographic input used to be `abs(latitude)`,
so Riyadh (24.7 deg N, ~110 mm/year) and Guangzhou (23.1 deg N, ~1700 mm/year) were
climatically identical to it. That is not a tuning problem: the subtropical desert belt
*is* a latitude phenomenon, but whether a given point inside it is Sahara or Florida
depends on land, sea and circulation. No function of latitude alone can separate them,
because the separating variable is geography. So geography is embedded here, in the
smallest and most explainable form that still generalises worldwide.

**Two layers.**

1. A *meridional baseline* — the real zonal shape of atmospheric circulation. An ITCZ
   maximum at the equator, the subtropical subsidence minimum near 25-30 deg, the
   mid-latitude storm-track recovery, and the polar decline as the air runs out of
   capacity to hold moisture. This is the humid case: what falls at a latitude where
   nothing blocks the moisture.

2. Named *regional departures* from it — nineteen in all. Twelve arid regions covering
   the world's deserts, continental interiors and rain shadows, and seven wet ones
   covering the monsoon coasts and the humid subtropical east margins that the
   baseline's subtropical minimum understates.

Everything here is a pure function of coordinates: no randomness, no I/O, no clock. The
simulator layers its own seeded noise on top; this module only decides the target.
"""

import math
from dataclasses import dataclass
from typing import Literal

# How far a region's influence reaches beyond its own edge, in degrees. Real
# desert margins are transitions, not walls — the Sahel is ~500 km of gradient
# between the Sahara and the savanna — so influence feathers out rather than
# stopping at a rectangle's boundary.
FEATHER_DEG = 5.0

# What full aridity does to the baseline. 0.97 leaves 3% rather than zero: even
# hyper-arid places record rain, just rarely.
ARID_DEPTH = 0.97

# What full wetness does. 1.2 lets a monsoon coast reach ~2.2x its latitude's
# baseline, which is the observed order for the Guinea coast and southern Japan.
WET_GAIN = 1.2

# Annual totals never reach zero; the driest places on Earth still measure a
# millimetre or two in an average year.
MIN_ANNUAL_MM = 5.0


# --------------------------------------------------------------------------
# Meridional baseline
# --------------------------------------------------------------------------

# (|latitude|, mm/year) anchors for the humid case. The shape is the standard
# zonal profile of precipitation: ITCZ peak, subtropical minimum under the
# descending branch of the Hadley cell, mid-latitude recovery under the storm
# tracks, then a polar decline driven by the Clausius-Clapeyron limit on how much
# moisture cold air can carry.
_BASELINE_ANCHORS: tuple[tuple[float, float], ...] = (
    (0.0, 2200.0),
    (10.0, 1600.0),
    (20.0, 1100.0),
    (25.0, 900.0),
    (30.0, 820.0),
    (35.0, 850.0),
    (40.0, 950.0),
    (45.0, 1000.0),
    (50.0, 950.0),
    (60.0, 700.0),
    (70.0, 450.0),
    (80.0, 280.0),
    (90.0, 180.0),
)


# --------------------------------------------------------------------------
# Regions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ClimateRegion:
    """One coarse geographic feature that departs from the zonal baseline.

    `strength` is how far it departs at full influence, on 0-1. It is a property of
    the *feature* — the Atacama is drier than Patagonia — never a value chosen to make
    a particular town come out right.
    """

    name: str
    kind: Literal["arid", "wet"]
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    strength: float
    note: str


REGIONS: tuple[ClimateRegion, ...] = (
    # ---- Arid: the subtropical desert belt, continental interiors, rain shadows ----
    ClimateRegion(
        name="Sahara",
        kind="arid",
        lat_min=15.0,
        lat_max=31.0,
        lon_min=-17.0,
        lon_max=36.0,
        strength=1.00,
        note="The largest hot desert; hyper-arid from the Atlantic coast to the Red Sea.",
    ),
    ClimateRegion(
        name="Arabian Desert",
        kind="arid",
        lat_min=13.0,
        lat_max=33.0,
        lon_min=34.0,
        lon_max=60.0,
        strength=0.97,
        note="The Arabian Peninsula, including the Rub' al Khali and the Nefud.",
    ),
    ClimateRegion(
        name="Iranian Plateau and Thar",
        kind="arid",
        lat_min=24.0,
        lat_max=38.0,
        lon_min=44.0,
        lon_max=76.0,
        strength=0.85,
        note="Iran, Afghanistan, Baluchistan and the Thar; arid but less so than the Sahara.",
    ),
    ClimateRegion(
        name="Turkestan Deserts",
        kind="arid",
        lat_min=36.0,
        lat_max=47.0,
        lon_min=52.0,
        lon_max=72.0,
        strength=0.85,
        note="Karakum and Kyzylkum, in the deep continental interior of Central Asia.",
    ),
    ClimateRegion(
        name="Taklamakan and Gobi",
        kind="arid",
        lat_min=36.0,
        lat_max=47.0,
        lon_min=76.0,
        lon_max=112.0,
        strength=0.90,
        note="Behind the Tibetan Plateau and the Tien Shan; among the most continental on Earth.",
    ),
    ClimateRegion(
        name="North American Deserts",
        kind="arid",
        lat_min=24.0,
        lat_max=42.0,
        lon_min=-120.0,
        lon_max=-103.0,
        strength=0.85,
        note="Sonoran, Mojave, Chihuahuan and Great Basin, in the lee of the Sierra Nevada.",
    ),
    ClimateRegion(
        name="Peruvian Coastal Desert",
        kind="arid",
        lat_min=-19.0,
        lat_max=-4.0,
        lon_min=-82.0,
        lon_max=-74.0,
        strength=0.98,
        note="The coastal strip west of the Peruvian Andes, dried by the cold Humboldt current.",
    ),
    ClimateRegion(
        name="Atacama",
        kind="arid",
        lat_min=-31.0,
        lat_max=-15.0,
        lon_min=-73.0,
        lon_max=-66.0,
        strength=0.98,
        note="Humboldt current plus subtropical subsidence; the driest desert on Earth.",
    ),
    ClimateRegion(
        name="Patagonia",
        kind="arid",
        lat_min=-52.0,
        lat_max=-38.0,
        lon_min=-73.0,
        lon_max=-65.0,
        strength=0.75,
        note="Rain shadow east of the southern Andes, under the westerlies.",
    ),
    ClimateRegion(
        name="Namib, Kalahari and Karoo",
        kind="arid",
        lat_min=-34.0,
        lat_max=-15.0,
        lon_min=12.0,
        lon_max=26.0,
        strength=0.85,
        note="Southern African drylands; the Namib coast is hyper-arid under the Benguela current.",
    ),
    ClimateRegion(
        name="Australian Interior",
        kind="arid",
        lat_min=-33.0,
        lat_max=-19.0,
        lon_min=113.0,
        lon_max=145.0,
        strength=0.88,
        note="The arid centre of the continent, from the Great Sandy to the Simpson.",
    ),
    ClimateRegion(
        name="Horn of Africa",
        kind="arid",
        lat_min=2.0,
        lat_max=18.0,
        lon_min=40.0,
        lon_max=52.0,
        strength=0.85,
        note=(
            "Somali and Danakil drylands. Bounded west by the Ethiopian Highlands, whose "
            "crest near 39-40 deg E is among the wettest parts of Africa, not part of this."
        ),
    ),
    # ---- Wet: monsoon coasts and humid subtropical east margins ----
    ClimateRegion(
        name="Guinea Coast",
        kind="wet",
        lat_min=4.0,
        lat_max=11.0,
        lon_min=-17.0,
        lon_max=15.0,
        strength=0.35,
        note="The West African monsoon coast, among the wettest places in Africa.",
    ),
    ClimateRegion(
        name="South China and Indochina",
        kind="wet",
        lat_min=5.0,
        lat_max=32.0,
        lon_min=96.0,
        lon_max=125.0,
        strength=0.45,
        note="East Asian monsoon; the humid subtropical margin the zonal minimum understates.",
    ),
    ClimateRegion(
        name="Japan and Korea",
        kind="wet",
        lat_min=30.0,
        lat_max=45.0,
        lon_min=126.0,
        lon_max=146.0,
        strength=0.50,
        note="Baiu front plus typhoons on a mid-latitude east margin.",
    ),
    ClimateRegion(
        name="Southeast United States",
        kind="wet",
        lat_min=25.0,
        lat_max=40.0,
        lon_min=-95.0,
        lon_max=-75.0,
        strength=0.50,
        note="Gulf and Atlantic coastal plain; maritime tropical air on a subtropical east coast.",
    ),
    ClimateRegion(
        name="Southeast South America",
        kind="wet",
        lat_min=-35.0,
        lat_max=-15.0,
        lon_min=-60.0,
        lon_max=-40.0,
        strength=0.35,
        note="South Atlantic convergence zone over southern Brazil and the Plata basin.",
    ),
    ClimateRegion(
        name="Southeast Africa",
        kind="wet",
        lat_min=-30.0,
        lat_max=-12.0,
        lon_min=30.0,
        lon_max=42.0,
        strength=0.30,
        note="Indian Ocean moisture on the Mozambique and KwaZulu coasts.",
    ),
    ClimateRegion(
        name="East Australian Coast",
        kind="wet",
        lat_min=-38.0,
        lat_max=-15.0,
        lon_min=145.0,
        lon_max=154.0,
        strength=0.35,
        note="The narrow humid strip east of the Great Dividing Range.",
    ),
)


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


def normalise_longitude(longitude: float) -> float:
    """Fold a longitude into [-180, 180) so the antimeridian wraps correctly."""
    return (longitude + 180.0) % 360.0 - 180.0


def _smoothstep(t: float) -> float:
    """A cubic ease over [0, 1]; flat at both ends so influence has no corners."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _longitude_gap(longitude: float, lon_min: float, lon_max: float) -> float:
    """Degrees of longitude between a point and a span, the short way round."""
    if lon_min <= longitude <= lon_max:
        return 0.0
    below = abs(normalise_longitude(lon_min - longitude))
    above = abs(normalise_longitude(longitude - lon_max))
    return min(below, above)


def _influence(region: ClimateRegion, latitude: float, longitude: float) -> float:
    """How strongly `region` acts at this point: full inside, feathering to nothing
    `FEATHER_DEG` beyond its edge."""
    lat_gap = max(region.lat_min - latitude, latitude - region.lat_max, 0.0)
    lon_gap = _longitude_gap(normalise_longitude(longitude), region.lon_min, region.lon_max)
    if lat_gap == 0.0 and lon_gap == 0.0:
        return region.strength

    gap = math.hypot(lat_gap, lon_gap)
    if gap >= FEATHER_DEG:
        return 0.0
    return region.strength * _smoothstep(1.0 - gap / FEATHER_DEG)


def _strongest(kind: str, latitude: float, longitude: float) -> float:
    """The dominant influence of one kind. Overlapping features do not stack — two
    deserts side by side make one desert, not a drier one."""
    return max(
        (_influence(r, latitude, longitude) for r in REGIONS if r.kind == kind),
        default=0.0,
    )


# --------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------


def zonal_baseline_mm(latitude: float) -> float:
    """Humid-case annual precipitation at this latitude, in mm.

    What falls where moisture is not blocked — the regional layer takes it away.
    """
    abs_lat = min(abs(latitude), 90.0)
    previous_lat, previous_mm = _BASELINE_ANCHORS[0]
    for anchor_lat, anchor_mm in _BASELINE_ANCHORS[1:]:
        if abs_lat <= anchor_lat:
            span = anchor_lat - previous_lat
            t = 0.0 if span == 0 else (abs_lat - previous_lat) / span
            return previous_mm + t * (anchor_mm - previous_mm)
        previous_lat, previous_mm = anchor_lat, anchor_mm
    return _BASELINE_ANCHORS[-1][1]


def aridity_index(latitude: float, longitude: float) -> float:
    """How dry this place is relative to its latitude, on 0 (humid) to 1 (hyper-arid).

    Also the driver of soil chemistry: aridity is what decides whether bases leach out
    of a profile or carbonates accumulate in it.
    """
    return max(
        0.0,
        min(1.0, _strongest("arid", latitude, longitude) - _strongest("wet", latitude, longitude)),
    )


def humid_index(latitude: float, longitude: float) -> float:
    """How wet this place is relative to its latitude, on 0 to 1. The mirror of
    :func:`aridity_index`; at most one of the two is ever non-zero."""
    return max(
        0.0,
        min(1.0, _strongest("wet", latitude, longitude) - _strongest("arid", latitude, longitude)),
    )


def annual_precipitation_mm(latitude: float, longitude: float) -> float:
    """Target mean annual precipitation at these coordinates, in mm.

    This is the quantity the simulator aims at. Making it explicit is the point: the
    old model tuned a daily probability and an event size independently and let the
    annual total fall out as an accident, which made it untestable and, in the Sahara,
    wrong by a factor of several hundred.
    """
    baseline = zonal_baseline_mm(latitude)
    arid = aridity_index(latitude, longitude)
    humid = humid_index(latitude, longitude)
    return max(MIN_ANNUAL_MM, baseline * (1.0 - ARID_DEPTH * arid) * (1.0 + WET_GAIN * humid))
