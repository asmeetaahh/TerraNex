"""Multimodal crop-image diagnosis.

Three fields exist specifically to keep the vision model honest:

* `is_plant_material` — guards against confidently diagnosing a photo that isn't a plant.
* `differential_diagnoses` — forces alternatives to be stated rather than a single
  overconfident answer.
* `disclaimer` — every diagnosis is AI-assisted, not an agronomist's verdict.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PaginatedResponse
from app.schemas.enums import AIMode, ImageAnalysisStatus, Severity


class DifferentialItem(BaseModel):
    """An alternative explanation the model considered."""

    condition: str = Field(examples=["early_blight"])
    condition_label: str = Field(examples=["Early blight"])
    likelihood: float = Field(ge=0, le=1)
    distinguishing_features: str | None = Field(
        default=None,
        description="What would confirm or rule this out on closer inspection.",
    )


class TreatmentOption(BaseModel):
    """One treatment path, with enough context to choose between them."""

    name: str = Field(examples=["Copper-based fungicide"])
    approach: str = Field(
        description="Broad category.", examples=["organic", "chemical", "cultural"]
    )
    description: str
    timing: str | None = Field(default=None, examples=["Apply at first sign, repeat in 7 days"])
    precautions: str | None = None


class CropImageAnalysis(BaseModel):
    """Structured diagnosis for a single crop image."""

    is_plant_material: bool = Field(
        description=(
            "False when the image does not show plant material. "
            "All other fields are then unreliable."
        )
    )
    crop_identified: str | None = Field(default=None, examples=["potato"])
    condition: str = Field(
        description="Machine key for the primary finding.",
        examples=["late_blight", "healthy"],
    )
    condition_label: str = Field(examples=["Late blight"])
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    affected_area_pct: float | None = Field(default=None, ge=0, le=100)

    symptoms_observed: list[str] = Field(default_factory=list)
    differential_diagnoses: list[DifferentialItem] = Field(
        default_factory=list, description="Alternatives considered, most likely first."
    )
    immediate_actions: list[str] = Field(default_factory=list)
    treatment_options: list[TreatmentOption] = Field(default_factory=list)
    prevention: list[str] = Field(default_factory=list)

    disclaimer: str = Field(
        description="Mandatory. AI-assisted diagnosis, not a substitute for an agronomist."
    )


class CropImage(BaseModel):
    """An uploaded crop image and its diagnosis, if one has run."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    farm_id: UUID
    farm_crop_id: UUID | None = None

    url: str | None = Field(default=None, description="Signed URL for display. May expire.")
    content_type: str = Field(examples=["image/jpeg"])
    size_bytes: int = Field(ge=0)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    note: str | None = Field(
        default=None, max_length=1000, description="Optional context supplied at upload."
    )

    analysis_status: ImageAnalysisStatus
    analysis: CropImageAnalysis | None = Field(
        default=None, description="Null until analysis completes."
    )
    analysis_error: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    ai_mode: AIMode | None = None

    uploaded_at: datetime
    analyzed_at: datetime | None = None


class CropImageList(PaginatedResponse[CropImage]):
    """Response for `GET /api/v1/farms/{farm_id}/crop-images`."""
