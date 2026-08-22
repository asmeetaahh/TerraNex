"""Canopy vigour and crop development from the vegetation index series.

Two signals, weighted together. **Level** — how green the canopy is right now, against
what a healthy one reads. **Direction** — whether it is greening up or dying back over
the past month, which is the part a single reading cannot tell you. A crop at NDVI 0.55
is doing well if it was 0.40 a month ago and badly if it was 0.75.

Alongside those it reports the agronomic context the panel needs: days since planting,
days to expected harvest, and thermal time accumulated so far.

**The index here is simulated and stays simulated.** No keyless global source of real
NDVI exists, so `meta.mode` reports `simulated` on every response and the engine says
nothing to suggest otherwise. That is a declared capability, not a degradation — which
is why simulated vegetation is deliberately absent from `degraded_sources`.

**Nothing is assumed.** An empty series means no NDVI was available, not bare ground:
reporting 0.0 would claim a measurement nobody made. The assessment says so and the
score is excluded from the composite instead.

Pure: no I/O, no clock, no randomness. Every value comes from the context.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from app.engine.context import AnalysisContext, DailyPoint
from app.engine.scoring import INSUFFICIENT, band_for, clamp, factor, unknown_factor
from app.schemas.common import ScoreBand, ScoredFactor

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: NDVI a fully closed, healthy canopy reads. Vigour is scored as a fraction of it, so
#: a crop at this value or above scores 100. Above roughly this point the index
#: saturates and stops discriminating between a good canopy and a better one.
HEALTHY_NDVI_CEILING = 0.85

#: Below this the canopy is sparse enough to be worth flagging — thin stand, stress, or
#: a crop not yet established.
LOW_NDVI_THRESHOLD = 0.35

#: How far back the trend is measured.
BASELINE_LOOKBACK_DAYS = 30

#: Percent change either side of which the trend stops being flat. Narrower than this
#: is sampling noise in a simulated index rather than a real change in the field.
TREND_BAND_PCT = 5.0

#: Score a perfectly flat trend earns.
#:
#: Above the midpoint on purpose: a crop holding steady is doing what it should, so
#: "stable" is a mildly good outcome rather than a mediocre one. Improvement pushes it
#: toward 100, decline pulls it down point for point.
STABLE_TREND_SCORE = 65.0

WEIGHT_CANOPY_VIGOUR = 0.6
WEIGHT_VIGOUR_TREND = 0.4

#: Base temperature used when the crop declares none. Broadly right for warm-season
#: cereals and the value this calculation has always used.
DEFAULT_BASE_TEMP_C = 10.0

#: Baselines nearer zero than this cannot carry a percentage change — the denominator
#: vanishes. Direction is still known, so it is reported at full magnitude instead.
BASELINE_EPSILON = 1e-9


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VegetationAssessment:
    """Everything the vegetation assessment determined."""

    sufficient: bool
    score: float
    band: ScoreBand

    current_ndvi: float | None = None
    baseline_ndvi: float | None = None
    trend: str | None = None
    trend_pct: float = 0.0

    growth_stage: str | None = None
    days_since_planting: int | None = None
    days_to_expected_harvest: int | None = None
    growing_degree_days: float | None = None
    gdd_required: float | None = None

    stress_indicators: tuple[str, ...] = ()
    factors: tuple[ScoredFactor, ...] = field(default=())
    explanation: str = ""


# --------------------------------------------------------------------------
# Series reading
# --------------------------------------------------------------------------


def _numeric(value: object) -> float | None:
    """One usable measurement, or None. `bool` is excluded; it subclasses `int`."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def current_ndvi(series: Sequence[tuple[date, float]]) -> float | None:
    """The most recent reading, or None when the series is empty.

    Deliberately the last sample of the whole series rather than of a windowed slice,
    which is what keeps this identical to the value `GET /vegetation` reports.
    """
    if not series:
        return None
    return _numeric(sorted(series, key=lambda pair: pair[0])[-1][1])


def baseline_ndvi(
    series: Sequence[tuple[date, float]], today: date, lookback_days: int = BASELINE_LOOKBACK_DAYS
) -> float | None:
    """The reading the trend is measured against.

    The most recent sample at least `lookback_days` old. When the series does not reach
    back that far, its oldest sample is used instead — a shorter baseline is a weaker
    comparison, but it is still a real one, and refusing to report a trend for a young
    series would leave a newly planted field with no direction at all.
    """
    if not series:
        return None

    ordered = sorted(series, key=lambda pair: pair[0])
    cutoff = today - _days(lookback_days)

    for sample_date, value in reversed(ordered):
        if sample_date <= cutoff:
            return _numeric(value)
    return _numeric(ordered[0][1])


def _days(count: int):
    from datetime import timedelta

    return timedelta(days=count)


def trend_change_pct(now: float, then: float) -> float:
    """Percent change between two readings.

    A baseline of zero has no percentage to give — the denominator vanishes — but the
    direction is still unambiguous: a canopy that went from nothing to something has
    improved. Those cases report the full magnitude rather than being discarded, which
    is what the previous truthiness check did: it treated a legitimate NDVI of 0.0 as a
    missing baseline and silently dropped the trend.
    """
    if abs(then) < BASELINE_EPSILON:
        if abs(now - then) < BASELINE_EPSILON:
            return 0.0
        return 100.0 if now > then else -100.0
    return (now - then) / abs(then) * 100.0


def classify_trend(change_pct: float) -> str:
    if change_pct > TREND_BAND_PCT:
        return "improving"
    if change_pct < -TREND_BAND_PCT:
        return "declining"
    return "stable"


