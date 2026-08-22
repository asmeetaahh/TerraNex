"""Shared scoring primitives.

Every risk module converts a raw 0-100 number into a band, a level, or a
:class:`ScoredFactor` through these helpers, so the same score never lands in two
different bands depending on which module produced it.

The thresholds are **promoted verbatim** from `app.services.analysis_service`, where
they were private. Step 2 of Phase 4 points that module here and deletes its copies;
until then both exist and must agree, which `tests/unit/engine/test_scoring.py`
asserts directly against the originals.
"""

from collections.abc import Sequence

from app.schemas.common import RiskLevel, ScoreBand, ScoredFactor

INSUFFICIENT = "Insufficient data"
"""Prefix every unavailable-factor explanation carries, so a caller can detect one.

Kept as a literal rather than an enum member because it is a *narrative* marker: it
appears inside prose the AI layer will later rewrite, and the contract has no field
for it.
"""


def clamp(value: float, low: float, high: float) -> float:
    """Constrain `value` to `[low, high]`."""
    return max(low, min(high, value))


def band_for(score: float) -> ScoreBand:
    """Qualitative band for a 0-100 score.

    Higher is better — this is a *health* scale, so 90 is `excellent`.
    """
    if score >= 80:
        return ScoreBand.excellent
    if score >= 65:
        return ScoreBand.good
    if score >= 45:
        return ScoreBand.moderate
    if score >= 25:
        return ScoreBand.poor
    return ScoreBand.critical


def risk_level_for(score: float) -> RiskLevel:
    """Qualitative severity for a 0-100 score.

    Higher is worse — this is a *risk* scale, so 90 is `severe`. The inversion against
    :func:`band_for` is deliberate and is why the two are separate functions: a risk
    module reports severity, a health module reports quality, and reusing one for the
    other silently flips the meaning of every number.
    """
    if score >= 70:
        return RiskLevel.severe
    if score >= 50:
        return RiskLevel.high
    if score >= 28:
        return RiskLevel.moderate
    return RiskLevel.low


def factor(
    key: str,
    label: str,
    score: float,
    weight: float,
    explanation: str,
) -> ScoredFactor:
    """A weighted contributor to a composite, with its band derived from its score.

    `score` is clamped rather than validated, because a sub-calculation that overshoots
    slightly should not raise in the middle of an analysis run.
    """
    bounded = clamp(score, 0.0, 100.0)
    return ScoredFactor(
        key=key,
        label=label,
        score=bounded,
        weight=clamp(weight, 0.0, 1.0),
        band=band_for(bounded),
        explanation=explanation,
    )


def unknown_factor(key: str, label: str, missing: Sequence[str]) -> ScoredFactor:
    """A factor that could not be assessed.

    The contract requires a numeric score and a band, and offers no "unknown" member —
    so `weight=0.0` carries the meaning instead. It excludes the factor from the
    weighted composite *arithmetically* rather than by convention, and the explanation
    states plainly what was missing.

    This is the mechanism that stops a farm being scored badly for the sin of having
    no soil data: the weight goes to zero and the composite renormalises over what is
    actually known.
    """
    return ScoredFactor(
        key=key,
        label=label,
        # Neutralised placeholders: the contract requires both, and weight=0.0 keeps
        # them out of every derived number.
        score=0.0,
        weight=0.0,
        band=ScoreBand.moderate,
        explanation=(
            f"{INSUFFICIENT}: {', '.join(missing)} unavailable, so this factor was not "
            "assessed and is excluded from the overall score."
        ),
    )


def is_unknown(candidate: ScoredFactor) -> bool:
    """Whether `candidate` came from :func:`unknown_factor`.

    Composite scoring needs to tell "scored zero" from "not assessed", and the contract
    gives it only `weight` to go on.
    """
    return candidate.weight == 0.0 and candidate.explanation.startswith(INSUFFICIENT)
