"""Persisted analysis runs.

Every dashboard panel, risk card and advisory list is a projection of the most recent
run, so holding runs only in a process-local dict meant a restart emptied the whole
product until someone re-ran the analysis. Nothing failed; the panels simply went blank.

**The payload is JSON, with four fields promoted to columns.** The full validated
`AnalysisRun` is roughly twenty kilobytes of deeply nested sections that will grow again
when the AI layer lands, and normalising advisories and recommendations into child
tables would buy nothing while costing a migration every time a section gains a field.
What is promoted is exactly what is queried: `created_at` to find the latest,
`overall_health_score` and `overall_band` for sorting and trend charts, and
`inputs_hash` for cache lookup.

**`inputs_hash` is internal.** `AnalysisRun` is published in the frozen contract and has
no field for it, so it stays a column and never reaches a response. It exists to answer
one question — have we already computed this exact analysis — and the three version
columns beside it answer the follow-up: was it computed by the code running now.

Runs are immutable. Nothing updates a row after insert; a re-analysis writes a new one.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base, enum_values, utcnow
from app.schemas.common import ScoreBand
from app.schemas.enums import AIMode, AnalysisStatus


def _one_of(column: str, enum_cls: type) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{v}'" for v in enum_values(enum_cls)))


class AnalysisRunORM(Base):
    """One stored analysis."""

    __tablename__ = "analysis_runs"
    __table_args__ = (
        CheckConstraint(_one_of("status", AnalysisStatus), name="status_valid"),
        CheckConstraint(_one_of("overall_band", ScoreBand), name="overall_band_valid"),
        CheckConstraint(_one_of("ai_mode", AIMode), name="ai_mode_valid"),
        CheckConstraint(
            "overall_health_score >= 0 AND overall_health_score <= 100",
            name="overall_health_score_range",
        ),
        CheckConstraint("duration_ms >= 0", name="duration_ms_positive"),
        # Finding the newest run for a farm is the hottest read in the product: every
        # dashboard, risk panel and advisory list starts with it.
        Index("ix_analysis_runs_farm_created", "farm_id", "created_at"),
        # The cache lookup. Deliberately not unique: two concurrent requests may both
        # compute and insert the same hash, and since runs are immutable the duplicate
        # is harmless — whereas a unique constraint would turn a benign race into a 500.
        Index("ix_analysis_runs_farm_hash", "farm_id", "inputs_hash"),
    )

    id: Mapped[Uuid] = mapped_column(Uuid, primary_key=True)

    farm_id: Mapped[Uuid] = mapped_column(
        Uuid, ForeignKey("farms.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised from the farm. Every read is already ownership-scoped, and carrying
    # the owner here avoids a join on the hottest path. The cascade chain keeps it
    # consistent: deleting a user deletes their farms, which deletes these rows.
    #
    # Nullable for the same reason `farms.user_id` is: runs computed before ownership
    # existed, and the in-memory development path, have no owner.
    user_id: Mapped[Uuid | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False)
    overall_health_score: Mapped[int] = mapped_column(Integer, nullable=False)
    overall_band: Mapped[str] = mapped_column(String(20), nullable=False)
    ai_mode: Mapped[str] = mapped_column(String(20), nullable=False)

    # ---- reproducibility ----
    #
    # `inputs_hash` says whether the same question was asked; the three versions say
    # whether the same code would answer it. A cached run is reusable only when all
    # four match, which is why an engine change or a ruleset edit invalidates history
    # without anyone having to remember to clear a cache.
    # No standalone index: every lookup is scoped by farm, so the composite
    # (farm_id, inputs_hash) index below serves it and a second one would only
    # cost writes.
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(20), nullable=False)
    ruleset_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str | None] = mapped_column(String(80))

    # ---- payload ----
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    degraded_sources: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text)
