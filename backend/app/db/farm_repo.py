"""Database-backed reads and writes for farms and plantings.

**Every function here returns the same `FarmRecord` and `FarmCropRecord` dataclasses
the in-memory store returns.** That is the point of the module. `environment_service`
and `analysis_service` both accept a `FarmRecord` and read its attributes directly; by
converting at this boundary rather than handing back ORM objects, the storage swap
stays invisible to them and neither file changes.

It also sidesteps the detached-instance problem: an ORM object read inside a session
and used after it closes is a source of surprising failures. A dataclass is inert.

The service layer decides *whether* to call in here — see `database_enabled()` in
`farm_service`. This module assumes a database is configured.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.db.memory import FarmCropRecord, FarmRecord
from app.db.session import session_scope
from app.models import FarmCropORM, FarmORM, as_utc

# Columns a farm update may set, mapped straight from `FarmUpdate`. Listed rather than
# inferred so a future schema field cannot silently become writable.
# `user_id` is deliberately absent: ownership is assigned server-side from the verified
# token and can never be changed by a request body.
FARM_UPDATABLE = {
    "name",
    "latitude",
    "longitude",
    "area_hectares",
    "country_code",
    "region",
    "elevation_m",
    "irrigation_type",
    "farming_practice",
    "notes",
}

PLANTING_UPDATABLE = {
    "crop_id",
    "planting_date",
    "expected_harvest_date",
    "growth_stage",
    "area_hectares",
    "is_primary",
    "status",
    "notes",
}


def _as_farm_record(row: FarmORM) -> FarmRecord:
    """Timestamps go through `as_utc`: SQLite drops the timezone and Postgres returns
    the session offset, so without it the same farm reports `created_at` one way from
    `POST /farms` (built in memory) and another from `GET /farms/{id}` (read back)."""
    return FarmRecord(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        latitude=row.latitude,
        longitude=row.longitude,
        area_hectares=row.area_hectares,
        country_code=row.country_code,
        region=row.region,
        elevation_m=row.elevation_m,
        irrigation_type=row.irrigation_type,
        farming_practice=row.farming_practice,
        notes=row.notes,
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at),
        deleted_at=as_utc(row.deleted_at),
    )


def _as_planting_record(row: FarmCropORM) -> FarmCropRecord:
    return FarmCropRecord(
        id=row.id,
        farm_id=row.farm_id,
        crop_id=row.crop_id,
        planting_date=row.planting_date,
        expected_harvest_date=row.expected_harvest_date,
        growth_stage=row.growth_stage,
        area_hectares=row.area_hectares,
        is_primary=row.is_primary,
        status=row.status,
        notes=row.notes,
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at),
    )


def _scalar(value: Any) -> Any:
    """Enums arrive from Pydantic as `StrEnum` members; store their value."""
    return value.value if hasattr(value, "value") else value


# --------------------------------------------------------------------------
# Farms
# --------------------------------------------------------------------------


def get_farm(farm_id: UUID, user_id: UUID | None = None) -> FarmRecord | None:
    """A live farm, or None.

    Soft-deleted rows are invisible, as in the store. When `user_id` is given, a farm
    owned by anyone else is invisible too — the caller cannot tell "not yours" from
    "does not exist", which is what stops the endpoint confirming that someone else's
    farm id is real.
    """
    with session_scope() as db:
        row = db.get(FarmORM, farm_id)
        if row is None or row.deleted_at is not None:
            return None
        if user_id is not None and row.user_id != user_id:
            return None
        return _as_farm_record(row)


def live_farms(user_id: UUID | None = None) -> list[FarmRecord]:
    """Every farm that has not been soft-deleted, oldest first, scoped to an owner.

    Ordering by `created_at` reproduces the store's insertion order, so `GET /farms`
    returns farms in the same sequence it always has.
    """
    statement = select(FarmORM).where(FarmORM.deleted_at.is_(None))
    if user_id is not None:
        statement = statement.where(FarmORM.user_id == user_id)
    statement = statement.order_by(FarmORM.created_at, FarmORM.id)
    with session_scope() as db:
        return [_as_farm_record(row) for row in db.scalars(statement)]


def insert_farm(record: FarmRecord) -> None:
    with session_scope() as db:
        db.add(
            FarmORM(
                id=record.id,
                user_id=record.user_id,
                name=record.name,
                latitude=record.latitude,
                longitude=record.longitude,
                area_hectares=record.area_hectares,
                country_code=record.country_code,
                region=record.region,
                elevation_m=record.elevation_m,
                irrigation_type=_scalar(record.irrigation_type),
                farming_practice=_scalar(record.farming_practice),
                notes=record.notes,
                created_at=record.created_at,
                updated_at=record.updated_at,
                deleted_at=record.deleted_at,
            )
        )


def update_farm(
    farm_id: UUID,
    changes: dict[str, Any],
    updated_at: datetime,
    user_id: UUID | None = None,
) -> FarmRecord | None:
    with session_scope() as db:
        row = db.get(FarmORM, farm_id)
        if row is None or row.deleted_at is not None:
            return None
        if user_id is not None and row.user_id != user_id:
            return None
        for key, value in changes.items():
            if key in FARM_UPDATABLE:
                setattr(row, key, _scalar(value))
        row.updated_at = updated_at
        db.flush()
        return _as_farm_record(row)


def soft_delete_farm(farm_id: UUID, when: datetime, user_id: UUID | None = None) -> None:
    with session_scope() as db:
        row = db.get(FarmORM, farm_id)
        if row is None:
            return
        if user_id is not None and row.user_id != user_id:
            return
        row.deleted_at = when


def count_plantings(farm_id: UUID) -> int:
    """Backs `Farm.crop_count`, which is derived on read and never stored."""
    statement = select(func.count()).select_from(FarmCropORM).where(FarmCropORM.farm_id == farm_id)
    with session_scope() as db:
        return int(db.scalar(statement) or 0)


def count_plantings_by_farm(farm_ids: list[UUID]) -> dict[UUID, int]:
    """Planting counts for many farms in one query.

    `GET /farms` renders `crop_count` for every farm; asking per farm would be an N+1
    on the list endpoint the dashboard opens with.
    """
    if not farm_ids:
        return {}
    statement = (
        select(FarmCropORM.farm_id, func.count())
        .where(FarmCropORM.farm_id.in_(farm_ids))
        .group_by(FarmCropORM.farm_id)
    )
    with session_scope() as db:
        return {farm_id: int(total) for farm_id, total in db.execute(statement)}


# --------------------------------------------------------------------------
# Plantings
# --------------------------------------------------------------------------


def plantings_for_farm(farm_id: UUID) -> list[FarmCropRecord]:
    statement = (
        select(FarmCropORM)
        .where(FarmCropORM.farm_id == farm_id)
        .order_by(FarmCropORM.created_at, FarmCropORM.id)
    )
    with session_scope() as db:
        return [_as_planting_record(row) for row in db.scalars(statement)]


def get_planting(farm_id: UUID, farm_crop_id: UUID) -> FarmCropRecord | None:
    with session_scope() as db:
        row = db.get(FarmCropORM, farm_crop_id)
        if row is None or row.farm_id != farm_id:
            return None
        return _as_planting_record(row)


def insert_planting(record: FarmCropRecord) -> None:
    """Insert a planting, demoting any incumbent primary in the same transaction.

    Both statements share one `session_scope`, so a farm can never be left with two
    primaries or none — which is why this is not enforced by a partial unique index:
    the constraint would fire between the demote and the insert.
    """
    with session_scope() as db:
        if record.is_primary:
            _demote_others(db, record.farm_id, keep=record.id)
        db.add(
            FarmCropORM(
                id=record.id,
                farm_id=record.farm_id,
                crop_id=record.crop_id,
                planting_date=record.planting_date,
                expected_harvest_date=record.expected_harvest_date,
                growth_stage=_scalar(record.growth_stage),
                area_hectares=record.area_hectares,
                is_primary=record.is_primary,
                status=_scalar(record.status),
                notes=record.notes,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )


def update_planting(
    farm_id: UUID,
    farm_crop_id: UUID,
    changes: dict[str, Any],
    updated_at: datetime,
) -> FarmCropRecord | None:
    with session_scope() as db:
        row = db.get(FarmCropORM, farm_crop_id)
        if row is None or row.farm_id != farm_id:
            return None
        for key, value in changes.items():
            if key in PLANTING_UPDATABLE:
                setattr(row, key, _scalar(value))
        if changes.get("is_primary"):
            _demote_others(db, farm_id, keep=farm_crop_id)
        row.updated_at = updated_at
        db.flush()
        return _as_planting_record(row)


def delete_planting(farm_crop_id: UUID) -> None:
    with session_scope() as db:
        row = db.get(FarmCropORM, farm_crop_id)
        if row is not None:
            db.delete(row)


def _demote_others(db, farm_id: UUID, *, keep: UUID) -> None:
    """`is_primary` marks the crop an analysis centres on — at most one per farm."""
    statement = select(FarmCropORM).where(
        FarmCropORM.farm_id == farm_id,
        FarmCropORM.id != keep,
        FarmCropORM.is_primary.is_(True),
    )
    for other in db.scalars(statement):
        other.is_primary = False
