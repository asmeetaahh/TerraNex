"""Shared pytest fixtures.

Tests never touch the network: external providers are replayed from recorded
fixtures via respx, and the AI layer is forced into mock mode.
"""

import os
from collections.abc import AsyncIterator

import pytest

# Must be set before app.core.config is imported anywhere.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("ENABLE_AUTH", "false")
os.environ.setdefault("SEED_DEMO_DATA", "false")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return fastapi_app


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
