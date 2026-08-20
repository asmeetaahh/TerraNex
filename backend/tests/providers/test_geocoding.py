"""Open-Meteo geocoding: real coordinates, or nothing at all.

Every test here mocks the transport with respx. Nothing touches the network.
"""

import httpx
import pytest
import respx

from app.providers import geocoding
from app.providers.geocoding import OPEN_METEO_URL
from app.schemas.common import DataMode

# Trimmed from a real Open-Meteo geocoding response. Nashik is a genuine
# agricultural centre in Maharashtra; the coordinates below are its real ones.
NASHIK_RESPONSE = {
    "results": [
        {
            "id": 1261731,
            "name": "Nashik",
            "latitude": 19.99727,
            "longitude": 73.79096,
            "elevation": 565.0,
            "feature_code": "PPLA2",
            "country_code": "IN",
            "country": "India",
            "admin1": "Maharashtra",
            "admin2": "Nashik",
            "timezone": "Asia/Kolkata",
            "population": 1289497,
        },
        {
            "id": 1261732,
            "name": "Nashik Division",
            "latitude": 20.25,
            "longitude": 74.0,
            "elevation": 500.0,
            "feature_code": "ADM2",
            "country_code": "IN",
            "country": "India",
            "admin1": "Maharashtra",
            "timezone": "Asia/Kolkata",
        },
    ],
    "generationtime_ms": 0.9,
}

# Open-Meteo omits `results` entirely when nothing matches.
NO_MATCH_RESPONSE = {"generationtime_ms": 0.4}


@pytest.fixture(autouse=True)
def use_open_meteo(monkeypatch):
    monkeypatch.setattr(geocoding.settings, "GEOCODING_PROVIDER", "open_meteo")


@respx.mock
async def test_indian_location_resolves_to_real_coordinates() -> None:
    respx.get(OPEN_METEO_URL).mock(return_value=httpx.Response(200, json=NASHIK_RESPONSE))

    result = await geocoding.search("Nashik", limit=10)

    assert result.ok
    assert result.meta.mode is DataMode.live
    top = result.data[0]
    assert top.name == "Nashik"
    # Exactly what the provider returned — not rounded, not adjusted.
    assert top.latitude == 19.99727
    assert top.longitude == 73.79096
    assert top.country == "India"
    assert top.country_code == "IN"
    assert top.region == "Maharashtra"
    assert top.elevation_m == 565.0
    assert top.display_name == "Nashik, Maharashtra, India"


@respx.mock
async def test_multiple_candidates_are_returned_in_provider_order() -> None:
    respx.get(OPEN_METEO_URL).mock(return_value=httpx.Response(200, json=NASHIK_RESPONSE))

    result = await geocoding.search("Nashik", limit=10)

    assert len(result.data) == 2
    assert [c.name for c in result.data] == ["Nashik", "Nashik Division"]


@respx.mock
async def test_query_is_sent_to_the_provider_verbatim() -> None:
    route = respx.get(OPEN_METEO_URL).mock(return_value=httpx.Response(200, json=NASHIK_RESPONSE))

    await geocoding.search("Nashik", limit=5)

    request = route.calls.last.request
    assert request.url.params["name"] == "Nashik"
    assert request.url.params["count"] == "5"


@respx.mock
async def test_no_match_returns_empty_and_invents_nothing() -> None:
    """The core guarantee: an unresolvable place produces no coordinates."""
    respx.get(OPEN_METEO_URL).mock(return_value=httpx.Response(200, json=NO_MATCH_RESPONSE))

    result = await geocoding.search("Zzyzx Hollow", limit=10)

    assert result.ok
    assert result.data == []
    assert "no match" in (result.meta.note or "").lower()


@respx.mock
async def test_entries_without_coordinates_are_skipped_not_guessed() -> None:
    respx.get(OPEN_METEO_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"name": "Broken", "country": "Nowhere"},
                    {"name": "Pune", "latitude": 18.52043, "longitude": 73.85674},
                ]
            },
        )
    )

    result = await geocoding.search("x", limit=10)

    assert [c.name for c in result.data] == ["Pune"]


