"""Database-backed reads and writes for crop images.

Mirrors `analysis_repo`: takes and returns validated Pydantic models, never ORM objects,
so a detached instance can never reach the service layer.

**Ownership is resolved by the caller, not here**, exactly as for analysis runs. `get`
returns an image by its own id and the service resolves the owner through the image's
farm, so a row whose `user_id` is null — written on the in-memory path, or before
ownership existed — stays reachable by the farm's owner rather than being hidden from
the one person entitled to it.

**The digest travels beside the image, not inside it.** `CropImage` is published in the
frozen contract and has no `sha256` field, so the functions here take and return it
separately. It is what makes a diagnosis reproducible, so losing it is not cosmetic.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.db.session import session_scope
from app.models import CropImageORM
from app.schemas.image import CropImage


def _utc(value: datetime | None) -> datetime | None:
    """Re-attach UTC to a timestamp that lost it in storage.

    `DateTime(timezone=True)` is honoured by Postgres and **ignored by SQLite**, which
    has no timestamp type and hands back a naive datetime. Pydantic then serialises it
    without the trailing `Z`, so the same image would report
    `2026-08-22T19:08:25.666826` on SQLite and `…Z` on Postgres — a contract violation
    on one of the two supported configurations.

    The values are written as UTC (`utcnow`), so attaching UTC to a naive one restores
    what was stored rather than guessing at it.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _as_image(row: CropImageORM) -> CropImage:
    """Rebuild the published model from the stored row.

    `url` is null rather than read from the row: there is no object storage in this
    phase, so there is no honest URL to hand back and the column does not exist.

    The nested analysis is validated rather than trusted — in the general case the row
    was written by an older version of this code, and a payload that no longer satisfies
    the schema should fail loudly here rather than reach a response half-formed.
    """
    return CropImage(
        id=row.id,
        farm_id=row.farm_id,
        farm_crop_id=row.farm_crop_id,
        url=None,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        width=row.width,
        height=row.height,
        note=row.note,
        analysis_status=row.analysis_status,
        analysis=row.analysis,
        analysis_error=row.analysis_error,
        model=row.model,
        prompt_version=row.prompt_version,
        ai_mode=row.ai_mode,
        uploaded_at=_utc(row.uploaded_at),
        analyzed_at=_utc(row.analyzed_at),
    )


def insert_image(image: CropImage, *, sha256: str, user_id: UUID | None = None) -> None:
    """Store one uploaded image, still awaiting diagnosis."""
    with session_scope() as db:
        db.add(
            CropImageORM(
                id=image.id,
                farm_id=image.farm_id,
                farm_crop_id=image.farm_crop_id,
                user_id=user_id,
                content_type=image.content_type,
                size_bytes=image.size_bytes,
                width=image.width,
                height=image.height,
                sha256=sha256,
                note=image.note,
                analysis_status=str(image.analysis_status),
                # `mode="json"` so enums and datetimes inside the diagnosis serialise to
                # the primitives the JSON column holds, and the payload round-trips
                # through `model_validate` unchanged.
                analysis=(
                    image.analysis.model_dump(mode="json") if image.analysis is not None else None
                ),
                analysis_error=image.analysis_error,
                model=image.model,
                prompt_version=image.prompt_version,
                ai_mode=str(image.ai_mode) if image.ai_mode is not None else None,
                uploaded_at=image.uploaded_at,
                analyzed_at=image.analyzed_at,
            )
        )


def update_analysis(image: CropImage) -> None:
    """Write a diagnosis onto an existing row.

    An update rather than an insert: an image is one thing that acquires a diagnosis.
    Re-analysing the same photograph replaces the result instead of accumulating rows,
    which is what keeps `GET /crop-images/{id}` single-valued.

    `sha256` is untouched — the bytes did not change, and the digest is what makes the
    replacement diagnosis identical to the one it replaces.
    """
    with session_scope() as db:
        row = db.get(CropImageORM, image.id)
        if row is None:
            return
        row.analysis_status = str(image.analysis_status)
        row.analysis = (
            image.analysis.model_dump(mode="json") if image.analysis is not None else None
        )
        row.analysis_error = image.analysis_error
        row.model = image.model
        row.prompt_version = image.prompt_version
        row.ai_mode = str(image.ai_mode) if image.ai_mode is not None else None
        row.analyzed_at = image.analyzed_at


def get_image(image_id: UUID) -> CropImage | None:
    """One image by its own id.

    Deliberately **not** ownership-scoped; see the module docstring.
    """
    with session_scope() as db:
        row = db.get(CropImageORM, image_id)
        return _as_image(row) if row is not None else None


def get_digest(image_id: UUID) -> str | None:
    """The stored content digest, or None if the image is unknown.

    Separate from `get_image` because the digest is internal and most callers have no
    business with it — only the diagnosis seed does.
    """
    with session_scope() as db:
        return db.scalars(select(CropImageORM.sha256).where(CropImageORM.id == image_id)).first()


def images_for_farm(farm_id: UUID, user_id: UUID | None = None) -> list[CropImage]:
    """Every image for a farm, newest first — the order the history endpoint reports."""
    statement = (
        select(CropImageORM)
        .where(CropImageORM.farm_id == farm_id)
        .order_by(CropImageORM.uploaded_at.desc(), CropImageORM.id.desc())
    )
    if user_id is not None:
        statement = statement.where(CropImageORM.user_id == user_id)

    with session_scope() as db:
        return [_as_image(row) for row in db.scalars(statement)]


def count_images(farm_id: UUID) -> int:
    with session_scope() as db:
        return len(db.scalars(select(CropImageORM.id).where(CropImageORM.farm_id == farm_id)).all())
