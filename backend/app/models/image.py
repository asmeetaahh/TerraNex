"""Persisted crop images and their diagnoses.

Images were held in a process-local dict, so a restart emptied every farm's diagnosis
history while its farms and analyses survived — an inconsistency, not just a loss.

**The diagnosis is JSON, with the queried fields promoted to columns.** The same
reasoning as `analysis_runs`: `CropImageAnalysis` is a nested payload that will grow
when the vision model lands, and normalising differentials and treatment options into
child tables would cost a migration per field and buy nothing, since nothing queries
into them. What is promoted is what is read — `uploaded_at` to order history,
`analysis_status` to render pending against complete.

**`sha256` is internal and load-bearing.** It seeds the deterministic diagnosis, so the
same photograph always yields the same result. It lived in a module-global dict beside
the store, which meant it was lost on restart and re-analysing an image silently fell
back to seeding from the image's id — a *different* diagnosis for the same photograph,
which is exactly the determinism the feature promises. As a column it survives.
`CropImage` is published in the frozen contract and has no field for it, so like
`inputs_hash` it never reaches a response.

**Rows are mutable, unlike analysis runs.** Analysing an image updates it in place
rather than inserting a second row: an image is one thing that acquires a diagnosis,
whereas a re-analysis of a farm is a new observation of a changed world.
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
from app.schemas.enums import AIMode, ImageAnalysisStatus


def _one_of(column: str, enum_cls: type) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{v}'" for v in enum_values(enum_cls)))


class CropImageORM(Base):
    """One uploaded crop photograph, with its diagnosis once analysed."""

    __tablename__ = "crop_images"
    __table_args__ = (
        CheckConstraint(
            _one_of("analysis_status", ImageAnalysisStatus), name="analysis_status_valid"
        ),
        # Nullable until the image is analysed, so the CHECK has to admit NULL —
        # a bare IN would reject it and every pending upload would fail to insert.
        CheckConstraint(
            f"ai_mode IS NULL OR {_one_of('ai_mode', AIMode)}",
            name="ai_mode_valid",
        ),
        CheckConstraint("size_bytes >= 0", name="size_bytes_positive"),
        CheckConstraint("width IS NULL OR width >= 0", name="width_positive"),
        CheckConstraint("height IS NULL OR height >= 0", name="height_positive"),
        # History is always read newest-first for one farm; this is the only query.
        Index("ix_crop_images_farm_uploaded", "farm_id", "uploaded_at"),
    )

    id: Mapped[Uuid] = mapped_column(Uuid, primary_key=True)

    farm_id: Mapped[Uuid] = mapped_column(
        Uuid, ForeignKey("farms.id", ondelete="CASCADE"), nullable=False
    )

    # SET NULL, not CASCADE. A diagnosis is evidence: deleting the planting it was
    # attached to should cost the photograph its crop link, not its existence. The
    # column is already nullable because attaching an image to a planting is optional.
    farm_crop_id: Mapped[Uuid | None] = mapped_column(
        Uuid, ForeignKey("farm_crops.id", ondelete="SET NULL"), nullable=True
    )

    # Denormalised from the farm, as on `analysis_runs`: every read is already
    # ownership-scoped and carrying the owner here avoids a join. Nullable for the same
    # reason — the in-memory path and anything written before ownership have no owner.
    user_id: Mapped[Uuid | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )

    # ---- the file, as measured. The bytes themselves are not stored: there is no
    # object storage in this phase, which is why `url` stays null in the response. ----
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)

    #: Hex SHA-256 of the uploaded bytes. Internal; seeds the diagnosis.
    #:
    #: Not unique, and deliberately not indexed. The same photograph may legitimately be
    #: uploaded twice — to a second farm, or to record a second observation of the same
    #: leaf — and nothing looks an image up by its digest.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    note: Mapped[str | None] = mapped_column(Text)

    # ---- diagnosis ----
    analysis_status: Mapped[str] = mapped_column(String(20), nullable=False)
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    analysis_error: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(80))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    ai_mode: Mapped[str | None] = mapped_column(String(20))

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
