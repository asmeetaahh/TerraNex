"""Farm registration and per-farm crop plantings.

Ownership is resolved server-side by the `CurrentUser` dependency. While
`ENABLE_AUTH=false` every farm belongs to the seeded demo user, so the frontend can
build the full registration flow before login exists. Turning auth on changes no
request or response shape, and no path, method, field or status code.

The dependency is a plain function rather than `fastapi.security.HTTPBearer`, because a
security scheme would add `securitySchemes` and a per-path `security` block to the
generated OpenAPI and `contracts/openapi.json` is frozen. See `app.core.deps`.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.core.deps import CurrentUser, get_current_user
from app.schemas.crop import FarmCrop, FarmCropCreate, FarmCropList, FarmCropUpdate
from app.schemas.farm import Farm, FarmCreate, FarmList, FarmUpdate
from app.services import farm_service

router = APIRouter(prefix="/farms", tags=["farms"])

# The resolved identity every farm operation is scoped to. Contributes nothing to the
# OpenAPI document — verified, so the frozen contract stays byte-identical.
Caller = Annotated[CurrentUser, Depends(get_current_user)]

FarmId = Annotated[UUID, Path(description="Farm identifier.")]
FarmCropId = Annotated[UUID, Path(description="Farm-crop (planting) identifier.")]


@router.post(
    "",
    response_model=Farm,
    status_code=status.HTTP_201_CREATED,
    summary="Register a farm",
    description=(
        "Creates a farm at the given coordinates. Coordinates drive every downstream data lookup."
    ),
)
async def create_farm(payload: FarmCreate, user: Caller) -> Farm:
    return farm_service.create_farm(payload, user)


@router.get("", response_model=FarmList, summary="List farms")
async def list_farms(
    user: Caller,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> FarmList:
    return farm_service.list_farms(page=page, page_size=page_size, user=user)


@router.get(
    "/{farm_id}",
    response_model=Farm,
    summary="Get a farm",
    responses={404: {"description": "FARM_NOT_FOUND"}},
)
async def get_farm(farm_id: FarmId, user: Caller) -> Farm:
    return farm_service.get_farm(farm_id, user)


@router.patch(
    "/{farm_id}",
    response_model=Farm,
    summary="Update a farm",
    description="Partial update. Omitted fields are left unchanged.",
    responses={404: {"description": "FARM_NOT_FOUND"}},
)
async def update_farm(farm_id: FarmId, payload: FarmUpdate, user: Caller) -> Farm:
    return farm_service.update_farm(farm_id, payload, user)


@router.delete(
    "/{farm_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a farm",
    description="Soft delete. Historical analysis runs are retained.",
    responses={404: {"description": "FARM_NOT_FOUND"}},
)
async def delete_farm(farm_id: FarmId, user: Caller) -> Response:
    farm_service.delete_farm(farm_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------
# Plantings
# --------------------------------------------------------------------------


@router.post(
    "/{farm_id}/crops",
    response_model=FarmCrop,
    status_code=status.HTTP_201_CREATED,
    summary="Add a crop to a farm",
    description="Attaches a catalog crop to this farm with planting dates and growth stage.",
    responses={404: {"description": "FARM_NOT_FOUND or CROP_NOT_FOUND"}},
)
async def add_farm_crop(farm_id: FarmId, payload: FarmCropCreate, user: Caller) -> FarmCrop:
    return farm_service.add_farm_crop(farm_id, payload, user)


@router.get(
    "/{farm_id}/crops",
    response_model=FarmCropList,
    summary="List a farm's crops",
    responses={404: {"description": "FARM_NOT_FOUND"}},
)
async def list_farm_crops(
    farm_id: FarmId,
    user: Caller,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> FarmCropList:
    return farm_service.list_farm_crops(farm_id, page=page, page_size=page_size, user=user)


@router.patch(
    "/{farm_id}/crops/{farm_crop_id}",
    response_model=FarmCrop,
    summary="Update a farm's crop",
    description="Used to advance growth stage or record the harvest.",
    responses={404: {"description": "FARM_NOT_FOUND or CROP_NOT_FOUND"}},
)
async def update_farm_crop(
    farm_id: FarmId, farm_crop_id: FarmCropId, payload: FarmCropUpdate, user: Caller
) -> FarmCrop:
    return farm_service.update_farm_crop(farm_id, farm_crop_id, payload, user)


@router.delete(
    "/{farm_id}/crops/{farm_crop_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a crop from a farm",
    responses={404: {"description": "FARM_NOT_FOUND or CROP_NOT_FOUND"}},
)
async def delete_farm_crop(farm_id: FarmId, farm_crop_id: FarmCropId, user: Caller) -> Response:
    farm_service.delete_farm_crop(farm_id, farm_crop_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
