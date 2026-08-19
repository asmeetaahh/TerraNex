"""Reference data: crop catalog and simulated geocoding."""

from app.db.memory import store
from app.db.seed import seed_crops
from app.schemas.common import PaginatedResponse
from app.schemas.crop import Crop, CropList
from app.schemas.enums import CropCategory, Season
from app.schemas.location import Location, LocationList
from app.services.simulation import seeded_rng, simulated_meta

# A small gazetteer so `/reference/locations` returns recognisable places for the
# common cases. Anything else falls back to a deterministic synthetic result, so the
# endpoint always answers and the frontend's picker is never empty.
_GAZETTEER: list[dict[str, object]] = [
    {
        "name": "Nairobi",
        "latitude": -1.2864,
        "longitude": 36.8172,
        "country": "Kenya",
        "country_code": "KE",
        "region": "Nairobi County",
        "elevation_m": 1795,
    },
    {
        "name": "Nakuru",
        "latitude": -0.3031,
        "longitude": 36.0800,
        "country": "Kenya",
        "country_code": "KE",
        "region": "Nakuru County",
        "elevation_m": 1850,
    },
    {
        "name": "Kisumu",
        "latitude": -0.0917,
        "longitude": 34.7680,
        "country": "Kenya",
        "country_code": "KE",
        "region": "Kisumu County",
        "elevation_m": 1131,
    },
    {
        "name": "Kampala",
        "latitude": 0.3476,
        "longitude": 32.5825,
        "country": "Uganda",
        "country_code": "UG",
        "region": "Central Region",
        "elevation_m": 1190,
    },
    {
        "name": "Arusha",
        "latitude": -3.3869,
        "longitude": 36.6830,
        "country": "Tanzania",
        "country_code": "TZ",
        "region": "Arusha Region",
        "elevation_m": 1400,
    },
    {
        "name": "Addis Ababa",
        "latitude": 9.0250,
        "longitude": 38.7469,
        "country": "Ethiopia",
        "country_code": "ET",
        "region": "Addis Ababa",
        "elevation_m": 2355,
    },
    {
        "name": "Kano",
        "latitude": 12.0022,
        "longitude": 8.5920,
        "country": "Nigeria",
        "country_code": "NG",
        "region": "Kano State",
        "elevation_m": 481,
    },
    {
        "name": "Pune",
        "latitude": 18.5204,
        "longitude": 73.8567,
        "country": "India",
        "country_code": "IN",
        "region": "Maharashtra",
        "elevation_m": 560,
    },
    {
        "name": "Ludhiana",
        "latitude": 30.9010,
        "longitude": 75.8573,
        "country": "India",
        "country_code": "IN",
        "region": "Punjab",
        "elevation_m": 244,
    },
    {
        "name": "Hyderabad",
        "latitude": 17.3850,
        "longitude": 78.4867,
        "country": "India",
        "country_code": "IN",
        "region": "Telangana",
        "elevation_m": 542,
    },
    {
        "name": "Ames",
        "latitude": 42.0308,
        "longitude": -93.6319,
        "country": "United States",
        "country_code": "US",
        "region": "Iowa",
        "elevation_m": 287,
    },
    {
        "name": "Fresno",
        "latitude": 36.7378,
        "longitude": -119.7871,
        "country": "United States",
        "country_code": "US",
        "region": "California",
        "elevation_m": 94,
    },
    {
        "name": "Rosario",
        "latitude": -32.9442,
        "longitude": -60.6505,
        "country": "Argentina",
        "country_code": "AR",
        "region": "Santa Fe",
        "elevation_m": 25,
    },
    {
        "name": "Ribeirão Preto",
        "latitude": -21.1775,
        "longitude": -47.8103,
        "country": "Brazil",
        "country_code": "BR",
        "region": "São Paulo",
        "elevation_m": 546,
    },
    {
        "name": "Toowoomba",
        "latitude": -27.5598,
        "longitude": 151.9507,
        "country": "Australia",
        "country_code": "AU",
        "region": "Queensland",
        "elevation_m": 691,
    },
]


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


def _display_name(entry: dict[str, object]) -> str:
    parts = [entry["name"], entry.get("region"), entry.get("country")]
    return ", ".join(str(p) for p in parts if p)


def search_locations(*, query: str, limit: int) -> LocationList:
    """Simulated geocoding.

    Matches the built-in gazetteer first. If nothing matches, a single deterministic
    synthetic location is derived from the query string, so the caller always gets a
    usable coordinate. `meta.mode` is `simulated` either way — these are not real
    geocoder results.
    """
    needle = query.strip().lower()
    matches = [e for e in _GAZETTEER if needle in str(e["name"]).lower()]

    if not matches:
        matches = [e for e in _GAZETTEER if needle in _display_name(e).lower()]

    if matches:
        items = [
            Location(
                name=str(e["name"]),
                latitude=float(e["latitude"]),
                longitude=float(e["longitude"]),
                country=e.get("country"),
                country_code=e.get("country_code"),
                region=e.get("region"),
                elevation_m=e.get("elevation_m"),
                display_name=_display_name(e),
            )
            for e in matches[:limit]
        ]
        note = "Matched against a built-in gazetteer, not a live geocoding service."
    else:
        rng = seeded_rng("geocode", needle)
        latitude = round(rng.uniform(-55, 60), 4)
        longitude = round(rng.uniform(-180, 180), 4)
        items = [
            Location(
                name=query.strip().title(),
                latitude=latitude,
                longitude=longitude,
                country=None,
                country_code=None,
                region=None,
                elevation_m=round(rng.uniform(0, 2200), 1),
                display_name=f"{query.strip().title()} (synthetic location)",
            )
        ]
        note = (
            "No gazetteer match; a deterministic synthetic coordinate was generated "
            "for this query. Not a real place lookup."
        )

    return LocationList(
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        has_next=False,
        meta=simulated_meta(note),
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
