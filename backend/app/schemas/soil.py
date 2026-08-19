"""Soil payloads.

:class:`SoilProfile` is measured/estimated properties. :class:`SoilAssessment` is the
risk engine's interpretation of those properties against a specific crop — kept
separate so raw data and derived judgement are never confused.
"""

from pydantic import BaseModel, Field

from app.schemas.common import DataSourceMeta, ScoreBand, ScoredFactor
from app.schemas.enums import SoilTexture


class SoilProfile(BaseModel):
    """Physical and chemical soil properties at the farm location.

    Response for `GET /api/v1/farms/{farm_id}/soil`.
    """

    farm_id: str
    depth_cm: str = Field(
        default="0-30",
        description="Depth interval these values describe.",
        examples=["0-30"],
    )

    ph: float | None = Field(default=None, ge=0, le=14, examples=[6.2])
    organic_carbon_pct: float | None = Field(default=None, ge=0, le=100)
    nitrogen_g_kg: float | None = Field(default=None, ge=0)
    cec_cmol_kg: float | None = Field(
        default=None, ge=0, description="Cation exchange capacity — nutrient-holding ability."
    )
    bulk_density_kg_dm3: float | None = Field(default=None, ge=0)

    sand_pct: float | None = Field(default=None, ge=0, le=100)
    silt_pct: float | None = Field(default=None, ge=0, le=100)
    clay_pct: float | None = Field(default=None, ge=0, le=100)
    texture_class: SoilTexture | None = None

    water_holding_capacity_mm: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Plant-available water capacity, derived from texture. Feeds the water balance."
        ),
    )

    meta: DataSourceMeta


class SoilAssessment(BaseModel):
    """The risk engine's read on whether this soil suits the farm's crop."""

    score: int = Field(ge=0, le=100, description="Deterministic suitability score.")
    band: ScoreBand
    texture_class: SoilTexture | None = None
    ph_status: str | None = Field(
        default=None,
        description="pH judged against the crop's tolerated range.",
        examples=["optimal", "slightly_acidic", "too_alkaline"],
    )
    organic_matter_status: str | None = Field(default=None, examples=["low", "adequate"])
    fertility_status: str | None = Field(default=None, examples=["moderate"])
    limitations: list[str] = Field(
        default_factory=list,
        description="Concrete constraints found, e.g. 'pH 5.1 is below maize tolerance'.",
    )
    factors: list[ScoredFactor] = Field(default_factory=list)
    explanation: str = Field(
        description="Narrative over the computed values. Never introduces new numbers."
    )
