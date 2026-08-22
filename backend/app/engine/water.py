"""FAO-56 soil water balance.

A daily root-zone depletion model, following FAO Irrigation and Drainage Paper 56.
Given a window of weather, a soil, and a crop, it tracks how much water the root zone
has lost and reports when the crop will begin to suffer for it.

**Why daily rather than a single bulk balance.** Summing rainfall and demand over a
month and subtracting answers "was there enough water overall", which is not the
question a farmer has. Thirty millimetres of rain on one day and none for the following
three weeks nets out the same as a millimetre a day, but only one of them stresses the
crop. Depletion has to be carried forward day by day, clamped at both ends, for
`days_until_stress` to mean anything.

**What this module refuses to do.** It will not invent a soil. The reservoir depends on
texture and rooting depth, and with neither available there is no balance to compute —
so the result is marked insufficient rather than defaulted to a plausible number. A
fabricated capacity produces a fabricated irrigation recommendation, and a farmer acting
on it wastes water or loses a crop.

Pure: no I/O, no clock, no randomness. Every value comes from the context.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from app.engine.context import AnalysisContext, DailyPoint
from app.engine.scoring import INSUFFICIENT, factor, risk_level_for, unknown_factor
from app.schemas.common import RiskLevel, ScoredFactor

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: Total available water by USDA texture class, as a volumetric fraction — FAO-56
#: Table 19, the midpoint of each class's (theta_FC - theta_WP) range.
#:
#: Duplicated from `app.services.simulation`, which uses the same table to *simulate* a
#: soil profile. The engine cannot import a service, and the two uses are genuinely
#: different — one invents a plausible soil, one computes a balance from a real one.
#: `test_water.py` asserts the two tables agree, so a correction to either is caught.
AVAILABLE_WATER_FRACTION: dict[str, float] = {
    "sand": 0.06,
    "loamy_sand": 0.08,
    "sandy_loam": 0.12,
    "loam": 0.17,
    "silt_loam": 0.19,
    "silt": 0.18,
    "sandy_clay_loam": 0.14,
    "clay_loam": 0.16,
    "silty_clay_loam": 0.17,
    "sandy_clay": 0.13,
    "silty_clay": 0.14,
    "clay": 0.13,
}

#: The depth a reported `water_holding_capacity_mm` refers to. Providers report soil
#: properties for a fixed interval; dividing by it recovers the volumetric fraction so
#: the engine can re-scale to the crop's actual rooting depth.
REFERENCE_SOIL_DEPTH_MM = 300.0

#: Fraction of applied water that reaches the root zone, by irrigation system.
#:
#: These are *efficiencies*, and they divide the recommendation: delivering 20 mm to the
#: roots through furrows costs 33 mm at the head of the field, through drip 22 mm. They
#: do not reduce depletion — only water actually applied does that.
APPLICATION_EFFICIENCY: dict[str, float] = {
    "drip": 0.90,
    "sprinkler": 0.75,
    "furrow": 0.60,
    "flood": 0.55,
}

#: Rain that never reaches the soil. Light showers wet the canopy and evaporate; FAO-56
#: treats them as ineffective. Heavier rain in excess of the reservoir is handled by
#: clamping depletion at zero, which is the model's runoff and deep-percolation term.
INTERCEPTION_MM = 2.0

#: Assessment window: a month of history to establish depletion, a week of forecast to
#: project it forward.
HISTORY_DAYS = 30
FORECAST_DAYS = 7

#: Score at which the crop reaches readily-available-water depletion. Chosen to equal
#: the `low` -> `moderate` boundary in `scoring.risk_level_for`, so the agronomic
#: threshold and the reported risk level cross at exactly the same point rather than
#: drifting apart.
STRESS_ONSET_SCORE = 28.0

#: Below this the recommendation is noise rather than advice.
MINIMUM_USEFUL_IRRIGATION_MM = 5.0


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WaterAssessment:
    """Everything the water balance determined.

    The service maps this onto the frozen `WaterRisk` schema. Keeping the engine's own
    result separate means the engine can report things the contract has no field for —
    how many days used an estimated ET₀, which inputs were missing — without either
    losing them or forcing a contract change.
    """

    sufficient: bool
    score: float
    level: RiskLevel

    # Reservoir
    taw_mm: float | None = None
    raw_mm: float | None = None
    depletion_mm: float | None = None
    stress_coefficient: float | None = None

    # Contract-facing totals
    water_balance_mm: float = 0.0
    deficit_mm: float = 0.0
    total_precipitation_mm: float | None = None
    total_crop_demand_mm: float | None = None
    soil_moisture_pct: float | None = None

    # Advice
    days_until_stress: int | None = None
    net_irrigation_mm: float = 0.0
    applied_irrigation_mm: float = 0.0
    application_efficiency: float | None = None

    # Provenance
    estimated_et0_days: int = 0
    missing: tuple[str, ...] = ()
    drivers: tuple[str, ...] = ()
    factors: tuple[ScoredFactor, ...] = ()
    explanation: str = ""
    parameters_source: str = "unknown"

    days: tuple["DayState", ...] = field(default=())


@dataclass(frozen=True, slots=True)
class DayState:
    """One day of the balance, retained so a test or a UI can trace the derivation."""

    day: date
    et0_mm: float
    et0_estimated: bool
    kc: float
    etc_mm: float
    stress_coefficient: float
    effective_rain_mm: float
    depletion_mm: float


# --------------------------------------------------------------------------
# Physics
# --------------------------------------------------------------------------


def extraterrestrial_radiation_mm(latitude: float, day_of_year: int) -> float:
    """FAO-56 extraterrestrial radiation (Ra), in mm/day equivalent.

    Latitude is deliberately not clamped: `acos` is given an already-bounded argument,
    and the boundary cases are the physically correct ones — an argument of -1 is
    twenty-four-hour daylight, +1 is polar night with Ra of zero. Clamping latitude
    instead would collapse every farm beyond the polar circles onto the same value.
    """
    phi = math.radians(latitude)
    dr = 1 + 0.033 * math.cos(2 * math.pi * day_of_year / 365.25)
    decl = 0.409 * math.sin(2 * math.pi * day_of_year / 365.25 - 1.39)

    cos_ws = max(-1.0, min(1.0, -math.tan(phi) * math.tan(decl)))
    ws = math.acos(cos_ws)

    ra_mj = (
        (24 * 60 / math.pi)
        * 0.0820
        * dr
        * (ws * math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.sin(ws))
    )
    return max(0.0, ra_mj * 0.408)


def hargreaves_et0_mm(mean_temp_c: float, diurnal_range_c: float, ra_mm: float) -> float:
    """Hargreaves reference evapotranspiration — the FAO-56 fallback method.

    Used only when the provider supplies no Penman-Monteith ET₀ for a day. It needs
    nothing but temperature and latitude, which is exactly why FAO-56 recommends it
    where humidity, wind and radiation are unavailable.
    """
    et0 = 0.0023 * ra_mm * (mean_temp_c + 17.8) * math.sqrt(max(diurnal_range_c, 1.0))
    return max(0.0, et0)


def effective_rainfall_mm(precipitation_mm: float) -> float:
    """The share of a day's rain that reaches the root zone.

    Rain below the interception threshold wets the canopy and evaporates. Above it, all
    of the rain is credited here and any excess beyond field capacity is removed by the
    depletion clamp — which is the model's runoff and percolation term. Applying a
    second runoff fraction on top would double-count it.
    """
    if precipitation_mm <= INTERCEPTION_MM:
        return 0.0
    return precipitation_mm - INTERCEPTION_MM


def stress_coefficient(depletion_mm: float, taw_mm: float, raw_mm: float) -> float:
    """FAO-56 Ks: how much transpiration the crop can still sustain.

    One while readily available water remains, then falling linearly to zero at the
    wilting point. The kink at RAW is the definition of the onset of water stress.
    """
    if depletion_mm <= raw_mm:
        return 1.0
    remaining = taw_mm - raw_mm
    if remaining <= 0:
        return 0.0
    return max(0.0, min(1.0, (taw_mm - depletion_mm) / remaining))


def total_available_water_mm(available_water_fraction: float, root_depth_m: float) -> float:
    """TAW = 1000 x (theta_FC - theta_WP) x Zr, in millimetres."""
    return 1000.0 * available_water_fraction * root_depth_m


# --------------------------------------------------------------------------
# Input resolution
# --------------------------------------------------------------------------


def resolve_available_water_fraction(context: AnalysisContext) -> tuple[float | None, str]:
    """The soil's volumetric available water, and where the value came from.

    Three sources in descending order of directness. There is deliberately no fourth:
    with none of them the caller gets `None` and reports insufficient data.
    """
    soil = context.soil

    if soil.available_water_fraction is not None:
        return soil.available_water_fraction, "provider"

    if soil.texture_class and soil.texture_class in AVAILABLE_WATER_FRACTION:
        return AVAILABLE_WATER_FRACTION[soil.texture_class], "texture"

    if soil.water_holding_capacity_mm is not None and soil.water_holding_capacity_mm > 0:
        return soil.water_holding_capacity_mm / REFERENCE_SOIL_DEPTH_MM, "reported_capacity"

    return None, "unavailable"


def application_efficiency_for(irrigation_type: str | None) -> float | None:
    """Fraction of applied water reaching the root zone, or None if the farm is rainfed.

    `rainfed`, `none` and an unset type all mean the same thing: there is no system to
    apply water through, so there is no efficiency to divide by.
    """
    if not irrigation_type:
        return None
    return APPLICATION_EFFICIENCY.get(irrigation_type)


def daily_et0_mm(day: DailyPoint, latitude: float) -> tuple[float | None, bool]:
    """A day's reference evapotranspiration, and whether it had to be estimated.

    Prefers the provider's Penman-Monteith figure, which is computed from real
    radiation, humidity and wind. Falls back to Hargreaves from the day's temperature
    range only when the provider omitted it.
    """
    if day.et0_mm is not None:
        return day.et0_mm, False

    if day.temp_max_c is None or day.temp_min_c is None:
        return None, False

    mean = day.temp_mean_c
    if mean is None:
        mean = (day.temp_max_c + day.temp_min_c) / 2.0

    ra = extraterrestrial_radiation_mm(latitude, day.day.timetuple().tm_yday)
    return hargreaves_et0_mm(mean, day.temp_max_c - day.temp_min_c, ra), True


# --------------------------------------------------------------------------
# The balance
# --------------------------------------------------------------------------


def _insufficient(missing: Sequence[str], parameters_source: str) -> WaterAssessment:
    """A result that reports what could not be computed and computes nothing else.

    Every numeric field the contract requires stays at zero rather than being guessed.
    The caller marks the run partial; the explanation names the gap.
    """
    listed = ", ".join(missing) if missing else "weather data"
    labels = list(missing) or ["weather data"]
    return WaterAssessment(
        sufficient=False,
        score=0.0,
        level=RiskLevel.low,
        missing=tuple(missing),
        drivers=(
            f"{INSUFFICIENT}: {listed} unavailable, so the soil water balance was not calculated",
        ),
        factors=(unknown_factor("water_balance", "Water balance", labels),),
        explanation=(f"{INSUFFICIENT}: {listed} unavailable, so water risk could not be assessed."),
        parameters_source=parameters_source,
    )


def evaluate(context: AnalysisContext) -> WaterAssessment:
    """Run the daily water balance over the assessment window.

    Depletion starts at zero — the root zone is assumed at field capacity thirty days
    ago. Without stored state from a previous run there is no better cold start, and a
    month of real weather dominates the assumption well before the forecast window.
    """
    window = context.window(HISTORY_DAYS, FORECAST_DAYS)
    crop = context.crop

    missing: list[str] = []

    kc = crop.kc_for(context.growth_stage)
    if kc is None:
        missing.append("crop coefficient")

    fraction, fraction_source = resolve_available_water_fraction(context)
    root_depth = crop.root_depth_m
    if fraction is None:
        missing.append("soil texture or water-holding capacity")
    if root_depth is None:
        missing.append("crop rooting depth")

    if not window:
        missing.append("weather data")

    if missing:
        return _insufficient(missing, crop.parameters_source)

    # Narrow for the type checker; each is guaranteed non-None by the guard above.
    assert kc is not None and fraction is not None and root_depth is not None

    taw = total_available_water_mm(fraction, root_depth)
    depletion_fraction = crop.depletion_fraction if crop.depletion_fraction is not None else 0.5
    raw = depletion_fraction * taw

    if taw <= 0:
        return _insufficient(["a soil reservoir greater than zero"], crop.parameters_source)

    depletion = 0.0
    states: list[DayState] = []
    precipitation_total = 0.0
    demand_total = 0.0
    et0_readings = 0
    estimated_days = 0
    rain_readings = 0

    for day in window:
        et0, estimated = daily_et0_mm(day, context.latitude)
        if et0 is None:
            # No temperature either. The day contributes nothing rather than being
            # treated as a day of zero demand, which would understate the deficit.
            continue
        et0_readings += 1
        estimated_days += int(estimated)

        rain = day.precipitation_mm
        if rain is not None:
            rain_readings += 1
            precipitation_total += rain
        effective_rain = effective_rainfall_mm(rain) if rain is not None else 0.0

        ks = stress_coefficient(depletion, taw, raw)
        etc = et0 * kc
        demand_total += etc

        depletion = max(0.0, min(taw, depletion - effective_rain + etc * ks))

        states.append(
            DayState(
                day=day.day,
                et0_mm=et0,
                et0_estimated=estimated,
                kc=kc,
                etc_mm=etc,
                stress_coefficient=ks,
                effective_rain_mm=effective_rain,
                depletion_mm=depletion,
            )
        )

    if et0_readings == 0:
        return _insufficient(["reference evapotranspiration"], crop.parameters_source)
    if rain_readings == 0:
        return _insufficient(["precipitation"], crop.parameters_source)

    final_ks = stress_coefficient(depletion, taw, raw)

    # The contract documents `water_balance_mm` as exactly this difference of sums, so
    # it is reported unchanged even though the depletion model is what drives the score.
    balance = precipitation_total - demand_total

    days_until_stress = _days_until_stress(states, context.today, raw)

    net_irrigation = depletion if depletion >= raw else 0.0
    efficiency = application_efficiency_for(context.irrigation_type)
    applied = net_irrigation / efficiency if efficiency else net_irrigation
    if applied < MINIMUM_USEFUL_IRRIGATION_MM:
        net_irrigation, applied = 0.0, 0.0

    score = _score(depletion, taw, raw)

    return WaterAssessment(
        sufficient=True,
        score=score,
        level=risk_level_for(score),
        taw_mm=taw,
        raw_mm=raw,
        depletion_mm=depletion,
        stress_coefficient=final_ks,
        water_balance_mm=balance,
        deficit_mm=max(0.0, -balance),
        total_precipitation_mm=precipitation_total,
        total_crop_demand_mm=demand_total,
        soil_moisture_pct=100.0 * (1.0 - depletion / taw),
        days_until_stress=days_until_stress,
        net_irrigation_mm=net_irrigation,
        applied_irrigation_mm=applied,
        application_efficiency=efficiency,
        estimated_et0_days=estimated_days,
        drivers=_drivers(
            precipitation_total, demand_total, taw, raw, depletion, efficiency, estimated_days
        ),
        factors=_factors(depletion, taw, raw, score, efficiency, crop.parameters_source),
        explanation=_explanation(balance, depletion, raw, score),
        parameters_source=crop.parameters_source,
        days=tuple(states),
    )


def _days_until_stress(states: Sequence[DayState], today: date, raw_mm: float) -> int | None:
    """Days from today until depletion first crosses the readily-available threshold.

    Counted only across the forecast, because a crossing that already happened is not a
    projection. Zero means the crop is in stress now.
    """
    forecast = [state for state in states if state.day >= today]
    for offset, state in enumerate(forecast):
        if state.depletion_mm > raw_mm:
            return offset
    return None


def _score(depletion_mm: float, taw_mm: float, raw_mm: float) -> float:
    """Depletion expressed as a 0-100 risk.

    Two segments meeting at RAW. Below it the crop is drawing on water it can take
    freely, so risk rises gently to the stress-onset score. Above it every further
    millimetre costs transpiration, so risk climbs steeply to 100 at wilting point.
    """
    if raw_mm > 0 and depletion_mm <= raw_mm:
        return STRESS_ONSET_SCORE * (depletion_mm / raw_mm)

    span = taw_mm - raw_mm
    if span <= 0:
        return 100.0
    beyond = (depletion_mm - raw_mm) / span
    return STRESS_ONSET_SCORE + (100.0 - STRESS_ONSET_SCORE) * max(0.0, min(1.0, beyond))


def _drivers(
    precipitation_mm: float,
    demand_mm: float,
    taw_mm: float,
    raw_mm: float,
    depletion_mm: float,
    efficiency: float | None,
    estimated_days: int,
) -> tuple[str, ...]:
    drivers = [
        f"{precipitation_mm:.0f} mm rainfall against {demand_mm:.0f} mm crop demand over "
        f"{HISTORY_DAYS} days of history and {FORECAST_DAYS} days of forecast",
        f"Root zone holds {taw_mm:.0f} mm, of which {raw_mm:.0f} mm is readily available",
        f"Current depletion is {depletion_mm:.0f} mm",
    ]
    if efficiency:
        drivers.append(
            f"Irrigation delivers about {efficiency * 100:.0f}% of what is applied, "
            "so the recommendation is grossed up accordingly"
        )
    else:
        drivers.append("Farm is rainfed; there is no irrigation buffer against a shortfall")
    if estimated_days:
        drivers.append(
            f"{estimated_days} day(s) had no measured evapotranspiration and were "
            "estimated from temperature range"
        )
    return tuple(drivers)


def _factors(
    depletion_mm: float,
    taw_mm: float,
    raw_mm: float,
    score: float,
    efficiency: float | None,
    parameters_source: str,
) -> tuple[ScoredFactor, ...]:
    source_note = {
        "crop": "",
        "category_default": " Coefficients are category defaults, not this crop's own.",
        "global_default": " Coefficients are generic defaults for an unrecognised crop.",
    }.get(parameters_source, "")

    return (
        factor(
            key="water_balance",
            label="Water balance",
            score=100.0 - score,
            weight=0.6,
            explanation=(
                f"{depletion_mm:.0f} mm depleted from a {taw_mm:.0f} mm root zone; "
                f"stress begins past {raw_mm:.0f} mm.{source_note}"
            ),
        ),
        factor(
            key="irrigation_capacity",
            label="Irrigation capacity",
            score=(efficiency or 0.0) * 100.0,
            weight=0.4,
            explanation=(
                f"Irrigation delivers about {efficiency * 100:.0f}% of applied water."
                if efficiency
                else "Rainfed: no irrigation available to close a shortfall."
            ),
        ),
    )


def _explanation(balance_mm: float, depletion_mm: float, raw_mm: float, score: float) -> str:
    state = "in water stress" if depletion_mm > raw_mm else "drawing on readily available water"
    return (
        f"Water balance is {balance_mm:.0f} mm "
        f"({'deficit' if balance_mm < 0 else 'surplus'}); the root zone is {state}, "
        f"placing water risk at {risk_level_for(score).value}."
    )
