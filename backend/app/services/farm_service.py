"""Farm and planting CRUD.

Reads and writes the database when one is configured, and the Phase 3 in-memory store
otherwise. Both paths produce identical `Farm` and `FarmCrop` payloads — the storage
swap is invisible from the API, which is what lets the migration proceed one service at
a time with the whole suite green in between.

`app.db.farm_repo` returns the same `FarmRecord` and `FarmCropRecord` dataclasses the
store does, so `require_farm` and `primary_planting` keep their existing return types
and the services that consume them are untouched.

**Ownership is enforced here.** Every entry point takes the resolved `CurrentUser` and
scopes its reads and writes to that owner. A farm belonging to someone else is reported
as `FARM_NOT_FOUND` rather than `FORBIDDEN`: a 403 would confirm the id exists, which
tells an attacker something a 404 does not. `FORBIDDEN` remains in the taxonomy for
resources whose existence is not itself sensitive.

`user_id` is set from the verified identity on create and is absent from
`FARM_UPDATABLE`, so no request body can assign or reassign ownership.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.deps import CurrentUser
from app.core.errors import ErrorCode, FarmNotFoundError, NotFoundError
from app.db import farm_repo
from app.db.memory import FarmCropRecord, FarmRecord, store
from app.db.session import database_enabled
from app.schemas.crop import (
    FarmCrop,
    FarmCropCreate,
    FarmCropList,
    FarmCropUpdate,
)
from app.schemas.farm import Farm, FarmCreate, FarmList, FarmUpdate
from app.services.reference_service import get_crop, paginate


def _now() -> datetime:
    return datetime.now(UTC)


def _ensure_owner_row(user: CurrentUser) -> None:
    """A farm cannot reference an owner that does not exist.

    Authenticated users get their row when their token is first verified. The demo
    user's id is derived rather than assigned, so its row is created lazily here — the
    first time a demo farm needs an owner to point at.
    """
    if not database_enabled():
        return
    from app.db import user_repo

    user_repo.ensure_user(user_id=user.id, auth_id=user.auth_id, email=user.email)


class CropNotFoundError(NotFoundError):
    """The crop or planting does not exist."""

    code = ErrorCode.CROP_NOT_FOUND


def _crop_count(farm_id: UUID) -> int:
    if database_enabled():
        return farm_repo.count_plantings(farm_id)
    return len(store.crops_for_farm(farm_id))


def _has_analysis(farm_id: UUID) -> bool:
    """Analysis runs are still held in the in-memory store in this phase, so this
    reads from there whether or not a database is configured."""
    return store.latest_run(farm_id) is not None


def _to_farm(record: FarmRecord, *, crop_count: int | None = None) -> Farm:
    """Project stored state into the API model, computing derived fields on read.

    `crop_count` may be supplied by a caller that already counted in bulk, which is
    what keeps `GET /farms` from issuing one count per farm.
    """
    return Farm(
        id=record.id,
        name=record.name,
        latitude=record.latitude,
        longitude=record.longitude,
        area_hectares=record.area_hectares,
        country_code=record.country_code,
        region=record.region,
        elevation_m=record.elevation_m,
        irrigation_type=record.irrigation_type,
        farming_practice=record.farming_practice,
        notes=record.notes,
        created_at=record.created_at,
        updated_at=record.updated_at,
        crop_count=_crop_count(record.id) if crop_count is None else crop_count,
        has_analysis=_has_analysis(record.id),
    )


def _to_farm_crop(record: FarmCropRecord) -> FarmCrop:
    crop = get_crop(record.crop_id)
    if crop is None:  # pragma: no cover - catalog is immutable at runtime
        raise CropNotFoundError(f"Crop {record.crop_id} is not in the catalog.")
    return FarmCrop(
        id=record.id,
        farm_id=record.farm_id,
        crop_id=record.crop_id,
        crop=crop,
        planting_date=record.planting_date,
        expected_harvest_date=record.expected_harvest_date,
        growth_stage=record.growth_stage,
        area_hectares=record.area_hectares,
        is_primary=record.is_primary,
        status=record.status,
        notes=record.notes,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def plantings_for_farm(farm_id: UUID) -> list[FarmCropRecord]:
    """Every planting on a farm, from whichever backend holds them.

    Public because `analysis_service` builds the dashboard's crop list from it. Reading
    the in-memory store directly from another module is what made the dashboard report
    zero crops for a farm whose `crop_count` said otherwise, once plantings moved into
    the database.
    """
    if database_enabled():
        return farm_repo.plantings_for_farm(farm_id)
    return store.crops_for_farm(farm_id)


def find_planting(farm_id: UUID, farm_crop_id: UUID) -> FarmCropRecord | None:
    """A planting on this farm, or None.

    The non-raising counterpart of `_require_planting`, for callers that treat a
    missing planting as simply absent rather than as an error.
    """
    if database_enabled():
        return farm_repo.get_planting(farm_id, farm_crop_id)
    record = store.farm_crops.get(farm_crop_id)
    return record if record is not None and record.farm_id == farm_id else None


def require_farm(farm_id: UUID, user: CurrentUser) -> FarmRecord:
    """The farm, or `FARM_NOT_FOUND`.

    `user` is **required, with no default**. It used to default to `None`, which meant
    "no ownership filter" — and every service outside this module called it that way, so
    any authenticated user could read and write any other user's farm through the
    analysis, weather, soil, vegetation, recommendation and image endpoints. A missing
    argument is now an error at the call site rather than a silent bypass.
    """
    owner = user.id
    record = (
        farm_repo.get_farm(farm_id, owner) if database_enabled() else store.get_farm(farm_id, owner)
    )
    if record is None:
        raise FarmNotFoundError(
            f"Farm {farm_id} does not exist or is not accessible.",
            details={"farm_id": str(farm_id)},
        )
    return record


# --------------------------------------------------------------------------
# Farms
# --------------------------------------------------------------------------


def create_farm(payload: FarmCreate, user: CurrentUser) -> Farm:
    """Register a farm owned by `user`.

    Ownership comes from the resolved identity, never from `payload` — `FarmCreate` has
    no `user_id` field and gaining one would be a contract change.
    """
    _ensure_owner_row(user)
    now = _now()
    record = FarmRecord(
        id=uuid4(),
        user_id=user.id,
        name=payload.name,
        latitude=payload.latitude,
        longitude=payload.longitude,
        area_hectares=payload.area_hectares,
        country_code=payload.country_code.upper() if payload.country_code else None,
        region=payload.region,
        elevation_m=payload.elevation_m,
        irrigation_type=payload.irrigation_type,
        farming_practice=payload.farming_practice,
        notes=payload.notes,
        created_at=now,
        updated_at=now,
    )
    if database_enabled():
        farm_repo.insert_farm(record)
    else:
        with store.lock:
            store.farms[record.id] = record
    return _to_farm(record, crop_count=0)


def list_farms(*, page: int, page_size: int, user: CurrentUser) -> FarmList:
    if database_enabled():
        records = farm_repo.live_farms(user.id)
        # One grouped count for the whole page rather than one per farm.
        counts = farm_repo.count_plantings_by_farm([r.id for r in records])
        farms = [_to_farm(r, crop_count=counts.get(r.id, 0)) for r in records]
    else:
        farms = [_to_farm(r) for r in store.live_farms(user.id)]
    return paginate(FarmList, farms, page, page_size)


def get_farm(farm_id: UUID, user: CurrentUser) -> Farm:
    return _to_farm(require_farm(farm_id, user))


def update_farm(farm_id: UUID, payload: FarmUpdate, user: CurrentUser) -> Farm:
    record = require_farm(farm_id, user)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("country_code"):
        changes["country_code"] = changes["country_code"].upper()

    if database_enabled():
        updated = farm_repo.update_farm(farm_id, changes, _now(), user.id)
        if updated is None:  # pragma: no cover - require_farm already proved it exists
            raise FarmNotFoundError(f"Farm {farm_id} does not exist or is not accessible.")
        return _to_farm(updated)

    with store.lock:
        for key, value in changes.items():
            setattr(record, key, value)
        record.updated_at = _now()

    return _to_farm(record)


def delete_farm(farm_id: UUID, user: CurrentUser) -> None:
    """Soft delete. Analyses and images are retained but become unreachable."""
    require_farm(farm_id, user)
    if database_enabled():
        farm_repo.soft_delete_farm(farm_id, _now(), user.id)
        return
    record = store.farms[farm_id]
    with store.lock:
        record.deleted_at = _now()


# --------------------------------------------------------------------------
# Plantings
# --------------------------------------------------------------------------


def add_farm_crop(farm_id: UUID, payload: FarmCropCreate, user: CurrentUser) -> FarmCrop:
    require_farm(farm_id, user)

    if get_crop(payload.crop_id) is None:
        raise CropNotFoundError(
            f"Crop {payload.crop_id} is not in the reference catalog.",
            details={"crop_id": str(payload.crop_id)},
        )

    now = _now()
    record = FarmCropRecord(
        id=uuid4(),
        farm_id=farm_id,
        crop_id=payload.crop_id,
        planting_date=payload.planting_date,
        expected_harvest_date=payload.expected_harvest_date,
        growth_stage=payload.growth_stage,
        area_hectares=payload.area_hectares,
        is_primary=payload.is_primary,
        status=payload.status,
        notes=payload.notes,
        created_at=now,
        updated_at=now,
    )

    if database_enabled():
        farm_repo.insert_planting(record)
    else:
        with store.lock:
            if record.is_primary:
                _demote_other_primaries(farm_id, keep=record.id)
            store.farm_crops[record.id] = record

    return _to_farm_crop(record)


def list_farm_crops(farm_id: UUID, *, page: int, page_size: int, user: CurrentUser) -> FarmCropList:
    require_farm(farm_id, user)
    crops = [_to_farm_crop(r) for r in plantings_for_farm(farm_id)]
    return paginate(FarmCropList, crops, page, page_size)


def update_farm_crop(
    farm_id: UUID, farm_crop_id: UUID, payload: FarmCropUpdate, user: CurrentUser
) -> FarmCrop:
    require_farm(farm_id, user)
    record = _require_planting(farm_id, farm_crop_id)

    changes = payload.model_dump(exclude_unset=True)
    if "crop_id" in changes and get_crop(changes["crop_id"]) is None:
        raise CropNotFoundError(
            f"Crop {changes['crop_id']} is not in the reference catalog.",
            details={"crop_id": str(changes["crop_id"])},
        )

    if database_enabled():
        updated = farm_repo.update_planting(farm_id, farm_crop_id, changes, _now())
        if updated is None:  # pragma: no cover - _require_planting already proved it
            raise CropNotFoundError(f"Planting {farm_crop_id} does not exist on farm {farm_id}.")
        return _to_farm_crop(updated)

    with store.lock:
        for key, value in changes.items():
            setattr(record, key, value)
        if changes.get("is_primary"):
            _demote_other_primaries(farm_id, keep=record.id)
        record.updated_at = _now()

    return _to_farm_crop(record)


def delete_farm_crop(farm_id: UUID, farm_crop_id: UUID, user: CurrentUser) -> None:
    require_farm(farm_id, user)
    _require_planting(farm_id, farm_crop_id)
    if database_enabled():
        farm_repo.delete_planting(farm_crop_id)
        return
    with store.lock:
        store.farm_crops.pop(farm_crop_id, None)


def _require_planting(farm_id: UUID, farm_crop_id: UUID) -> FarmCropRecord:
    record = find_planting(farm_id, farm_crop_id)
    if record is None:
        raise CropNotFoundError(
            f"Planting {farm_crop_id} does not exist on farm {farm_id}.",
            details={"farm_id": str(farm_id), "farm_crop_id": str(farm_crop_id)},
        )
    return record


def _demote_other_primaries(farm_id: UUID, *, keep: UUID) -> None:
    """`is_primary` marks the crop the analysis centres on — at most one per farm."""
    for other in store.crops_for_farm(farm_id):
        if other.id != keep and other.is_primary:
            other.is_primary = False


def primary_planting(farm_id: UUID) -> FarmCropRecord | None:
    """The crop an analysis should centre on: the explicit primary, else the first."""
    plantings = plantings_for_farm(farm_id)
    if not plantings:
        return None
    for planting in plantings:
        if planting.is_primary:
            return planting
    return plantings[0]
