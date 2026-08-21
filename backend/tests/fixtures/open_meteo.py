"""Open-Meteo response builders, shaped exactly like the real API's payloads.

Shared by the provider tests and the shared-environment tests so both exercise the
same parsing path.

Two layers live here:

* the original Nashik-defaulted :func:`daily_payload` / :func:`hourly_payload`, whose
  output is unchanged — the existing provider and shared-environment tests assert
  against those exact values;
* a location-parameterised layer built on :class:`Site`, used by the global-location
  smoke tests to prove that nothing in the stack assumes one country, one hemisphere
  or one timezone.

The :data:`SITES` matrix is **test data, not a supported-country list**. TerraNex
resolves farms anywhere Open-Meteo does; these twelve places were chosen to span
hemispheres, latitudes and UTC offsets, and their coordinates and IANA zones are the
real ones.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import httpx
import respx

from app.providers.geocoding import OPEN_METEO_URL as GEOCODING_URL
from app.providers.weather import FORECAST_URL

NASHIK_LAT = 19.99727
NASHIK_LON = 73.79096

CANONICAL_PAST_DAYS = 92
CANONICAL_FORECAST_DAYS = 16
CANONICAL_TOTAL = CANONICAL_PAST_DAYS + CANONICAL_FORECAST_DAYS


# --------------------------------------------------------------------------
# The location matrix
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    """A real place, described only by what the provider layer actually carries.

    Deliberately free of climate fields. P1-2 proves that coordinates, timezones and
    the real-provider path work everywhere; whether the *values* are climatically
    right for an arid or polar site is the simulator/climate milestone's question, and
    inventing per-site rainfall here would only test this fixture against itself.
    """

    key: str
    name: str
    country: str
    country_code: str
    admin1: str
    latitude: float
    longitude: float
    timezone: str
    utc_offset_seconds: int


RIBEIRAO_PRETO = Site(
    key="ribeirao_preto",
    name="Ribeirão Preto",
    country="Brazil",
    country_code="BR",
    admin1="São Paulo",
    latitude=-21.1775,
    longitude=-47.8103,
    timezone="America/Sao_Paulo",
    utc_offset_seconds=-10_800,
)

KRASNODAR = Site(
    key="krasnodar",
    name="Krasnodar",
    country="Russia",
    country_code="RU",
    admin1="Krasnodar Krai",
    latitude=45.0453,
    longitude=38.9818,
    timezone="Europe/Moscow",
    utc_offset_seconds=10_800,
)

MURMANSK = Site(
    key="murmansk",
    name="Murmansk",
    country="Russia",
    country_code="RU",
    admin1="Murmansk Oblast",
    latitude=68.9678,
    longitude=33.0992,
    timezone="Europe/Moscow",
    utc_offset_seconds=10_800,
)

NASHIK = Site(
    key="nashik",
    name="Nashik",
    country="India",
    country_code="IN",
    admin1="Maharashtra",
    latitude=NASHIK_LAT,
    longitude=NASHIK_LON,
    timezone="Asia/Kolkata",
    utc_offset_seconds=19_800,
)

ZHENGZHOU = Site(
    key="zhengzhou",
    name="Zhengzhou",
    country="China",
    country_code="CN",
    admin1="Henan",
    latitude=34.7578,
    longitude=113.6486,
    timezone="Asia/Shanghai",
    utc_offset_seconds=28_800,
)

BLOEMFONTEIN = Site(
    key="bloemfontein",
    name="Bloemfontein",
    country="South Africa",
    country_code="ZA",
    admin1="Free State",
    latitude=-29.1211,
    longitude=26.2140,
    timezone="Africa/Johannesburg",
    utc_offset_seconds=7_200,
)

ASWAN = Site(
    key="aswan",
    name="Aswan",
    country="Egypt",
    country_code="EG",
    admin1="Aswan",
    latitude=24.0908,
    longitude=32.8994,
    timezone="Africa/Cairo",
    utc_offset_seconds=7_200,
)

BAHIR_DAR = Site(
    key="bahir_dar",
    name="Bahir Dar",
    country="Ethiopia",
    country_code="ET",
    admin1="Amhara",
    latitude=11.5936,
    longitude=37.3908,
    timezone="Africa/Addis_Ababa",
    utc_offset_seconds=10_800,
)

SHIRAZ = Site(
    key="shiraz",
    name="Shiraz",
    country="Iran",
    country_code="IR",
    admin1="Fars",
    latitude=29.6103,
    longitude=52.5311,
    timezone="Asia/Tehran",
    utc_offset_seconds=12_600,
)

AL_AIN = Site(
    key="al_ain",
    name="Al Ain",
    country="United Arab Emirates",
    country_code="AE",
    admin1="Abu Dhabi",
    latitude=24.1917,
    longitude=55.7606,
    timezone="Asia/Dubai",
    utc_offset_seconds=14_400,
)

MEDAN = Site(
    key="medan",
    name="Medan",
    country="Indonesia",
    country_code="ID",
    admin1="North Sumatra",
    latitude=3.5833,
    longitude=98.6667,
    timezone="Asia/Jakarta",
    utc_offset_seconds=25_200,
)

RIYADH = Site(
    key="riyadh",
    name="Riyadh",
    country="Saudi Arabia",
    country_code="SA",
    admin1="Riyadh",
    latitude=24.6877,
    longitude=46.7219,
    timezone="Asia/Riyadh",
    utc_offset_seconds=10_800,
)

#: The location matrix.
#:
#: **Test locations, not a supported-country whitelist.** TerraNex resolves farms
#: anywhere Open-Meteo does; these twelve were chosen to span hemispheres, latitudes
#: and UTC offsets.
#:
#: `timezone` is the authoritative location timezone — the IANA zone, and the value
#: these tests assert on. `utc_offset_seconds` is the **standard-time** offset, used
#: only to shape the payloads; this fixture deliberately does not model DST. Aswan is
#: the one site where that differs from the real world seasonally (Egypt observes DST
#: from April to October), which is immaterial here because P1-2 tests provider and
#: location plumbing, not clock arithmetic.
SITES: tuple[Site, ...] = (
    RIBEIRAO_PRETO,
    KRASNODAR,
    MURMANSK,
    NASHIK,
    ZHENGZHOU,
    BLOEMFONTEIN,
    ASWAN,
    BAHIR_DAR,
    SHIRAZ,
    AL_AIN,
    MEDAN,
    RIYADH,
)


# --------------------------------------------------------------------------
# Weather payloads
#
# The weather *values* are identical for every site, on purpose. Only coordinates,
# timezone and offset vary, which is exactly the surface P1-2 is testing.
# --------------------------------------------------------------------------


def daily_payload_for(
    site: Site,
    days: int = CANONICAL_TOTAL,
    start: date | None = None,
    *,
    include_offset: bool = True,
    timezone_abbreviation: str | None = None,
) -> dict:
    """A daily block for `site`, spanning the canonical window.

    `timezone_abbreviation` is emitted only when a caller supplies one. Open-Meteo
    always sends it, but nothing in the parser reads it and this fixture has no
    verified value for most of these zones — deriving `GMT+2` for Africa/Cairo would
    be invented data wearing an authoritative face. Only the Nashik wrapper passes a
    literal, and it is the real one.
    """
    first = start or (date.today() - timedelta(days=CANONICAL_PAST_DAYS))
    times = [(first + timedelta(days=i)).isoformat() for i in range(days)]
    payload: dict = {
        "latitude": site.latitude,
        "longitude": site.longitude,
        "timezone": site.timezone,
    }
    if timezone_abbreviation is not None:
        payload["timezone_abbreviation"] = timezone_abbreviation
    payload["daily_units"] = {"temperature_2m_max": "°C", "precipitation_sum": "mm"}
    payload["daily"] = {
        "time": times,
        "temperature_2m_max": [33.4 + (i % 3) for i in range(days)],
        "temperature_2m_min": [21.1 + (i % 3) for i in range(days)],
        "temperature_2m_mean": [27.2 + (i % 3) for i in range(days)],
        "relative_humidity_2m_mean": [64 + (i % 5) for i in range(days)],
        "precipitation_sum": [0.0 if i % 4 else 12.5 for i in range(days)],
        "precipitation_hours": [0.0 if i % 4 else 3.0 for i in range(days)],
        "wind_speed_10m_max": [14.2 for _ in range(days)],
        "et0_fao_evapotranspiration": [5.42 for _ in range(days)],
        "shortwave_radiation_sum": [21.3 for _ in range(days)],
        "cloud_cover_mean": [40 for _ in range(days)],
        "weather_code": [61 if i % 4 == 0 else 1 for i in range(days)],
    }
    if include_offset:
        # Present in every real response; the parser reads it to convert the local
        # naive timestamps that `timezone=auto` produces.
        payload["utc_offset_seconds"] = site.utc_offset_seconds
    return payload


def hourly_payload_for(
    site: Site,
    hours: int = 72,
    *,
    include_offset: bool = True,
) -> dict:
    """An hourly block plus a current block for `site`, as the near-term request
    returns."""
    base = date.today()
    times = [
        (f"{(base + timedelta(days=h // 24)).isoformat()}T{h % 24:02d}:00") for h in range(hours)
    ]
    payload = {
        "latitude": site.latitude,
        "longitude": site.longitude,
        "timezone": site.timezone,
        "current": {
            "time": f"{base.isoformat()}T09:00",
            "temperature_2m": 28.6,
            "relative_humidity_2m": 71,
            "apparent_temperature": 31.2,
            "precipitation": 0.4,
            "wind_speed_10m": 11.5,
            "wind_direction_10m": 245,
            "cloud_cover": 55,
            "pressure_msl": 1008.4,
            "weather_code": 61,
        },
        "hourly": {
            "time": times,
            "temperature_2m": [26.0 + (h % 6) for h in range(hours)],
            "relative_humidity_2m": [70 + (h % 10) for h in range(hours)],
            "precipitation": [0.1 for _ in range(hours)],
            "wind_speed_10m": [10.0 for _ in range(hours)],
            "et0_fao_evapotranspiration": [0.22 for _ in range(hours)],
            "shortwave_radiation": [420.0 for _ in range(hours)],
            "soil_moisture_0_to_1cm": [0.24 for _ in range(hours)],
            "soil_temperature_0cm": [25.5 for _ in range(hours)],
        },
    }
    if include_offset:
        payload["utc_offset_seconds"] = site.utc_offset_seconds
    return payload


def daily_payload(days: int = CANONICAL_TOTAL, start: date | None = None) -> dict:
    """A daily block spanning the canonical window, at Nashik.

    `utc_offset_seconds` is omitted, as it always has been: the timezone tests in
    `tests/providers/test_weather.py` add it explicitly to demonstrate the conversion,
    and several assert the *absent-offset* behaviour. Keeping this wrapper's output
    byte-identical is what lets the location-parameterised builders be added without
    touching any existing test.
    """
    return daily_payload_for(NASHIK, days, start, include_offset=False, timezone_abbreviation="IST")


def hourly_payload(hours: int = 72) -> dict:
    """An hourly block plus a current block, as the near-term request returns."""
    return hourly_payload_for(NASHIK, hours, include_offset=False)


# --------------------------------------------------------------------------
# Geocoding payloads
# --------------------------------------------------------------------------


def geocode_response(site: Site) -> dict:
    """An Open-Meteo `/v1/search` payload resolving to `site`.

    `id`, `elevation` and `population` are omitted rather than invented: the parser
    reads none of them, and this fixture only carries values that are genuinely known
    for each place.
    """
    return {
        "results": [
            {
                "name": site.name,
                "latitude": site.latitude,
                "longitude": site.longitude,
                "feature_code": "PPL",
                "country_code": site.country_code,
                "country": site.country,
                "admin1": site.admin1,
                "timezone": site.timezone,
            }
        ],
        "generationtime_ms": 0.6,
    }


# --------------------------------------------------------------------------
# respx routes
#
# Open-Meteo serves the daily and hourly documents from one path, distinguished only
# by the query, so both builders dispatch on `"daily" in request.url.params` — the
# same pattern the existing provider tests use.
# --------------------------------------------------------------------------


def site_of_request(request: httpx.Request) -> Site:
    """The site whose coordinates this request carries.

    Raises if the coordinates belong to no known site, which makes every matrix route
    an assertion in its own right: a request built from a place *name*, a rounded
    coordinate, or a hardcoded default would not match anything here.
    """
    latitude = float(request.url.params["latitude"])
    longitude = float(request.url.params["longitude"])
    for site in SITES:
        if abs(site.latitude - latitude) < 1e-9 and abs(site.longitude - longitude) < 1e-9:
            return site
    raise AssertionError(
        f"weather was requested for ({latitude}, {longitude}), which is none of the "
        "matrix locations — something re-derived the position"
    )


def matrix_weather_route():
    """Mock the forecast endpoint for every site at once, keyed on the coordinates
    the request actually carries."""

    def responder(request: httpx.Request) -> httpx.Response:
        site = site_of_request(request)
        if "daily" in request.url.params:
            return httpx.Response(200, json=daily_payload_for(site))
        return httpx.Response(200, json=hourly_payload_for(site))

    return respx.get(FORECAST_URL).mock(side_effect=responder)


def geocoding_route(site: Site):
    """Mock the geocoding endpoint for one site."""
    return respx.get(GEOCODING_URL).mock(
        return_value=httpx.Response(200, json=geocode_response(site))
    )
