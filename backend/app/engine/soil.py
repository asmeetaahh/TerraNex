"""Soil suitability for the planted crop.

Four questions, each answerable from a measurement the crop catalog or a soil survey
already holds:

* **Is the pH inside the range this crop tolerates?** Outside it, nutrients lock up long
  before anything visible happens to the plant.
* **Is the texture one this crop prefers?** A crop bred for free-draining loam does not
  thrive in heavy clay however well it is fed.
* **Can the profile hold enough water between rains?** The same figure the water balance
  uses for its reservoir, judged here as a property of the soil rather than of a season.
* **Is there enough organic carbon — for this texture?** A sand at 1.2% carbon is
  excellent; a clay at 1.2% is depleted. Judging both against one number calls half the
  world's soils poor for no reason, which is why the expectation here is texture-aware.

**Nothing is assumed.** The assessment this replaced read `soil.ph` unconditionally,
which was safe only because the value was always simulated. A real survey returns
partial profiles, so every property here may be absent — and an absent property is
reported as unassessed rather than scored as average.

Pure: no I/O, no clock, no randomness. Every value comes from the context.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.engine.context import AnalysisContext, SoilPoint
from app.engine.scoring import INSUFFICIENT, band_for, clamp, factor, unknown_factor
from app.schemas.common import ScoreBand, ScoredFactor

# --------------------------------------------------------------------------
# Expectations
# --------------------------------------------------------------------------

#: Organic carbon a healthy soil of each texture is expected to carry, in percent.
#:
#: Clays hold carbon that sands cannot: fine particles physically protect organic matter
#: from the microbes that would otherwise mineralise it. Scoring every texture against a
#: single figure marks sandy soils as degraded when they are merely sandy, and lets a
#: genuinely depleted clay pass. These are broad agronomic expectations, not survey data.
EXPECTED_ORGANIC_CARBON_PCT: dict[str, float] = {
    "sand": 0.8,
    "loamy_sand": 1.0,
    "sandy_loam": 1.2,
    "loam": 1.8,
    "silt_loam": 2.0,
    "silt": 2.0,
    "sandy_clay_loam": 1.6,
    "clay_loam": 2.2,
    "silty_clay_loam": 2.4,
    "sandy_clay": 1.8,
    "silty_clay": 2.6,
    "clay": 2.6,
}

#: Used when the texture is unknown, so carbon can still be judged rather than skipped.
DEFAULT_EXPECTED_ORGANIC_CARBON_PCT = 1.8

#: Score a soil earns when a property sits exactly where it should.
IDEAL_SCORE = 92.0

#: Score lost per pH unit outside the crop's tolerated range. A full unit is a large
#: agronomic distance — roughly the gap between "slightly acidic" and "nutrient lockup".
PH_PENALTY_PER_UNIT = 32.0

#: Floor for pH: a badly mismatched pH is a severe limitation, never a disqualification,
#: because it is the one property here a farmer can actually amend.
PH_FLOOR = 5.0

#: Score for a texture the crop does not list among its preferences. Not zero — an
#: unpreferred texture is a handicap, not a barrier, and many crops list only a few.
UNPREFERRED_TEXTURE_SCORE = 55.0

#: Awarded when the crop expresses no texture preference at all. Neutral rather than
#: perfect: an absent preference is not evidence of a good match.
NO_PREFERENCE_TEXTURE_SCORE = 75.0

#: Plant-available water, in millimetres over the sampled profile, at which the soil
#: stops being the limiting factor. Below this the crop depends on frequent rain.
AMPLE_AVAILABLE_WATER_MM = 60.0

#: Weights within the composite. pH leads because it gates nutrient availability for
#: every other input; carbon and texture follow as the slow structural properties.
WEIGHT_PH = 0.35
WEIGHT_ORGANIC_CARBON = 0.25
WEIGHT_TEXTURE = 0.25
WEIGHT_AVAILABLE_WATER = 0.15

#: Fallback pH window when the crop declares none. Deliberately wide: it is the range
#: within which most crops are unremarkable, not a claim about any one of them.
GENERIC_PH_MIN = 5.5
GENERIC_PH_MAX = 7.5


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SoilAssessmentResult:
    """Everything the soil assessment determined."""

    sufficient: bool
    score: float
    band: ScoreBand

    texture_class: str | None = None
    ph_status: str | None = None
    organic_matter_status: str | None = None
    fertility_status: str | None = None

    expected_organic_carbon_pct: float | None = None
    ph_window: tuple[float, float] | None = None
    ph_window_source: str = "generic"

    limitations: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    factors: tuple[ScoredFactor, ...] = field(default=())
    explanation: str = ""


# --------------------------------------------------------------------------
# Component scores
# --------------------------------------------------------------------------


def ph_window_for(ph_min: float | None, ph_max: float | None) -> tuple[tuple[float, float], str]:
    """The pH range this crop tolerates, and whether the crop actually declared it."""
    if ph_min is not None and ph_max is not None:
        return (ph_min, ph_max), "crop"
    return (GENERIC_PH_MIN, GENERIC_PH_MAX), "generic"


def score_ph(ph: float, window: tuple[float, float]) -> tuple[float, str]:
    """Suitability of a measured pH, and a word for where it sits.

    Inside the window there is nothing to penalise. Outside it the score falls with the
    distance to the nearer edge, so a soil half a unit too acidic is distinguished from
    one two units too acidic — which the status word alone cannot express.
    """
    low, high = window
    if low <= ph <= high:
        return IDEAL_SCORE, "optimal"

    distance = low - ph if ph < low else ph - high
    score = clamp(IDEAL_SCORE - distance * PH_PENALTY_PER_UNIT, PH_FLOOR, IDEAL_SCORE)
    return score, "too_acidic" if ph < low else "too_alkaline"


def expected_organic_carbon(texture_class: str | None) -> float:
    """Carbon a healthy soil of this texture should carry."""
    if texture_class is None:
        return DEFAULT_EXPECTED_ORGANIC_CARBON_PCT
    return EXPECTED_ORGANIC_CARBON_PCT.get(texture_class, DEFAULT_EXPECTED_ORGANIC_CARBON_PCT)


def score_organic_carbon(organic_carbon_pct: float, expected_pct: float) -> tuple[float, str]:
    """Carbon judged against what this texture can realistically hold.

    A soil at its texture's expectation scores well; one at half of it scores poorly.
    Exceeding the expectation is capped rather than rewarded without limit — carbon
    above what the texture protects is not evidence of a better soil.
    """
    if expected_pct <= 0:
        return IDEAL_SCORE, "adequate"

    ratio = organic_carbon_pct / expected_pct
    score = clamp(ratio * IDEAL_SCORE, 5.0, 100.0)

    if ratio < 0.55:
        status = "low"
    elif ratio < 1.15:
        status = "adequate"
    else:
        status = "high"
    return score, status


def score_texture(texture_class: str | None, preferred: Sequence[str]) -> tuple[float, str]:
    """How well the texture matches what the crop prefers."""
    if not preferred:
        return NO_PREFERENCE_TEXTURE_SCORE, "no_preference"
    if texture_class in preferred:
        return IDEAL_SCORE, "preferred"
    return UNPREFERRED_TEXTURE_SCORE, "not_preferred"


def score_available_water(water_holding_capacity_mm: float) -> tuple[float, str]:
    """How much of a buffer the profile gives the crop between rains."""
    score = clamp(water_holding_capacity_mm / AMPLE_AVAILABLE_WATER_MM * 100.0, 5.0, 100.0)
    if score < 45:
        status = "low"
    elif score < 80:
        status = "moderate"
    else:
        status = "high"
    return score, status


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def _insufficient(missing: Sequence[str]) -> SoilAssessmentResult:
    """No property could be assessed, so nothing is scored.

    The contract requires a numeric score and a band. Zero with a `critical` band would
    read as a terrible soil rather than an unmeasured one, so the explanation carries
    the meaning and every factor is emitted at weight zero — which removes them from
    the composite arithmetically.
    """
    listed = ", ".join(missing) if missing else "soil measurements"
    return SoilAssessmentResult(
        sufficient=False,
        score=0.0,
        band=ScoreBand.moderate,
        missing=tuple(missing),
        factors=(
            unknown_factor("soil_ph", "Soil pH", ["pH"]),
            unknown_factor("organic_carbon", "Organic carbon", ["organic carbon"]),
            unknown_factor("texture", "Texture match", ["texture"]),
        ),
        explanation=(
            f"{INSUFFICIENT}: {listed} unavailable, so soil suitability could not be assessed."
        ),
    )


def evaluate(context: AnalysisContext) -> SoilAssessmentResult:
    """Score this soil for this crop, using only the properties actually measured."""
    soil: SoilPoint = context.soil
    crop = context.crop

    window, window_source = ph_window_for(crop.ph_min, crop.ph_max)
    preferred = tuple(crop.preferred_textures)
    expected_soc = expected_organic_carbon(soil.texture_class)

    factors: list[ScoredFactor] = []
    limitations: list[str] = []
    missing: list[str] = []

    ph_status: str | None = None
    organic_status: str | None = None
    fertility_status: str | None = None

    # ---- pH ----
    if soil.ph is None:
        missing.append("pH")
        factors.append(unknown_factor("soil_ph", "Soil pH", ["pH"]))
    else:
        ph_score, ph_status = score_ph(soil.ph, window)
        against = "the crop's tolerated range" if window_source == "crop" else "a generic range"
        factors.append(
            factor(
                key="soil_ph",
                label="Soil pH",
                score=ph_score,
                weight=WEIGHT_PH,
                explanation=(
                    f"pH {soil.ph:.1f} against {against} of {window[0]:.1f}-{window[1]:.1f}."
                ),
            )
        )
        if ph_status != "optimal":
            limitations.append(
                f"pH {soil.ph:.1f} is outside the {window[0]:.1f}-{window[1]:.1f} range "
                f"preferred by this crop"
            )

    # ---- organic carbon ----
    if soil.organic_carbon_pct is None:
        missing.append("organic carbon")
        factors.append(unknown_factor("organic_carbon", "Organic carbon", ["organic carbon"]))
    else:
        soc_score, organic_status = score_organic_carbon(soil.organic_carbon_pct, expected_soc)
        texture_note = (
            f"a {soil.texture_class.replace('_', ' ')}"
            if soil.texture_class
            else "a soil of unknown texture"
        )
        factors.append(
            factor(
                key="organic_carbon",
                label="Organic carbon",
                score=soc_score,
                weight=WEIGHT_ORGANIC_CARBON,
                explanation=(
                    f"{soil.organic_carbon_pct:.2f}% against about {expected_soc:.1f}% "
                    f"expected for {texture_note}."
                ),
            )
        )
        if organic_status == "low":
            limitations.append(
                f"Organic carbon is low at {soil.organic_carbon_pct:.2f}%, against about "
                f"{expected_soc:.1f}% expected for this texture"
            )

    # ---- texture ----
    if soil.texture_class is None:
        missing.append("texture")
        factors.append(unknown_factor("texture", "Texture match", ["texture"]))
    else:
        texture_score, texture_status = score_texture(soil.texture_class, preferred)
        readable = soil.texture_class.replace("_", " ")
        factors.append(
            factor(
                key="texture",
                label="Texture match",
                score=texture_score,
                weight=WEIGHT_TEXTURE,
                explanation=(
                    f"{readable} texture; this crop lists no texture preference."
                    if texture_status == "no_preference"
                    else f"{readable} texture, which this crop prefers."
                    if texture_status == "preferred"
                    else f"{readable} texture, which this crop does not prefer."
                ),
            )
        )
        if texture_status == "not_preferred":
            limitations.append(f"{readable} texture is not preferred by this crop")

    # ---- available water ----
    if soil.water_holding_capacity_mm is not None:
        water_score, fertility_status = score_available_water(soil.water_holding_capacity_mm)
        factors.append(
            factor(
                key="available_water",
                label="Water retention",
                score=water_score,
                weight=WEIGHT_AVAILABLE_WATER,
                explanation=(
                    f"Holds about {soil.water_holding_capacity_mm:.0f} mm of "
                    "plant-available water over the sampled profile."
                ),
            )
        )
        if fertility_status == "low":
            limitations.append(
                f"Low water retention: about {soil.water_holding_capacity_mm:.0f} mm "
                "of plant-available water"
            )

    assessed = [f for f in factors if f.weight > 0]
    if not assessed:
        return _insufficient(missing)

    total_weight = sum(f.weight for f in assessed)
    composite = sum(f.score * f.weight for f in assessed) / total_weight

    return SoilAssessmentResult(
        sufficient=True,
        score=composite,
        band=band_for(composite),
        texture_class=soil.texture_class,
        ph_status=ph_status,
        organic_matter_status=organic_status,
        fertility_status=fertility_status,
        expected_organic_carbon_pct=expected_soc,
        ph_window=window,
        ph_window_source=window_source,
        limitations=tuple(limitations),
        missing=tuple(missing),
        factors=tuple(factors),
        explanation=_explanation(soil, composite, missing),
    )


def _explanation(soil: SoilPoint, composite: float, missing: Sequence[str]) -> str:
    described: list[str] = []
    if soil.texture_class:
        described.append(soil.texture_class.replace("_", " "))
    if soil.ph is not None:
        described.append(f"pH {soil.ph:.1f}")
    if soil.organic_carbon_pct is not None:
        described.append(f"{soil.organic_carbon_pct:.2f}% organic carbon")

    subject = ", ".join(described) if described else "the available measurements"
    sentence = f"Soil described as {subject} scores {band_for(composite).value} for this farm."

    if missing:
        # Stated after the score, so a partial assessment is never read as complete.
        sentence += (
            f" {INSUFFICIENT}: {', '.join(missing)} unavailable, so the score reflects "
            "only what was measured."
        )
    return sentence
