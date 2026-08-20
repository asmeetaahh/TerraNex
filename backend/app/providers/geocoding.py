"""Place-name resolution.

**Coordinates are never derived from the query string.** An unresolvable place yields
an empty candidate list or an `unavailable` result — never a fabricated point. This is
the defect this module exists to close: a synthesised coordinate is indistinguishable
from a real one downstream, so every soil, weather and risk figure computed from it
would be silently wrong for a place the user believes they selected.

Two implementations share one interface:

* :func:`open_meteo_search` — the real global geocoder, no API key required.
* :func:`simulated_search` — a small offline gazetteer for tests and demo mode. It
  matches only places it genuinely knows and returns nothing otherwise.
"""

from typing import Any, Protocol

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.base import (
    GeocodeCandidate,
    ProviderCallError,
    ProviderResult,
)
from app.providers.cache import build_key, get_cache
from app.providers.http import get_json

logger = get_logger(__name__)

OPEN_METEO_SOURCE = "open-meteo-geocoding"
OPEN_METEO_URL = "https://geocoding-api.open-meteo.com/v1/search"

# Real coordinates for a handful of agricultural centres, used only when the
# geocoding provider is explicitly set to `simulated`. Unlike the removed synthetic
# fallback, every entry here is a genuine location.
OFFLINE_GAZETTEER: list[dict[str, Any]] = [
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
        "name": "Eldoret",
        "latitude": 0.5143,
        "longitude": 35.2698,
        "country": "Kenya",
        "country_code": "KE",
        "region": "Uasin Gishu County",
        "elevation_m": 2085,
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
        "name": "Nashik",
        "latitude": 19.9975,
        "longitude": 73.7898,
        "country": "India",
        "country_code": "IN",
        "region": "Maharashtra",
        "elevation_m": 584,
    },
    {
        "name": "Coimbatore",
        "latitude": 11.0018,
        "longitude": 76.9628,
        "country": "India",
        "country_code": "IN",
        "region": "Tamil Nadu",
        "elevation_m": 411,
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


class GeocodingProvider(Protocol):
    async def __call__(self, query: str, limit: int) -> ProviderResult[list[GeocodeCandidate]]: ...


def _parse_open_meteo(payload: dict[str, Any]) -> list[GeocodeCandidate]:
    """Map an Open-Meteo geocoding payload onto candidates.

    A payload with no `results` key means "no match" — a normal, correct answer that
    must surface as an empty list rather than anything invented.
    """
    results = payload.get("results") or []
    candidates: list[GeocodeCandidate] = []

    for entry in results:
        latitude, longitude = entry.get("latitude"), entry.get("longitude")
        if latitude is None or longitude is None:
            continue  # unusable without coordinates; skip rather than guess

        # admin1 is the province/state; fall back through the finer divisions.
        region = entry.get("admin1") or entry.get("admin2") or entry.get("admin3")

        candidates.append(
            GeocodeCandidate(
                name=str(entry.get("name", "")).strip() or "Unknown",
                latitude=float(latitude),
                longitude=float(longitude),
                country=entry.get("country"),
                country_code=entry.get("country_code"),
                region=region,
                elevation_m=entry.get("elevation"),
                timezone=entry.get("timezone"),
                population=entry.get("population"),
            )
        )

    return candidates


async def open_meteo_search(query: str, limit: int) -> ProviderResult[list[GeocodeCandidate]]:
    """Resolve a place name against Open-Meteo's global gazetteer."""
    try:
        payload = await get_json(
            OPEN_METEO_SOURCE,
            OPEN_METEO_URL,
            {
                "name": query,
                # Open-Meteo caps count at 100.
                "count": max(1, min(limit, 100)),
                "language": "en",
                "format": "json",
            },
        )
    except ProviderCallError as exc:
        logger.warning(
            "geocoding_unavailable",
            extra={"source": exc.source, "query": query, "reason": exc.reason},
        )
        return ProviderResult.unavailable(
            OPEN_METEO_SOURCE,
            f"Geocoding provider unavailable: {exc.reason}. No coordinates were guessed.",
        )

    candidates = _parse_open_meteo(payload)
    note = (
        f"Resolved by Open-Meteo geocoding ({len(candidates)} candidate(s))."
        if candidates
        else "Open-Meteo geocoding returned no match for this query."
    )
    return ProviderResult.live(candidates, OPEN_METEO_SOURCE, note)


async def simulated_search(query: str, limit: int) -> ProviderResult[list[GeocodeCandidate]]:
    """Offline gazetteer lookup for tests and demo mode.

    Returns only places it genuinely knows. An unknown query yields an empty list —
    there is deliberately no synthesis path.
    """
    needle = query.strip().lower()
    matches = [e for e in OFFLINE_GAZETTEER if needle in str(e["name"]).lower()]

    if not matches:
        matches = [
            e
            for e in OFFLINE_GAZETTEER
            if needle in f"{e['name']} {e.get('region', '')} {e.get('country', '')}".lower()
        ]

    candidates = [
        GeocodeCandidate(
            name=str(entry["name"]),
            latitude=float(entry["latitude"]),
            longitude=float(entry["longitude"]),
            country=entry.get("country"),
            country_code=entry.get("country_code"),
            region=entry.get("region"),
            elevation_m=entry.get("elevation_m"),
        )
        for entry in matches[:limit]
    ]

    note = (
        "Matched against the built-in offline gazetteer, not a live geocoding service."
        if candidates
        else (
            "No match in the offline gazetteer. No coordinates were generated — "
            "set GEOCODING_PROVIDER=open_meteo for global place search."
        )
    )
    return ProviderResult.simulated(candidates, note)


async def search(query: str, limit: int = 10) -> ProviderResult[list[GeocodeCandidate]]:
    """Resolve a place name using the configured provider, with caching."""
    provider_name = settings.GEOCODING_PROVIDER

    if provider_name == "simulated":
        return await simulated_search(query, limit)

    cache = get_cache("geocoding", settings.CACHE_TTL_GEOCODE_S)
    key = build_key("open_meteo", query.strip().lower(), limit)

    return await cache.get_or_fetch(key, lambda: open_meteo_search(query, limit))
