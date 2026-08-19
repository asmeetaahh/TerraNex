"""Analysis runs and the unified farm dashboard.

:class:`AnalysisRun` is the product's central artifact. `POST /api/v1/farms/{id}/analysis`
is the only endpoint that performs farm reasoning: it gathers provider data, runs the
deterministic risk engine, makes one AI call, validates the result, and persists it
immutably. Every other analysis endpoint is a cheap read of a stored run — no
recompute, no additional AI cost.

Each run records `model`, `prompt_version`, and `ai_mode`, so any stored output can be
traced to exactly what produced it.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.advisory import Advisory
from app.schemas.common import DataSourceMeta, PaginatedResponse, ScoreBand, ScoredFactor
from app.schemas.crop import FarmCrop
from app.schemas.enums import AIMode, AnalysisStatus
from app.schemas.farm import Farm
from app.schemas.image import CropImage
from app.schemas.recommendation import CropRecommendation, RegenerativeRecommendation
from app.schemas.risk import DiseaseRisk, WaterRisk, WeatherRisk
from app.schemas.soil import SoilAssessment
from app.schemas.vegetation import CropHealth
from app.schemas.weather import WeatherCurrent


class AnalysisRun(BaseModel):
    """A complete, immutable farm analysis."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    farm_id: UUID
    status: AnalysisStatus
    created_at: datetime
    duration_ms: int = Field(ge=0)

    # ---- Provenance ----
    model: str | None = Field(
        default=None,
        description="AI model identifier, or null when no model was used.",
        examples=["gemini-2.5-flash"],
    )
    prompt_version: str = Field(
        description="Version of the prompt set that produced the narrative fields.",
        examples=["v1"],
    )
    ai_mode: AIMode = Field(
        description=(
            "Which engine produced the narrative: real model, mock, or deterministic fallback."
        )
    )
    degraded_sources: list[str] = Field(
        default_factory=list,
        description="Providers that failed or degraded during this run.",
    )

    # ---- Composite ----
    overall_health_score: int = Field(
        ge=0, le=100, description="Deterministic weighted composite. Not AI-generated."
    )
    overall_band: ScoreBand
    summary: str = Field(description="Two to three sentence headline for the dashboard.")
    factors: list[ScoredFactor] = Field(
        default_factory=list,
        description="Per-factor contributions to the composite, for an explainable UI.",
    )

    # ---- Sections ----
    weather_risk: WeatherRisk
    water_risk: WaterRisk
    disease_risk: DiseaseRisk
    crop_health: CropHealth
    soil_assessment: SoilAssessment
    advisories: list[Advisory] = Field(default_factory=list)
    crop_recommendations: list[CropRecommendation] = Field(default_factory=list)
    regenerative_recommendations: list[RegenerativeRecommendation] = Field(default_factory=list)

    sources: list[DataSourceMeta] = Field(
        default_factory=list, description="Provenance for every input used."
    )


class AnalysisRunSummary(BaseModel):
    """Lightweight run record for history lists and trend charts."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    farm_id: UUID
    status: AnalysisStatus
    created_at: datetime
    duration_ms: int = Field(ge=0)
    overall_health_score: int = Field(ge=0, le=100)
    overall_band: ScoreBand
    ai_mode: AIMode
    degraded_sources: list[str] = Field(default_factory=list)


class AnalysisRunList(PaginatedResponse[AnalysisRunSummary]):
    """Response for `GET /api/v1/farms/{farm_id}/analysis`."""


class FarmDashboard(BaseModel):
    """Everything the unified dashboard needs, in one request.

    Response for `GET /api/v1/farms/{farm_id}/dashboard`.

    This endpoint does not 404 when no analysis exists — it returns
    `has_analysis: false` and `analysis: null` so the frontend renders a
    "Run analysis" empty state rather than handling an error.
    """

    farm: Farm
    crops: list[FarmCrop] = Field(default_factory=list)
    has_analysis: bool
    analysis: AnalysisRun | None = None
    current_weather: WeatherCurrent | None = None
    recent_images: list[CropImage] = Field(default_factory=list)
    data_freshness: list[DataSourceMeta] = Field(
        default_factory=list,
        description="Provenance of every panel, so the UI can badge simulated or stale data.",
    )
