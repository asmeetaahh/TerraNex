"""Vegetation indices and crop health.

The vegetation provider is an abstraction from day one: a mock implementation ships
first and a real satellite source can replace it without any contract change. Which
one produced a payload is always visible in `meta.mode`.
"""

from datetime import date

from pydantic import BaseModel, Field

from app.schemas.common import DataSourceMeta, ScoreBand, ScoredFactor


class VegetationPoint(BaseModel):
    """One observation in a vegetation index time series."""

    date: date
    ndvi: float | None = Field(
        default=None,
        ge=-1,
        le=1,
        description="Normalized Difference Vegetation Index. Healthy canopy ≈ 0.6-0.9.",
    )
    evi: float | None = Field(default=None, ge=-1, le=1, description="Enhanced Vegetation Index.")
    cloud_cover_pct: float | None = Field(
        default=None, ge=0, le=100, description="Higher values mean lower confidence."
    )


class VegetationSeries(BaseModel):
    """Response for `GET /api/v1/farms/{farm_id}/vegetation`."""

    farm_id: str
    series: list[VegetationPoint] = Field(default_factory=list)
    current_ndvi: float | None = Field(default=None, ge=-1, le=1)
    mean_ndvi: float | None = Field(default=None, ge=-1, le=1)
    trend: str | None = Field(
        default=None,
        description="Direction over the window.",
        examples=["improving", "stable", "declining"],
    )
    trend_pct: float | None = Field(
        default=None, description="Percent change from the start of the window."
    )
    meta: DataSourceMeta


class CropHealth(BaseModel):
    """Composite crop health, combining vegetation vigour with agronomic context.

    Response for `GET /api/v1/farms/{farm_id}/health`.
    """

    score: int = Field(ge=0, le=100)
    band: ScoreBand
    current_ndvi: float | None = Field(default=None, ge=-1, le=1)
    ndvi_trend: str | None = None
    growth_stage: str | None = None
    days_since_planting: int | None = Field(default=None, ge=0)
    days_to_expected_harvest: int | None = None
    gdd_accumulated: float | None = Field(
        default=None, ge=0, description="Growing degree days accumulated since planting."
    )
    gdd_required: float | None = Field(default=None, ge=0)
    stress_indicators: list[str] = Field(
        default_factory=list,
        description="Detected stress signals, e.g. 'NDVI declined 12% over 14 days'.",
    )
    factors: list[ScoredFactor] = Field(default_factory=list)
    explanation: str
