"""Shared pytest fixtures.

Tests never touch the network and never call a model: Phase 3 data comes entirely
from the deterministic simulator, and the AI layer is pinned to mock mode.
"""

import os
from collections.abc import AsyncIterator

import pytest

# Must be set before app.core.config is imported anywhere.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("ENABLE_AUTH", "false")
os.environ.setdefault("SEED_DEMO_DATA", "false")
# Pin both providers to the simulator so the suite is offline and deterministic.
# Assigned unconditionally rather than with setdefault: the code default is now
# `open_meteo`, so a developer with WEATHER_PROVIDER exported in their shell would
# otherwise have the whole suite silently hit the network. Tests that exercise the
# real provider opt in with `monkeypatch.setattr(settings, ...)` and mock the
# transport with respx, so no test ever reaches out.
os.environ["WEATHER_PROVIDER"] = "simulated"
os.environ["GEOCODING_PROVIDER"] = "simulated"

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.memory import store as memory_store  # noqa: E402
from app.db.seed import seed_crops  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.providers.cache import clear_all_caches  # noqa: E402

# A real place, used so simulated values are anchored somewhere plausible.
DEMO_FARM = {
    "name": "North Field",
    "latitude": -1.2864,
    "longitude": 36.8172,
    "country_code": "KE",
    "region": "Nairobi County",
    "area_hectares": 12.5,
}


@pytest.fixture(scope="session")
def app():
    return fastapi_app


@pytest.fixture(autouse=True)
def clean_store():
    """Isolate every test.

    The Phase 3 store is a process-level singleton, so without this a farm created
    by one test would leak into the next. The crop catalog is re-seeded rather than
    cleared, since it is immutable reference data.
    """
    seed_crops()
    memory_store.reset()
    # Provider caches are process-level; a hit from a previous test would mask a
    # missing respx mock and make results order-dependent.
    clear_all_caches()
    yield
    memory_store.reset()
    clear_all_caches()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    """An HTTP client wired straight to the ASGI app — no server, no sockets."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.fixture
def api_prefix() -> str:
    return settings.API_V1_PREFIX


@pytest.fixture
async def farm(client, api_prefix) -> dict:
    """A registered farm."""
    resp = await client.post(f"{api_prefix}/farms", json=DEMO_FARM)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
async def maize_crop(client, api_prefix) -> dict:
    """The maize entry from the reference catalog."""
    resp = await client.get(f"{api_prefix}/reference/crops", params={"page_size": 200})
    assert resp.status_code == 200
    return next(c for c in resp.json()["items"] if c["code"] == "maize")


@pytest.fixture
async def planted_farm(client, api_prefix, farm, maize_crop) -> dict:
    """A farm with a primary maize planting — the state most endpoints assume."""
    resp = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crops",
        json={
            "crop_id": maize_crop["id"],
            "growth_stage": "flowering",
            "is_primary": True,
            "planting_date": "2026-04-01",
            "expected_harvest_date": "2026-09-01",
            "status": "growing",
        },
    )
    assert resp.status_code == 201, resp.text
    return farm


@pytest.fixture
async def analyzed_farm(client, api_prefix, planted_farm) -> dict:
    """A farm with one completed analysis run."""
    resp = await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")
    assert resp.status_code == 200, resp.text
    return planted_farm


JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 64 + b"\xff\xd9"
