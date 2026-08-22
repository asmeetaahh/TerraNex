"""Shared pytest fixtures.

Tests never touch the network and never call a model: Phase 3 data comes entirely
from the deterministic simulator, and the AI layer is pinned to mock mode.
"""

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]

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
os.environ["SOIL_PROVIDER"] = "simulated"
# The default suite runs with no database at all, on the in-memory store. Tests that
# need one create it themselves through the `sqlite_db` fixture below, and the
# Postgres-marked subset is skipped unless TEST_DATABASE_URL is set. A DATABASE_URL
# exported in a developer's shell must not silently repoint the whole suite at a real
# database, so it is cleared rather than defaulted.
os.environ.pop("DATABASE_URL", None)

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


# --------------------------------------------------------------------------
# Database fixtures
#
# The suite's default is no database: the 471 tests that predate persistence run on
# the in-memory store and must keep doing so, offline and with nothing provisioned.
# Everything below is opt-in per test.
#
# Two tiers, as agreed for this phase:
#
# * `sqlite_db` — a throwaway file per test. Fast, needs nothing, and covers every
#   behaviour whose SQL is portable.
# * `postgres_db` — a real Postgres, skipped unless TEST_DATABASE_URL is set. It
#   exists for the things SQLite genuinely cannot answer: enforced foreign keys,
#   native uuid and numeric types, and real transactional isolation.
#
# The `postgres` marker is registered and skipped here rather than in pyproject.toml,
# so no packaging file changes for a test concern.
# --------------------------------------------------------------------------

TEST_DATABASE_URL_ENV = "TEST_DATABASE_URL"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "postgres: requires a live PostgreSQL instance (set TEST_DATABASE_URL to run)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip the Postgres tier unless a database is offered.

    Skipping rather than failing is deliberate: `uv run pytest` must stay green on a
    laptop with nothing installed, or the fast tier stops being run.
    """
    if os.environ.get(TEST_DATABASE_URL_ENV):
        return

    skip = pytest.mark.skip(reason=f"set {TEST_DATABASE_URL_ENV} to run the PostgreSQL tier")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip)


def _migrate(database_url: str) -> None:
    """Build the schema with Alembic, not `create_all`.

    Deployments only ever see the migration, so the tests should exercise the same
    path; `create_all` would let the migration drift without anything noticing.
    """
    from alembic.config import Config

    from alembic import command
    from app.core.config import settings as live_settings

    previous = live_settings.DATABASE_URL
    live_settings.DATABASE_URL = database_url
    try:
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
        command.upgrade(config, "head")
    finally:
        live_settings.DATABASE_URL = previous


@contextmanager
def _database_at(url: str) -> Iterator[str]:
    """Point the application at `url` for the duration of a test, then restore."""
    from app.db import session as session_module

    previous = settings.DATABASE_URL
    session_module.dispose_engine()
    settings.DATABASE_URL = url
    try:
        yield url
    finally:
        settings.DATABASE_URL = previous
        session_module.dispose_engine()


@pytest.fixture
def sqlite_db(tmp_path) -> Iterator[str]:
    """A migrated SQLite database, wired in as the application's database."""
    url = f"sqlite:///{tmp_path / 'terranex.db'}"
    _migrate(url)
    with _database_at(url) as active:
        # Seed the catalog exactly as the application's lifespan does, so a database
        # test starts from the same state a booted process would.
        seed_crops()
        yield active


@pytest.fixture
def postgres_db() -> Iterator[str]:
    """A migrated PostgreSQL database, from TEST_DATABASE_URL.

    The schema is dropped and rebuilt per test so cases cannot leak into one another.
    """
    from sqlalchemy import create_engine, text

    url = os.environ[TEST_DATABASE_URL_ENV]

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    _migrate(url)
    with _database_at(url) as active:
        seed_crops()
        yield active
