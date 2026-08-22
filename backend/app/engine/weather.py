"""Near-term weather threats, scored against the crop rather than against a constant.

Two ideas separate this from a generic weather warning:

**A threshold belongs to a crop, not to a thermometer.** Thirty-two degrees is a
hot day for wheat and an ordinary one for sorghum; five degrees is unremarkable for
barley and chilling injury for cassava. Every temperature threshold here derives from
the crop's own parameters, and falls back to a documented generic value only when there
is no crop to derive from.

**Heat is cumulative as well as acute.** Counting days above a threshold says nothing
about whether the crop is three weeks from harvest or three months. Growing degree days
answer that, and they are accumulated here from the crop's base temperature so the
drivers can say where in the season the exposure lands.

No calendar reasoning appears anywhere in this module. Season is never inferred from a
month, so a farm at 21 degrees south and one at 45 degrees north are scored by the same
physics from the same observations.

Pure: no I/O, no clock, no randomness. Every value comes from the context.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.engine.context import AnalysisContext, DailyPoint
from app.engine.scoring import INSUFFICIENT, clamp, factor, risk_level_for, unknown_factor
from app.schemas.common import RiskLevel, ScoredFactor

# --------------------------------------------------------------------------
# Thresholds
# --------------------------------------------------------------------------

#: Screen temperature at which ground frost becomes likely. Air measured at instrument
#: height sits above the ground surface, so frost forms on the crop before a
#: thermometer reads zero — which is why frost advisories are issued above freezing.
FROST_ADVISORY_C = 2.0

#: How far below its base temperature a crop suffers damage rather than merely pausing.
#:
#: Growth stops at the base temperature; injury begins lower. Tropical crops carry a
#: high base and are harmed well above freezing, temperate crops carry a low base and
#: tolerate frost. Tying the damage threshold to the base temperature captures that
#: ordering from data the catalog already holds. A modelling choice, not a measurement.
CHILLING_MARGIN_C = 8.0

#: Used only when no crop is planted, where there is no crop-relative threshold to
#: derive. Stated in the explanation whenever it applies, so a generic assessment is
#: never mistaken for a crop-specific one.
GENERIC_HEAT_C = 32.0

#: Physical hazards, independent of species: these damage a crop by force rather than
#: by physiology, so they are not crop-relative.
HEAVY_RAIN_MM = 25.0
HIGH_WIND_KMH = 40.0

#: A day at or below this is dry. Not zero: a trace reading is not rainfall.
DRY_DAY_MM = 0.2

FORECAST_WINDOW_DAYS = 7

#: Score contribution per exceedance day. Retained from the assessment this replaces so
#: that swapping the engine in does not move existing scores; what changed is where the
#: thresholds come from, not what an exceedance costs.
HEAT_WEIGHT = 9
FROST_WEIGHT = 14
HEAVY_RAIN_WEIGHT = 8
WIND_WEIGHT = 6
DRY_SPELL_WEIGHT = 3

#: A dry run shorter than this is ordinary weather rather than a driver worth naming.
DRY_SPELL_REPORTING_DAYS = 5


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WeatherAssessment:
    """Everything the weather assessment determined.

    Carries more than the published schema has fields for — accumulated degree days,
    the thresholds actually used, where they came from — so the service can put those
    into prose without the contract needing to grow.
    """

    score: float
    level: RiskLevel
    forecast_window_days: int = FORECAST_WINDOW_DAYS

    heat_stress_days: int = 0
    frost_risk_days: int = 0
    heavy_rain_days: int = 0
    high_wind_days: int = 0
    longest_dry_spell_days: int = 0

    max_temp_c: float | None = None
    min_temp_c: float | None = None
    total_precipitation_mm: float | None = None

    # Thresholds actually applied, and whether they came from the crop.
    heat_threshold_c: float | None = None
    cold_threshold_c: float | None = None
    thresholds_source: str = "generic"

    # Thermal time.
    growing_degree_days: float | None = None
    gdd_to_maturity: float | None = None
    maturity_fraction: float | None = None
    gdd_days_counted: int = 0

    missing: tuple[str, ...] = ()
    drivers: tuple[str, ...] = ()
    factors: tuple[ScoredFactor, ...] = field(default=())
    explanation: str = ""


# --------------------------------------------------------------------------
# Threshold derivation
# --------------------------------------------------------------------------


def heat_threshold_for(optimal_temp_max_c: float | None) -> tuple[float, str]:
    """The temperature above which this crop is heat-stressed."""
    if optimal_temp_max_c is not None:
        return optimal_temp_max_c, "crop"
    return GENERIC_HEAT_C, "generic"


def cold_threshold_for(base_temp_c: float | None) -> tuple[float, str]:
    """The temperature below which this crop takes cold damage.

    Never lower than the frost advisory: a crop with a base temperature of zero is
    still damaged by frost, and dropping below the advisory would report no cold risk
    for exactly the crops most likely to be caught by it.
    """
    if base_temp_c is None:
        return FROST_ADVISORY_C, "generic"
    return max(FROST_ADVISORY_C, base_temp_c - CHILLING_MARGIN_C), "crop"


# --------------------------------------------------------------------------
# Growing degree days
# --------------------------------------------------------------------------


def daily_growing_degrees(
    temp_min_c: float | None,
    temp_max_c: float | None,
    base_temp_c: float,
    upper_temp_c: float | None = None,
) -> float | None:
    """Thermal time accumulated in one day, or None when the day was not measured.

    The standard method with an upper cutoff: the maximum is capped before averaging,
    because a crop does not develop faster once past its optimum — it develops slower.
    Never negative. A cold day contributes nothing; it cannot take development away,
    and allowing it to would let a cold snap erase weeks of accumulated season.
    """
    if temp_min_c is None or temp_max_c is None:
        return None

    capped_max = min(temp_max_c, upper_temp_c) if upper_temp_c is not None else temp_max_c
    mean = (capped_max + temp_min_c) / 2.0
    return max(0.0, mean - base_temp_c)


def accumulate_growing_degrees(
    days: Sequence[DailyPoint],
    base_temp_c: float,
    upper_temp_c: float | None = None,
) -> tuple[float, int]:
    """Total thermal time over `days`, and how many days actually contributed."""
    total = 0.0
    counted = 0
    for day in days:
        degrees = daily_growing_degrees(
            reading(day, "temp_min_c"), reading(day, "temp_max_c"), base_temp_c, upper_temp_c
        )
        if degrees is None:
            continue
        total += degrees
        counted += 1
    return total, counted


# --------------------------------------------------------------------------
# Window statistics
# --------------------------------------------------------------------------


def reading(day: DailyPoint, attr: str) -> float | None:
    """One usable numeric reading from a day, or None.

    The adapter already coerces provider values, so this is the second line rather than
    the first — but a context can also be built by hand, and a comparison against a
    string raises exactly where a comparison against `None` would. Both mean unknown.
    `bool` is excluded because it subclasses `int`.
    """
    value = getattr(day, attr, None)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _count_above(days: Sequence[DailyPoint], attr: str, threshold: float) -> int:
    """Days exceeding `threshold`, skipping any that did not report the field."""
    return sum(1 for day in days if (v := reading(day, attr)) is not None and v > threshold)


def _count_below(days: Sequence[DailyPoint], attr: str, threshold: float) -> int:
    return sum(1 for day in days if (v := reading(day, attr)) is not None and v < threshold)


def _known(days: Sequence[DailyPoint], attr: str) -> list[float]:
    return [v for day in days if (v := reading(day, attr)) is not None]


def longest_dry_spell(days: Sequence[DailyPoint]) -> int:
    """The longest run of consecutive dry days.

    A day with no rainfall reading *breaks* the run rather than extending it. An
    unmeasured day is not evidence of dryness, and treating it as one would manufacture
    a drought out of a provider gap.
    """
    run = longest = 0
    for day in days:
        rain = reading(day, "precipitation_mm")
        if rain is None:
            run = 0
        elif rain <= DRY_DAY_MM:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return longest


#: Field names as they appear in an explanation.
FIELD_LABELS = {
    "temp_max_c": "maximum temperature",
    "temp_min_c": "minimum temperature",
    "precipitation_mm": "precipitation",
    "wind_kmh": "wind speed",
}


def _unavailable(days: Sequence[DailyPoint], *attrs: str) -> list[str]:
    """Labels of the requested fields that no day in the window reported."""
    return [FIELD_LABELS[a] for a in attrs if not _known(days, a)]


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def evaluate(context: AnalysisContext) -> WeatherAssessment:
    """Score near-term weather exposure for this crop at this place."""
    forecast = context.forecast(FORECAST_WINDOW_DAYS)
    crop = context.crop

    heat_threshold, heat_source = heat_threshold_for(crop.optimal_temp_max_c)
    cold_threshold, cold_source = cold_threshold_for(crop.base_temp_c)
    thresholds_source = "crop" if "crop" in {heat_source, cold_source} else "generic"

    heat_days = _count_above(forecast, "temp_max_c", heat_threshold)
    frost_days = _count_below(forecast, "temp_min_c", cold_threshold)
    heavy_rain_days = _count_above(forecast, "precipitation_mm", HEAVY_RAIN_MM)
    windy_days = _count_above(forecast, "wind_kmh", HIGH_WIND_KMH)
    dry_spell = longest_dry_spell(forecast)

    missing = _unavailable(forecast, "temp_max_c", "temp_min_c", "precipitation_mm", "wind_kmh")

    score = clamp(
        heat_days * HEAT_WEIGHT
        + frost_days * FROST_WEIGHT
        + heavy_rain_days * HEAVY_RAIN_WEIGHT
        + windy_days * WIND_WEIGHT
        + dry_spell * DRY_SPELL_WEIGHT,
        0,
        100,
    )

    gdd, gdd_counted, maturity_fraction = _thermal_time(context)

    highs = _known(forecast, "temp_max_c")
    lows = _known(forecast, "temp_min_c")
    rain = _known(forecast, "precipitation_mm")

    return WeatherAssessment(
        score=score,
        level=risk_level_for(score),
        heat_stress_days=heat_days,
        frost_risk_days=frost_days,
        heavy_rain_days=heavy_rain_days,
        high_wind_days=windy_days,
        longest_dry_spell_days=dry_spell,
        # Nullable in the contract: null is how "not measured" is expressed, and it is
        # what distinguishes "no hot days" from "no temperature data".
        max_temp_c=max(highs) if highs else None,
        min_temp_c=min(lows) if lows else None,
        total_precipitation_mm=sum(rain) if rain else None,
        heat_threshold_c=heat_threshold,
        cold_threshold_c=cold_threshold,
        thresholds_source=thresholds_source,
        growing_degree_days=gdd,
        gdd_to_maturity=crop.gdd_to_maturity,
        maturity_fraction=maturity_fraction,
        gdd_days_counted=gdd_counted,
        missing=tuple(missing),
        drivers=_drivers(
            forecast=forecast,
            heat_days=heat_days,
            heat_threshold=heat_threshold,
            heat_source=heat_source,
            frost_days=frost_days,
            cold_threshold=cold_threshold,
            heavy_rain_days=heavy_rain_days,
            windy_days=windy_days,
            dry_spell=dry_spell,
            missing=missing,
            gdd=gdd,
            maturity_fraction=maturity_fraction,
        ),
        factors=_factors(forecast, heat_days, heavy_rain_days, dry_spell, heat_source),
        explanation=_explanation(score, forecast, missing),
    )


def _thermal_time(context: AnalysisContext) -> tuple[float | None, int, float | None]:
    """Degree days accumulated since planting, and progress toward maturity.

    Accumulated over elapsed days only. Projecting thermal time across the forecast
    would report development the crop has not yet done.
    """
    crop = context.crop
    if crop.base_temp_c is None or context.planting_date is None:
        return None, 0, None

    elapsed = [day for day in context.daily if context.planting_date <= day.day <= context.today]
    if not elapsed:
        return None, 0, None

    total, counted = accumulate_growing_degrees(elapsed, crop.base_temp_c, crop.optimal_temp_max_c)
    if counted == 0:
        return None, 0, None

    fraction = None
    if crop.gdd_to_maturity:
        fraction = total / crop.gdd_to_maturity
    return total, counted, fraction


def _drivers(
    *,
    forecast: Sequence[DailyPoint],
    heat_days: int,
    heat_threshold: float,
    heat_source: str,
    frost_days: int,
    cold_threshold: float,
    heavy_rain_days: int,
    windy_days: int,
    dry_spell: int,
    missing: Sequence[str],
    gdd: float | None,
    maturity_fraction: float | None,
) -> tuple[str, ...]:
    drivers: list[str] = []

    if heat_days:
        qualifier = (
            "the crop's optimal maximum" if heat_source == "crop" else "a generic heat threshold"
        )
        drivers.append(f"{heat_days} forecast day(s) above {heat_threshold:.0f} °C ({qualifier})")
    if frost_days:
        drivers.append(f"{frost_days} forecast day(s) below {cold_threshold:.0f} °C")
    if heavy_rain_days:
        drivers.append(f"{heavy_rain_days} day(s) with more than {HEAVY_RAIN_MM:.0f} mm of rain")
    if windy_days:
        drivers.append(f"{windy_days} day(s) with winds above {HIGH_WIND_KMH:.0f} km/h")
    if dry_spell >= DRY_SPELL_REPORTING_DAYS:
        drivers.append(f"A {dry_spell}-day dry spell in the forecast window")

    if gdd is not None:
        if maturity_fraction is not None:
            drivers.append(
                f"{gdd:.0f} growing degree days accumulated since planting, "
                f"about {maturity_fraction * 100:.0f}% of the way to maturity"
            )
        else:
            drivers.append(f"{gdd:.0f} growing degree days accumulated since planting")

    if missing:
        # Stated before any all-clear, so a partial window is never read as calm.
        drivers.append(
            f"{INSUFFICIENT}: {', '.join(missing)} unavailable — the corresponding "
            "exposure was not assessed"
        )
    if not forecast:
        drivers.append(f"{INSUFFICIENT}: no daily forecast was available")
    elif not drivers:
        drivers.append("No threshold exceedances in the forecast window")

    return tuple(drivers)


def _factors(
    forecast: Sequence[DailyPoint],
    heat_days: int,
    heavy_rain_days: int,
    dry_spell: int,
    heat_source: str,
) -> tuple[ScoredFactor, ...]:
    factors: list[ScoredFactor] = []

    if _known(forecast, "temp_max_c"):
        against = (
            "the crop's optimal maximum"
            if heat_source == "crop"
            else "a generic threshold, as no crop is planted"
        )
        factors.append(
            factor(
                key="heat_stress",
                label="Heat stress",
                score=100 - heat_days * 18,
                weight=0.4,
                explanation=(f"{heat_days} of {len(forecast)} forecast days exceed {against}."),
            )
        )
    else:
        factors.append(unknown_factor("heat_stress", "Heat stress", ["maximum temperature"]))

    if _known(forecast, "precipitation_mm"):
        factors.append(
            factor(
                key="rainfall_extremes",
                label="Rainfall extremes",
                score=100 - heavy_rain_days * 22,
                weight=0.3,
                explanation=f"{heavy_rain_days} day(s) exceed {HEAVY_RAIN_MM:.0f} mm of rainfall.",
            )
        )
        factors.append(
            factor(
                key="dry_spell",
                label="Dry spell",
                score=100 - dry_spell * 12,
                weight=0.3,
                explanation=f"Longest run without measurable rain is {dry_spell} day(s).",
            )
        )
    else:
        factors.append(unknown_factor("rainfall_extremes", "Rainfall extremes", ["precipitation"]))
        factors.append(unknown_factor("dry_spell", "Dry spell", ["precipitation"]))

    return tuple(factors)


def _explanation(score: float, forecast: Sequence[DailyPoint], missing: Sequence[str]) -> str:
    if not forecast:
        return (
            f"{INSUFFICIENT}: no daily forecast was available, so weather risk could "
            "not be assessed."
        )
    if missing:
        return (
            f"Weather risk over the next {FORECAST_WINDOW_DAYS} days is "
            f"{risk_level_for(score).value} for the inputs that were available."
        )
    return (
        f"Conditions over the next {FORECAST_WINDOW_DAYS} days place weather risk at "
        f"{risk_level_for(score).value}."
    )
