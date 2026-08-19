"""Farm and planting CRUD against the Phase 3 in-memory store.

Ownership is not enforced yet: `ENABLE_AUTH` is false, so every farm belongs to the
implicit demo user. When auth lands, the ownership check goes here and no request or
response shape changes.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.errors import ErrorCode, FarmNotFoundError, NotFoundError
from app.db.memory import FarmCropRecord, FarmRecord, store
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


class CropNotFoundError(NotFoundError):
    """The crop or planting does not exist."""

    code = ErrorCode.CROP_NOT_FOUND


def _to_farm(record: FarmRecord) -> Farm:
    """Project stored state into the API model, computing derived fields on read."""
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
        crop_count=len(store.crops_for_farm(record.id)),
        has_analysis=store.latest_run(record.id) is not None,
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


def require_farm(farm_id: UUID) -> FarmRecord:
    record = store.get_farm(farm_id)
    if record is None:
        raise FarmNotFoundError(
            f"Farm {farm_id} does not exist or is not accessible.",
            details={"farm_id": str(farm_id)},
        )
    return record


# --------------------------------------------------------------------------
# Farms
# --------------------------------------------------------------------------


def create_farm(payload: FarmCreate) -> Farm:
    now = _now()
    record = FarmRecord(
        id=uuid4(),
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
    with store.lock:
        store.farms[record.id] = record
    return _to_farm(record)


def list_farms(*, page: int, page_size: int) -> FarmList:
    farms = [_to_farm(r) for r in store.live_farms()]
    return paginate(FarmList, farms, page, page_size)


def get_farm(farm_id: UUID) -> Farm:
    return _to_farm(require_farm(farm_id))


def update_farm(farm_id: UUID, payload: FarmUpdate) -> Farm:
    record = require_farm(farm_id)
    changes = payload.model_dump(exclude_unset=True)

    with store.lock:
        for key, value in changes.items():
            if key == "country_code" and value:
                value = value.upper()
            setattr(record, key, value)
        record.updated_at = _now()

    return _to_farm(record)


def delete_farm(farm_id: UUID) -> None:
    """Soft delete. Analyses and images are retained but become unreachable."""
    record = require_farm(farm_id)
    with store.lock:
        record.deleted_at = _now()


# --------------------------------------------------------------------------
# Plantings
# --------------------------------------------------------------------------


def add_farm_crop(farm_id: UUID, payload: FarmCropCreate) -> FarmCrop:
    require_farm(farm_id)

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

    with store.lock:
        if record.is_primary:
            _demote_other_primaries(farm_id, keep=record.id)
        store.farm_crops[record.id] = record

    return _to_farm_crop(record)


def list_farm_crops(farm_id: UUID, *, page: int, page_size: int) -> FarmCropList:
    require_farm(farm_id)
    crops = [_to_farm_crop(r) for r in store.crops_for_farm(farm_id)]
    return paginate(FarmCropList, crops, page, page_size)


def update_farm_crop(farm_id: UUID, farm_crop_id: UUID, payload: FarmCropUpdate) -> FarmCrop:
    require_farm(farm_id)
    record = _require_planting(farm_id, farm_crop_id)

    changes = payload.model_dump(exclude_unset=True)
    if "crop_id" in changes and get_crop(changes["crop_id"]) is None:
        raise CropNotFoundError(
            f"Crop {changes['crop_id']} is not in the reference catalog.",
            details={"crop_id": str(changes["crop_id"])},
        )

    with store.lock:
        for key, value in changes.items():
            setattr(record, key, value)
        if changes.get("is_primary"):
            _demote_other_primaries(farm_id, keep=record.id)
        record.updated_at = _now()

    return _to_farm_crop(record)


def delete_farm_crop(farm_id: UUID, farm_crop_id: UUID) -> None:
    require_farm(farm_id)
    _require_planting(farm_id, farm_crop_id)
    with store.lock:
        store.farm_crops.pop(farm_crop_id, None)


def _require_planting(farm_id: UUID, farm_crop_id: UUID) -> FarmCropRecord:
    record = store.farm_crops.get(farm_crop_id)
    if record is None or record.farm_id != farm_id:
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
    plantings = store.crops_for_farm(farm_id)
    if not plantings:
        return None
    for planting in plantings:
        if planting.is_primary:
            return planting
    return plantings[0]