# --------------------------------------------------------------------------
# Component scores
# --------------------------------------------------------------------------


def score_canopy_vigour(ndvi: float) -> float:
    """How green the canopy is, against a healthy closed one."""
    return clamp(ndvi / HEALTHY_NDVI_CEILING * 100.0, 0.0, 100.0)


def score_vigour_trend(change_pct: float) -> float:
    """Direction of travel, centred on a flat trend."""
    return clamp(STABLE_TREND_SCORE + change_pct, 0.0, 100.0)


# --------------------------------------------------------------------------
# Development
# --------------------------------------------------------------------------


def accumulate_degree_days(
    daily: Sequence[DailyPoint], planted_on: date, today: date, base_temp_c: float
) -> float:
    """Thermal time since planting, from daily mean temperature.

    Accumulated over the overlap between the growing period and the observation window,
    so a crop planted before the window begins reports the degree days actually
    observed rather than an extrapolation over days nobody measured.

    Note this uses the daily *mean* with no upper cutoff, which is **not** the method
    `app/engine/weather.py` uses for its own degree-day figure. The two are known to
    disagree, and reconciling them would move a published field's value — so they are
    deliberately left unreconciled here and unified as a separate change.
    """
    total = 0.0
    for day in daily:
        if not planted_on <= day.day <= today:
            continue
        mean = _numeric(day.temp_mean_c)
        if mean is None:
            continue
        total += max(0.0, mean - base_temp_c)
    return round(total, 1)


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def evaluate(context: AnalysisContext) -> VegetationAssessment:
    """Score canopy vigour and report crop development."""
    series = list(context.vegetation)
    today = context.today
    crop = context.crop

    now = current_ndvi(series)
    then = baseline_ndvi(series, today)

    change = 0.0
    trend: str | None = None
    if now is not None and then is not None:
        change = trend_change_pct(now, then)
        trend = classify_trend(change)

    vigour_score = score_canopy_vigour(now) if now is not None else None
    trend_score = score_vigour_trend(change) if now is not None else None

    days_since_planting = None
    degree_days = None
    if context.planting_date is not None:
        days_since_planting = max(0, (today - context.planting_date).days)
        degree_days = accumulate_degree_days(
            context.daily,
            context.planting_date,
            today,
            crop.base_temp_c if crop.base_temp_c is not None else DEFAULT_BASE_TEMP_C,
        )

    days_to_harvest = None
    if context.expected_harvest_date is not None:
        # Negative when the crop is overdue, which is information rather than an error.
        days_to_harvest = (context.expected_harvest_date - today).days

    stress = _stress_indicators(now, change, crop.code is not None)

    if vigour_score is None or trend_score is None:
        # The contract requires a numeric score and a band. Zero with a `critical` band
        # would read as a dying crop rather than an unmeasured one, so the band stays
        # neutral and the explanation carries the truth.
        composite = 0.0
        band = ScoreBand.moderate
    else:
        composite = vigour_score * WEIGHT_CANOPY_VIGOUR + trend_score * WEIGHT_VIGOUR_TREND
        band = band_for(composite)

    return VegetationAssessment(
        sufficient=now is not None,
        score=composite,
        band=band,
        current_ndvi=now,
        baseline_ndvi=then,
        trend=trend,
        trend_pct=change,
        growth_stage=context.growth_stage,
        days_since_planting=days_since_planting,
        days_to_expected_harvest=days_to_harvest,
        growing_degree_days=degree_days,
        gdd_required=crop.gdd_to_maturity,
        stress_indicators=stress,
        factors=_factors(now, vigour_score, trend_score, trend, change),
        explanation=_explanation(now, trend, composite),
    )


def _stress_indicators(now: float | None, change: float, has_crop: bool) -> tuple[str, ...]:
    if now is None:
        return (f"{INSUFFICIENT}: no vegetation index was available for this farm",)

    indicators: list[str] = []
    if change < -TREND_BAND_PCT:
        indicators.append(
            f"NDVI declined {abs(change):.0f}% over the last {BASELINE_LOOKBACK_DAYS} days"
        )
    if now < LOW_NDVI_THRESHOLD:
        indicators.append(f"Canopy vigour is low (NDVI {now})")
    if not has_crop:
        indicators.append("No crop is registered for this farm, so vigour reflects bare ground")
    return tuple(indicators)


def _factors(
    now: float | None,
    vigour_score: float | None,
    trend_score: float | None,
    trend: str | None,
    change: float,
) -> tuple[ScoredFactor, ...]:
    if vigour_score is None:
        vigour = unknown_factor("canopy_vigour", "Canopy vigour", ["vegetation index"])
    else:
        vigour = factor(
            key="canopy_vigour",
            label="Canopy vigour",
            score=vigour_score,
            weight=WEIGHT_CANOPY_VIGOUR,
            explanation=f"NDVI of {now}.",
        )

    if trend_score is None:
        direction = unknown_factor("vigour_trend", "Vigour trend", ["vegetation index"])
    else:
        direction = factor(
            key="vigour_trend",
            label="Vigour trend",
            score=trend_score,
            weight=WEIGHT_VIGOUR_TREND,
            explanation=(f"NDVI is {trend} ({change:+.0f}% over {BASELINE_LOOKBACK_DAYS} days)."),
        )

    return (vigour, direction)


def _explanation(now: float | None, trend: str | None, composite: float) -> str:
    if now is None:
        return (
            f"{INSUFFICIENT}: no vegetation index was available, so crop health could "
            "not be assessed."
        )
    return (
        f"Canopy vigour reads NDVI {now} and is {trend}, giving "
        f"{band_for(composite).value} crop health."
    )
