"""Reference data: the crop catalog and place-name resolution.

Geocoding is delegated to `app.providers.geocoding`. This module never synthesises
coordinates — see :func:`search_locations`.
"""

from app.db.memory import store
from app.db.seed import seed_crops
from app.providers import geocoding
from app.schemas.common import PaginatedResponse
from app.schemas.crop import Crop, CropList
from app.schemas.enums import CropCategory, Season
from app.schemas.location import Location, LocationList


def _ensure_catalog() -> None:
    if not store.crops:
        seed_crops()


def list_crops(
    *,
    category: CropCategory | None,
    season: Season | None,
    page: int,
    page_size: int,
) -> CropList:
    _ensure_catalog()

    crops: list[Crop] = list(store.crops.values())
    if category is not None:
        crops = [c for c in crops if c.category == category]
    if season is not None:
        crops = [c for c in crops if c.season == season]

    return _paginate(CropList, crops, page, page_size)


def get_crop(crop_id) -> Crop | None:
    _ensure_catalog()
    return store.crops.get(crop_id)


async def search_locations(*, query: str, limit: int) -> LocationList:
    """Resolve a place name to real coordinates.

    Delegates to the configured geocoding provider. **No coordinate is ever derived
    from the query string.** An unresolvable place returns an empty candidate list —
    a fabricated point would be indistinguishable from a real one downstream, and
    every soil, weather and risk figure computed from it would be silently wrong for
    a place the user believes they picked.

    The response's `meta.mode` reports whether these came from the live geocoder, a
    cache, the offline gazetteer, or not at all.
    """
    result = await geocoding.search(query, limit)
    candidates = result.data or []

    items = [
        Location(
            name=candidate.name,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            country=candidate.country,
            country_code=candidate.country_code,
            region=candidate.region,
            elevation_m=candidate.elevation_m,
            display_name=candidate.display_name,
        )
        for candidate in candidates[:limit]
    ]

    return LocationList(
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        has_next=False,
        meta=result.meta,
    )


def _paginate[T](list_cls: type, items: list[T], page: int, page_size: int):
    """Slice a list into the shared collection envelope."""
    total = len(items)
    start = (page - 1) * page_size
    window = items[start : start + page_size]
    return list_cls(
        items=window,
        total=total,
        page=page,
        page_size=page_size,
        has_next=start + page_size < total,
    )


def paginate[T](list_cls: type[PaginatedResponse[T]], items: list[T], page: int, page_size: int):
    """Public helper shared by the other services."""
    return _paginate(list_cls, items, page, page_size)
