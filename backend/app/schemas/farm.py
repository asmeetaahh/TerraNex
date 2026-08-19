"""Farm registration schemas.

Split-model convention used throughout the API: `FarmCreate` carries only
client-settable fields, `FarmUpdate` makes them all optional, and `Farm` is the full
server representation. A client can never set `id`, `user_id`, or the timestamps.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PaginatedResponse
from app.schemas.enums import FarmingPractice, IrrigationType


class FarmBase(BaseModel):
    """Fields a client may set on a farm."""

    name: str = Field(min_length=1, max_length=120, examples=["North Field"])
    latitude: float = Field(ge=-90, le=90, examples=[-1.2921])
    longitude: float = Field(ge=-180, le=180, examples=[36.8219])
    area_hectares: float | None = Field(default=None, gt=0, le=100_000, examples=[12.5])
    country_code: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="ISO 3166-1 alpha-2, uppercase.",
        examples=["KE"],
    )
    region: str | None = Field(default=None, max_length=120, examples=["Nairobi"])
    elevation_m: float | None = Field(default=None, ge=-500, le=9000)
    irrigation_type: IrrigationType = IrrigationType.rainfed
    farming_practice: FarmingPractice = FarmingPractice.conventional
    notes: str | None = Field(default=None, max_length=2000)


class FarmCreate(FarmBase):
    """Request body for `POST /api/v1/farms`."""


class FarmUpdate(BaseModel):
    """Request body for `PATCH /api/v1/farms/{farm_id}`. Omitted fields are unchanged."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    area_hectares: float | None = Field(default=None, gt=0, le=100_000)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    region: str | None = Field(default=None, max_length=120)
    elevation_m: float | None = Field(default=None, ge=-500, le=9000)
    irrigation_type: IrrigationType | None = None
    farming_practice: FarmingPractice | None = None
    notes: str | None = Field(default=None, max_length=2000)


class Farm(FarmBase):
    """Full server representation of a registered farm."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
    crop_count: int = Field(
        default=0, ge=0, description="Number of crops currently attached to this farm."
    )
    has_analysis: bool = Field(
        default=False,
        description="Whether an analysis run exists. Drives the dashboard empty state.",
    )


class FarmList(PaginatedResponse[Farm]):
    """Response for `GET /api/v1/farms`."""
