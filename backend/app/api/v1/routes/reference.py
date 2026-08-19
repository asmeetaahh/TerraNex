"""Reference data: the crop catalog, the enum catalog, and geocoding.

These power the frontend's dropdowns and the farm-registration location picker.
`/reference/enums` is answered from the enum definitions themselves, so it is live
today — the frontend can build every select input immediately.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.core.errors import NotImplementedYetError
from app.schemas.crop import CropList
from app.schemas.enums import CropCategory, EnumCatalogResponse, Season, build_enum_catalog
from app.schemas.location import LocationList

router = APIRouter(prefix="/reference", tags=["reference"])


@router.get(
    "/enums",
    response_model=EnumCatalogResponse,
    summary="Enum catalog",
    description=(
        "Every domain enum with display labels. Render dropdowns from this rather "
        "than hardcoding values — new enum members then appear without a frontend change."
    ),
)
async def get_enums() -> EnumCatalogResponse:
    # Built from the enum definitions, so it can never drift from what the API accepts.
    return EnumCatalogResponse(enums=build_enum_catalog())


@router.get(
    "/crops",
    response_model=CropList,
    summary="Crop catalog",
    description=(
        "The seeded global crop catalog with the agronomic constants used by the risk "
        "engine. Read-only reference data, shared by every farm."
    ),
)
async def list_crops(
    category: Annotated[CropCategory | None, Query(description="Filter by category.")] = None,
    season: Annotated[Season | None, Query(description="Filter by season.")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> CropList:
    raise NotImplementedYetError("Crop catalog", step="Step 3 (seeded fixtures)")


@router.get(
    "/locations",
    response_model=LocationList,
    summary="Geocode a place name",
    description=(
        "Resolves a place name to coordinates for farm registration. The frontend must "
        "not call a geocoding provider directly — the backend is the only integration layer.\n\n"
        "Check `meta.mode`: `simulated` means these are not real geocoder results."
    ),
)
async def search_locations(
    q: Annotated[str, Query(min_length=2, max_length=120, description="Place name to search.")],
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
) -> LocationList:
    raise NotImplementedYetError("Location geocoding", step="Step 5 (real providers)")
