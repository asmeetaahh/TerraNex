"""Crop catalog (reference data) and per-farm crop plantings.

Two distinct concepts, deliberately separated:

* :class:`Crop` — an entry in the seeded global catalog (agronomic constants for
  "maize"). Read-only reference data shared by every farm.
* :class:`FarmCrop` — *this* farm growing *that* crop on *these* dates.
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import PaginatedResponse
from app.schemas.enums import CropCategory, CropStatus, DroughtTolerance, GrowthStage, Season


class Crop(BaseModel):
    """A crop in the reference catalog, with the agronomic constants the risk
    engine needs. Seeded from a fixture; never client-writable."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str = Field(description="Stable machine key.", examples=["maize"])
    name: str = Field(examples=["Maize"])
    scientific_name: str | None = Field(default=None, examples=["Zea mays"])
    category: CropCategory
    season: Season

    base_temp_c: float | None = Field(
        default=None, description="Base temperature for growing-degree-day accumulation."
    )
    optimal_temp_min_c: float | None = None
    optimal_temp_max_c: float | None = None
    gdd_to_maturity: float | None = Field(
        default=None, description="Growing degree days from planting to maturity."
    )
    water_need_mm_season: float | None = Field(
        default=None, description="Typical seasonal water requirement in mm."
    )
    ph_min: float | None = Field(default=None, ge=0, le=14)
    ph_max: float | None = Field(default=None, ge=0, le=14)
    preferred_textures: list[str] = Field(default_factory=list)
    drought_tolerance: DroughtTolerance | None = None
    common_diseases: list[str] = Field(default_factory=list)


class CropList(PaginatedResponse[Crop]):
    """Response for `GET /api/v1/reference/crops`."""


class FarmCropBase(BaseModel):
    """Fields a client may set on a farm's planting."""

    crop_id: UUID = Field(description="References a crop in the reference catalog.")
    planting_date: date | None = None
    expected_harvest_date: date | None = None
    growth_stage: GrowthStage = GrowthStage.not_planted
    area_hectares: float | None = Field(default=None, gt=0, le=100_000)
    is_primary: bool = Field(
        default=False,
        description="The crop this farm's analysis is centred on. At most one per farm.",
    )
    status: CropStatus = CropStatus.planned
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _harvest_after_planting(self) -> "FarmCropBase":
        if (
            self.planting_date is not None
            and self.expected_harvest_date is not None
            and self.expected_harvest_date < self.planting_date
        ):
            raise ValueError("expected_harvest_date must be on or after planting_date")
        return self


class FarmCropCreate(FarmCropBase):
    """Request body for `POST /api/v1/farms/{farm_id}/crops`."""


class FarmCropUpdate(BaseModel):
    """Request body for `PATCH /api/v1/farms/{farm_id}/crops/{farm_crop_id}`."""

    crop_id: UUID | None = None
    planting_date: date | None = None
    expected_harvest_date: date | None = None
    growth_stage: GrowthStage | None = None
    area_hectares: float | None = Field(default=None, gt=0, le=100_000)
    is_primary: bool | None = None
    status: CropStatus | None = None
    notes: str | None = Field(default=None, max_length=1000)


class FarmCrop(FarmCropBase):
    """A crop planted on a specific farm, with the catalog entry embedded so the
    frontend renders a card without a second request."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    farm_id: UUID
    crop: Crop
    created_at: datetime
    updated_at: datetime


class FarmCropList(PaginatedResponse[FarmCrop]):
    """Response for `GET /api/v1/farms/{farm_id}/crops`."""
