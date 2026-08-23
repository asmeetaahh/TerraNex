"""Crop and regenerative-agriculture recommendations.

Both are ranked deterministically first — crop suitability from soil/climate matching,
regenerative practices from soil-condition rules — and only then narrated by the AI.
The ordering a user sees is reproducible; only the prose varies.
"""

from pydantic import BaseModel, Field

from app.schemas.common import PaginatedResponse, ReasonCode, ScoredFactor
from app.schemas.enums import CropCategory, Season


class CropRecommendation(BaseModel):
    """One suggested crop, with its deterministic suitability score."""

    crop_code: str = Field(examples=["sorghum"])
    crop_name: str = Field(examples=["Sorghum"])
    category: CropCategory
    season: Season | None = None

    suitability_score: int = Field(
        ge=0, le=100, description="Deterministic match against soil and climate."
    )
    rank: int = Field(ge=1)
    is_current_crop: bool = Field(default=False, description="True if the farm already grows this.")

    water_requirement_mm: float | None = Field(default=None, ge=0)
    expected_yield_note: str | None = None
    planting_window: str | None = Field(default=None, examples=["Late March to mid April"])

    strengths: list[str] = Field(
        default_factory=list, examples=[["Tolerates the farm's 5.8 pH", "Low water demand"]]
    )
    considerations: list[str] = Field(default_factory=list)
    factors: list[ScoredFactor] = Field(default_factory=list)
    rationale: str = Field(description="Narrative over the computed suitability.")

    reasons: list[ReasonCode] = Field(
        default_factory=list,
        description=(
            "The numbers `strengths` and `considerations` were formatted from, as data — "
            "one per assessed component, so a client can state why this crop suits the "
            "site in any language. Empty for a component that could not be assessed; "
            "`factors` already reports which evidence was missing."
        ),
    )


class CropRecommendationList(PaginatedResponse[CropRecommendation]):
    """Response for `GET /api/v1/farms/{farm_id}/recommendations/crops`."""


class RegenerativeRecommendation(BaseModel):
    """One regenerative practice matched to this farm's soil and management."""

    practice_code: str = Field(examples=["cover_cropping"])
    practice_name: str = Field(examples=["Cover cropping"])
    rank: int = Field(ge=1)
    relevance_score: int = Field(
        ge=0, le=100, description="How well this practice addresses the farm's limitations."
    )

    description: str
    expected_benefits: list[str] = Field(
        default_factory=list,
        examples=[["Raises soil organic carbon", "Reduces erosion on sloped ground"]],
    )
    soil_carbon_impact: str | None = Field(
        default=None,
        description="Qualitative expected effect on soil organic carbon.",
        examples=["moderate increase over 3-5 seasons"],
    )
    water_retention_impact: str | None = None
    implementation_steps: list[str] = Field(default_factory=list)
    effort_level: str | None = Field(default=None, examples=["moderate"])
    time_to_benefit: str | None = Field(default=None, examples=["1-2 seasons"])
    considerations: list[str] = Field(default_factory=list)
    rationale: str


class RegenerativeRecommendationList(PaginatedResponse[RegenerativeRecommendation]):
    """Response for `GET /api/v1/farms/{farm_id}/recommendations/regenerative`."""
