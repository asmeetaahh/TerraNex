"""Primitives shared across every TerraNex payload.

Two of these carry product rules rather than just structure:

* :class:`DataMode` — no payload may present generated values as real observations.
  The mode is part of the schema, so honesty is enforced by the contract rather
  than by discipline.
* :class:`ScoredFactor` — every composite score must be decomposable, so the UI can
  explain *why* a farm scored what it scored instead of showing an opaque number.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DataMode(StrEnum):
    """Provenance of a data payload. Never omit or guess this."""

    live = "live"
    """Fetched from a real external provider during this request."""

    cached = "cached"
    """Real provider data, served from cache within its TTL."""

    simulated = "simulated"
    """Generated locally. NOT a real observation. The UI must label it as such."""

    unavailable = "unavailable"
    """The provider failed. Values are estimates or null; see `note`."""


class DataSourceMeta(BaseModel):
    """Provenance attached to every externally-sourced payload."""

    source: str = Field(
        description="Provider identifier, or 'simulated' when locally generated.",
        examples=["open-meteo", "soilgrids", "simulated"],
    )
    mode: DataMode = Field(
        description=(
            "Whether these values are real observations. `simulated` means the data "
            "was generated locally and must be labelled as such in the UI."
        )
    )
    fetched_at: datetime = Field(description="When these values were produced or retrieved.")
    note: str | None = Field(
        default=None,
        description="Human-readable qualifier, e.g. why a provider was degraded.",
        examples=["SoilGrids timed out; texture-class defaults substituted."],
    )

    @property
    def is_real(self) -> bool:
        """True only for genuine provider observations."""
        return self.mode in (DataMode.live, DataMode.cached)


class ScoreBand(StrEnum):
    """Qualitative band for a 0-100 score."""

    excellent = "excellent"
    good = "good"
    moderate = "moderate"
    poor = "poor"
    critical = "critical"


class RiskLevel(StrEnum):
    """Qualitative severity for a risk assessment."""

    low = "low"
    moderate = "moderate"
    high = "high"
    severe = "severe"


class ScoredFactor(BaseModel):
    """One weighted contributor to a composite score.

    Emitting these alongside every composite is what makes the health score
    explainable: the UI renders the breakdown, not just the total.
    """

    key: str = Field(description="Stable machine key.", examples=["soil_ph"])
    label: str = Field(description="Display name.", examples=["Soil pH"])
    score: float = Field(ge=0, le=100, description="This factor's own 0-100 score.")
    weight: float = Field(ge=0, le=1, description="Its weight in the composite.")
    band: ScoreBand
    explanation: str = Field(description="Why this factor scored as it did.")


class PaginatedResponse[T](BaseModel):
    """The single collection shape used by every list endpoint.

    Endpoints without real pagination still use it (page=1, has_next=false) so the
    frontend learns one collection shape instead of two.
    """

    model_config = ConfigDict(populate_by_name=True)

    items: list[T]
    total: int = Field(ge=0, description="Total items matching the query.")
    page: int = Field(ge=1, default=1)
    page_size: int = Field(ge=1, default=50)
    has_next: bool = False


class PageParams(BaseModel):
    """Shared query parameters for paginated endpoints."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Coordinates(BaseModel):
    """WGS-84 point."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
