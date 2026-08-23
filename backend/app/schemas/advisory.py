"""AI agricultural advisories.

An advisory is the actionable output of an analysis run: what to do, when, and why.
`rationale` must cite the computed drivers that produced it, so every recommendation
traces back to a number the risk engine calculated.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PaginatedResponse, ReasonCode
from app.schemas.enums import AdvisoryCategory, AdvisoryPriority


class Advisory(BaseModel):
    """One prioritized, actionable recommendation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    farm_id: UUID
    analysis_run_id: UUID | None = None

    category: AdvisoryCategory
    priority: AdvisoryPriority
    title: str = Field(max_length=200, examples=["Irrigate within 48 hours"])
    body: str = Field(description="What to do, in plain language.")
    rationale: str = Field(
        description="Why — cites the computed drivers behind this advisory.",
        examples=["Water balance is −34 mm over 30 days and no rain is forecast for 6 days."],
    )
    action_window: str | None = Field(
        default=None, description="When to act.", examples=["within 48 hours"]
    )
    confidence: float = Field(ge=0, le=1)

    reasons: list[ReasonCode] = Field(
        default_factory=list,
        description=(
            "The numbers `title`, `body` and `rationale` were formatted from, as data — "
            "so a client can restate this advisory in any language. Present only where "
            "the evidence reaches no other field: irrigation depth and the heat and cold "
            "thresholds. A disease advisory's evidence is on "
            "`disease_risk.risks[].reasons`, and a soil advisory's on `soil_assessment`, "
            "so neither is repeated here."
        ),
    )

    created_at: datetime
    dismissed_at: datetime | None = None


class AdvisoryList(PaginatedResponse[Advisory]):
    """Response for `GET /api/v1/farms/{farm_id}/advisories`."""
