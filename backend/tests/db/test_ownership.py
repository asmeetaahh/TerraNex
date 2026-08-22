"""Farm ownership, persisted.

`tests/api/test_auth.py` proves isolation on the in-memory path. These prove it holds
in SQL — the `WHERE user_id = ?` is real, the `users` row is written on first sight of
a token, and ownership survives a restart.
"""

import time

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core import security
from app.core.config import settings
from app.core.deps import demo_user
from app.db.session import session_scope
from app.models import FarmORM, UserORM

SECRET = "a-test-signing-secret-not-a-real-one"
FARM = {"name": "Owned Field", "latitude": -1.2864, "longitude": 36.8172}


def token(subject: str, email: str = "a@example.test") -> str:
    return jwt.encode(
        {
            "sub": subject,
            "email": email,
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        SECRET,
        algorithm="HS256",
    )


def bearer(subject: str, **kw) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(subject, **kw)}"}


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_AUTH", True)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setattr(settings, "SUPABASE_URL", None)
    security.reset_jwks_client()
    yield
    security.reset_jwks_client()


# --------------------------------------------------------------------------
# The users table
# --------------------------------------------------------------------------


async def test_a_first_request_creates_the_user_row(
    sqlite_db, auth_on, client: AsyncClient, api_prefix: str
) -> None:
    await client.get(f"{api_prefix}/farms", headers=bearer("alice", email="alice@example.test"))

    with session_scope() as db:
        row = db.scalar(select(UserORM).where(UserORM.auth_id == "alice"))

    assert row is not None
    assert row.email == "alice@example.test"


async def test_repeated_requests_reuse_one_row(
    sqlite_db, auth_on, client: AsyncClient, api_prefix: str
) -> None:
    """Two rows for one account would split a user's farms in half."""
    for _ in range(3):
        await client.get(f"{api_prefix}/farms", headers=bearer("alice"))

    with session_scope() as db:
        assert db.query(UserORM).filter(UserORM.auth_id == "alice").count() == 1


async def test_a_changed_email_is_refreshed(
    sqlite_db, auth_on, client: AsyncClient, api_prefix: str
) -> None:
    await client.get(f"{api_prefix}/farms", headers=bearer("alice", email="old@example.test"))
    await client.get(f"{api_prefix}/farms", headers=bearer("alice", email="new@example.test"))

    with session_scope() as db:
        row = db.scalar(select(UserORM).where(UserORM.auth_id == "alice"))

    assert row.email == "new@example.test"
    with session_scope() as db:
        assert db.query(UserORM).count() == 1


async def test_no_token_material_is_stored(
    sqlite_db, auth_on, client: AsyncClient, api_prefix: str
) -> None:
    """`auth_id` is a public subject identifier. Nothing else from the token is kept."""
    raw = token("alice")
    await client.get(f"{api_prefix}/farms", headers={"Authorization": f"Bearer {raw}"})

    with session_scope() as db:
        row = db.scalar(select(UserORM).where(UserORM.auth_id == "alice"))

    stored = " ".join(str(v) for v in vars(row).values() if v is not None)
    assert raw not in stored
    assert SECRET not in stored


# --------------------------------------------------------------------------
# Ownership in SQL
# --------------------------------------------------------------------------


async def test_a_farm_row_records_its_owner(
    sqlite_db, auth_on, client: AsyncClient, api_prefix: str
) -> None:
    created = await client.post(f"{api_prefix}/farms", json=FARM, headers=bearer("alice"))
    assert created.status_code == 201, created.text

    with session_scope() as db:
        farm = db.query(FarmORM).one()
        owner = db.scalar(select(UserORM).where(UserORM.auth_id == "alice"))

    assert farm.user_id == owner.id


async def test_listing_is_scoped_in_sql(
    sqlite_db, auth_on, client: AsyncClient, api_prefix: str
) -> None:
    await client.post(f"{api_prefix}/farms", json={**FARM, "name": "A"}, headers=bearer("alice"))
    await client.post(f"{api_prefix}/farms", json={**FARM, "name": "B1"}, headers=bearer("bob"))
    await client.post(f"{api_prefix}/farms", json={**FARM, "name": "B2"}, headers=bearer("bob"))

    alice = (await client.get(f"{api_prefix}/farms", headers=bearer("alice"))).json()
    bob = (await client.get(f"{api_prefix}/farms", headers=bearer("bob"))).json()

    assert [f["name"] for f in alice["items"]] == ["A"]
    assert [f["name"] for f in bob["items"]] == ["B1", "B2"]
    with session_scope() as db:
        assert db.query(FarmORM).count() == 3, "all three rows exist; only the view is scoped"


async def test_another_users_farm_is_not_reachable(
    sqlite_db, auth_on, client: AsyncClient, api_prefix: str
) -> None:
    farm = (await client.post(f"{api_prefix}/farms", json=FARM, headers=bearer("alice"))).json()

    assert (
        await client.get(f"{api_prefix}/farms/{farm['id']}", headers=bearer("bob"))
    ).status_code == 404
    assert (
        await client.patch(
            f"{api_prefix}/farms/{farm['id']}", json={"name": "Stolen"}, headers=bearer("bob")
        )
    ).status_code == 404
    assert (
        await client.delete(f"{api_prefix}/farms/{farm['id']}", headers=bearer("bob"))
    ).status_code == 404

    with session_scope() as db:
        row = db.query(FarmORM).one()
        assert row.name == "Owned Field"
        assert row.deleted_at is None


async def test_ownership_survives_a_restart(
    sqlite_db, auth_on, client: AsyncClient, api_prefix: str
) -> None:
    from app.db import session as session_module

    created = await client.post(f"{api_prefix}/farms", json=FARM, headers=bearer("alice"))
    farm_id = created.json()["id"]

    session_module.dispose_engine()

    assert (
        await client.get(f"{api_prefix}/farms/{farm_id}", headers=bearer("alice"))
    ).status_code == 200
    assert (
        await client.get(f"{api_prefix}/farms/{farm_id}", headers=bearer("bob"))
    ).status_code == 404


# --------------------------------------------------------------------------
# Demo mode against a database
# --------------------------------------------------------------------------


async def test_the_demo_user_owns_demo_farms(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """Auth off, database on — the combination local development runs in."""
    assert settings.ENABLE_AUTH is False

    created = await client.post(f"{api_prefix}/farms", json=FARM)
    assert created.status_code == 201, created.text

    with session_scope() as db:
        farm = db.query(FarmORM).one()
        assert farm.user_id == demo_user().id
        # The owner row must exist, or the foreign key would have nothing to point at.
        assert db.get(UserORM, demo_user().id) is not None

    listed = (await client.get(f"{api_prefix}/farms")).json()
    assert listed["total"] == 1