@respx.mock
async def test_provider_failure_is_unavailable_not_fabricated() -> None:
    respx.get(OPEN_METEO_URL).mock(return_value=httpx.Response(503))

    result = await geocoding.search("Nashik", limit=10)

    assert result.ok is False
    assert result.data is None
    assert result.meta.mode is DataMode.unavailable
    assert "no coordinates were guessed" in (result.meta.note or "").lower()


@respx.mock
async def test_timeout_is_unavailable() -> None:
    respx.get(OPEN_METEO_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))

    result = await geocoding.search("Nashik", limit=10)

    assert result.ok is False
    assert result.meta.mode is DataMode.unavailable


@respx.mock
async def test_second_lookup_is_served_from_cache() -> None:
    route = respx.get(OPEN_METEO_URL).mock(return_value=httpx.Response(200, json=NASHIK_RESPONSE))

    first = await geocoding.search("Nashik", limit=10)
    second = await geocoding.search("Nashik", limit=10)

    assert route.call_count == 1, "the second lookup should not hit the provider"
    assert first.meta.mode is DataMode.live
    assert second.meta.mode is DataMode.cached
    assert first.data[0].latitude == second.data[0].latitude


@respx.mock
async def test_failures_are_not_cached() -> None:
    """A provider down for one request must be retried on the next, not remembered
    as broken for the whole TTL."""
    route = respx.get(OPEN_METEO_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json=NASHIK_RESPONSE),
        ]
    )

    failed = await geocoding.search("Nashik", limit=10)
    recovered = await geocoding.search("Nashik", limit=10)

    assert failed.ok is False
    assert recovered.ok is True
    assert route.call_count == 4  # 3 retry attempts, then the successful retry


# --------------------------------------------------------------------------
# Simulated provider
# --------------------------------------------------------------------------


async def test_simulated_provider_matches_only_real_places(monkeypatch) -> None:
    monkeypatch.setattr(geocoding.settings, "GEOCODING_PROVIDER", "simulated")

    known = await geocoding.search("Pune", limit=10)
    unknown = await geocoding.search("Zzyzx Hollow", limit=10)

    assert known.meta.mode is DataMode.simulated
    assert known.data[0].latitude == pytest.approx(18.5204)
    # No synthesis path exists.
    assert unknown.data == []


async def test_simulated_provider_never_reaches_the_network(monkeypatch) -> None:
    """respx with no routes raises on any request, proving the simulated path is
    entirely offline."""
    monkeypatch.setattr(geocoding.settings, "GEOCODING_PROVIDER", "simulated")

    with respx.mock(assert_all_called=False):
        result = await geocoding.search("Nairobi", limit=5)

    assert result.data
    assert result.meta.mode is DataMode.simulated


# --------------------------------------------------------------------------
# Configuration defaults
# --------------------------------------------------------------------------


def test_provider_defaults_are_simulated() -> None:
    """An unconfigured process must make no outbound calls.

    Open-Meteo is opted into explicitly through the environment; if this default
    ever flips back, a fresh clone or a CI job would start hitting the network.
    """
    from app.core.config import Settings

    defaults = Settings(_env_file=None)

    assert defaults.WEATHER_PROVIDER == "simulated"
    assert defaults.GEOCODING_PROVIDER == "simulated"


def test_no_provider_requires_credentials() -> None:
    """Open-Meteo needs no API key, and nothing in the provider layer reads one."""
    from pathlib import Path

    provider_dir = Path(__file__).resolve().parents[2] / "app" / "providers"
    for source in provider_dir.glob("*.py"):
        text = source.read_text().lower()
        assert "api_key" not in text, f"{source.name} references an API key"
        assert "apikey" not in text, f"{source.name} references an API key"
