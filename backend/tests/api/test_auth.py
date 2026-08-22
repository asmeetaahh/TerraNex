"""Supabase JWT authentication and farm ownership.

Tokens here are minted locally with the same library that verifies them, and JWKS is
served from an in-process key pair through respx. Nothing contacts Supabase, so these
run offline like the rest of the suite.

Two properties matter most and are easy to get subtly wrong:

* **a token that cannot be verified is never accepted** — not when the signature is
  wrong, not when it has expired, not when the algorithm is `none`, and not when the
  key server is unreachable. A network failure must read as "cannot verify", never as
  "fine",
* **one user cannot see or touch another's farm**, and the refusal looks the same as a
  farm that does not exist.
"""

import time
from datetime import UTC, datetime

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from jwt.utils import base64url_encode

from app.core import security
from app.core.config import settings
from app.core.deps import demo_user
from app.core.errors import UnauthorizedError

SUPABASE_URL = "https://project.supabase.test"
JWKS_URL = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
SECRET = "a-test-signing-secret-not-a-real-one"

FARM = {"name": "Owned Field", "latitude": -1.2864, "longitude": 36.8172}


# --------------------------------------------------------------------------
# Token minting — the test's own key material, never a real credential
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(key: rsa.RSAPrivateKey, kid: str = "test-key") -> dict:
    numbers = key.public_key().public_numbers()

    def b64(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64url_encode(raw).decode()

    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": b64(numbers.n),
                "e": b64(numbers.e),
            }
        ]
    }


def hs256_token(subject: str = "user-1", email: str = "a@example.test", **overrides) -> str:
    claims = {
        "sub": subject,
        "email": email,
        "aud": "authenticated",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        **overrides,
    }
    return jwt.encode(claims, SECRET, algorithm="HS256")


def rs256_token(key: rsa.RSAPrivateKey, subject: str = "user-1", **overrides) -> str:
    claims = {
        "sub": subject,
        "email": "a@example.test",
        "aud": "authenticated",
        "exp": int(time.time()) + 3600,
        **overrides,
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


@pytest.fixture
def auth_on(monkeypatch):
    """Authentication enabled, HS256 shared secret configured, JWKS unavailable."""
    monkeypatch.setattr(settings, "ENABLE_AUTH", True)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setattr(settings, "SUPABASE_URL", None)
    security.reset_jwks_client()
    yield
    security.reset_jwks_client()


@pytest.fixture
def auth_on_jwks(monkeypatch, rsa_key):
    """Authentication enabled against a JWKS endpoint served in-process."""
    monkeypatch.setattr(settings, "ENABLE_AUTH", True)
    monkeypatch.setattr(settings, "SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", None)
    security.reset_jwks_client()
    yield rsa_key
    security.reset_jwks_client()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------
# Rejecting what cannot be verified
# --------------------------------------------------------------------------


async def test_a_missing_token_is_unauthorized(
    auth_on, client: AsyncClient, api_prefix: str
) -> None:
    response = await client.get(f"{api_prefix}/farms")

    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "UNAUTHORIZED"
    assert error["request_id"]


@pytest.mark.parametrize(
    ("header", "label"),
    [
        ("", "empty"),
        ("Bearer", "scheme only"),
        ("Bearer ", "no credentials"),
        ("Basic abc123", "wrong scheme"),
        ("Bearer not-a-jwt", "not a jwt"),
        ("Bearer a.b.c", "three junk segments"),
    ],
)
async def test_a_malformed_header_is_unauthorized(
    auth_on, client: AsyncClient, api_prefix: str, header: str, label: str
) -> None:
    response = await client.get(f"{api_prefix}/farms", headers={"Authorization": header})

    assert response.status_code == 401, f"{label} was accepted"
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_a_token_signed_with_the_wrong_secret_is_rejected(
    auth_on, client: AsyncClient, api_prefix: str
) -> None:
    forged = jwt.encode(
        {"sub": "intruder", "aud": "authenticated", "exp": int(time.time()) + 3600},
        "a-different-secret-of-sufficient-length-to-not-warn",
        algorithm="HS256",
    )

    response = await client.get(f"{api_prefix}/farms", headers=bearer(forged))

    assert response.status_code == 401


async def test_an_expired_token_is_rejected(auth_on, client: AsyncClient, api_prefix: str) -> None:
    expired = hs256_token(exp=int(time.time()) - 60)

    response = await client.get(f"{api_prefix}/farms", headers=bearer(expired))

    assert response.status_code == 401


async def test_a_token_for_another_audience_is_rejected(
    auth_on, client: AsyncClient, api_prefix: str
) -> None:
    response = await client.get(f"{api_prefix}/farms", headers=bearer(hs256_token(aud="other")))

    assert response.status_code == 401


def test_an_unsigned_token_is_rejected(auth_on) -> None:
    """`alg: none` is the classic JWT forgery. It must never verify."""
    unsigned = jwt.encode({"sub": "intruder", "aud": "authenticated"}, key="", algorithm="none")

    with pytest.raises(UnauthorizedError):
        security.verify_token(unsigned)


def test_a_token_with_no_subject_is_rejected(auth_on) -> None:
    """Without `sub` there is no identity to own anything."""
    with pytest.raises(UnauthorizedError):
        security.verify_token(hs256_token(sub=""))


# --------------------------------------------------------------------------
# Accepting what verifies
# --------------------------------------------------------------------------


async def test_a_valid_token_is_accepted(auth_on, client: AsyncClient, api_prefix: str) -> None:
    response = await client.get(f"{api_prefix}/farms", headers=bearer(hs256_token()))

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_claims_are_extracted_from_a_verified_token(auth_on) -> None:
    claims = security.verify_token(hs256_token(subject="abc-123", email="farmer@example.test"))

    assert claims.subject == "abc-123"
    assert claims.email == "farmer@example.test"
    assert claims.raw["aud"] == "authenticated"


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
async def test_the_bearer_scheme_is_case_insensitive(
    auth_on, client: AsyncClient, api_prefix: str, scheme: str
) -> None:
    """RFC 7235 says the scheme is case-insensitive; a client that sends `bearer`
    is not malformed."""
    response = await client.get(
        f"{api_prefix}/farms", headers={"Authorization": f"{scheme} {hs256_token()}"}
    )

    assert response.status_code == 200


# --------------------------------------------------------------------------
# JWKS
# --------------------------------------------------------------------------


@respx.mock
async def test_an_rs256_token_verifies_against_jwks(
    auth_on_jwks, client: AsyncClient, api_prefix: str
) -> None:
    key = auth_on_jwks
    route = respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=_jwks(key)))

    response = await client.get(f"{api_prefix}/farms", headers=bearer(rs256_token(key)))

    assert response.status_code == 200, response.text
    assert route.called, "the JWKS endpoint was never consulted"


@respx.mock
async def test_the_key_set_is_cached_across_requests(
    auth_on_jwks, client: AsyncClient, api_prefix: str
) -> None:
    """A key set changes only on rotation. Fetching it per request would put a network
    round trip in front of every authenticated call."""
    key = auth_on_jwks
    route = respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=_jwks(key)))
    token = rs256_token(key)

    for _ in range(3):
        assert (await client.get(f"{api_prefix}/farms", headers=bearer(token))).status_code == 200

    assert route.call_count == 1


@respx.mock
async def test_an_unreachable_key_server_rejects_rather_than_admits(
    auth_on_jwks, client: AsyncClient, api_prefix: str
) -> None:
    """The failure mode that matters: if the key cannot be fetched, the token cannot be
    verified, so it must not be honoured."""
    key = auth_on_jwks
    respx.get(JWKS_URL).mock(return_value=httpx.Response(503))

    response = await client.get(f"{api_prefix}/farms", headers=bearer(rs256_token(key)))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_an_rs256_token_without_a_configured_project_is_rejected(
    auth_on, client: AsyncClient, api_prefix: str, rsa_key
) -> None:
    """`auth_on` leaves SUPABASE_URL unset, so there is nowhere to fetch a key from."""
    response = await client.get(f"{api_prefix}/farms", headers=bearer(rs256_token(rsa_key)))

    assert response.status_code == 401


def test_the_jwks_url_follows_supabase_layout(auth_on_jwks) -> None:
    assert security.jwks_url() == JWKS_URL


# --------------------------------------------------------------------------
# Ownership isolation
# --------------------------------------------------------------------------


async def test_creating_a_farm_assigns_the_authenticated_user(
    auth_on, client: AsyncClient, api_prefix: str
) -> None:
    from app.db.memory import store
    from app.db.user_repo import local_id_for

    created = await client.post(f"{api_prefix}/farms", json=FARM, headers=bearer(hs256_token()))

    assert created.status_code == 201, created.text
    # Ownership is server-side state, deliberately absent from the response contract.
    assert "user_id" not in created.json()
    stored = store.farms[__import__("uuid").UUID(created.json()["id"])]
    assert stored.user_id == local_id_for("user-1")


async def test_a_client_cannot_choose_its_own_owner(
    auth_on, client: AsyncClient, api_prefix: str
) -> None:
    """`FarmCreate` has no `user_id`; an extra key must not become one."""
    from app.db.memory import store
    from app.db.user_repo import local_id_for

    created = await client.post(
        f"{api_prefix}/farms",
        json={**FARM, "user_id": str(local_id_for("someone-else"))},
        headers=bearer(hs256_token()),
    )

    assert created.status_code == 201
    stored = store.farms[__import__("uuid").UUID(created.json()["id"])]
    assert stored.user_id == local_id_for("user-1")


async def test_one_user_cannot_list_anothers_farms(
    auth_on, client: AsyncClient, api_prefix: str
) -> None:
    alice, bob = hs256_token("alice"), hs256_token("bob")
    await client.post(f"{api_prefix}/farms", json={**FARM, "name": "Alice"}, headers=bearer(alice))
    await client.post(f"{api_prefix}/farms", json={**FARM, "name": "Bob"}, headers=bearer(bob))

    for token, expected in ((alice, "Alice"), (bob, "Bob")):
        listed = (await client.get(f"{api_prefix}/farms", headers=bearer(token))).json()
        assert [row["name"] for row in listed["items"]] == [expected]
        assert listed["total"] == 1


async def test_one_user_cannot_read_anothers_farm(
    auth_on, client: AsyncClient, api_prefix: str
) -> None:
    """404, not 403: a 403 would confirm the id is real."""
    alice, bob = hs256_token("alice"), hs256_token("bob")
    farm = (await client.post(f"{api_prefix}/farms", json=FARM, headers=bearer(alice))).json()

    response = await client.get(f"{api_prefix}/farms/{farm['id']}", headers=bearer(bob))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FARM_NOT_FOUND"


async def test_one_user_cannot_modify_anothers_farm(
    auth_on, client: AsyncClient, api_prefix: str
) -> None:
    alice, bob = hs256_token("alice"), hs256_token("bob")
    farm = (await client.post(f"{api_prefix}/farms", json=FARM, headers=bearer(alice))).json()

    patched = await client.patch(
        f"{api_prefix}/farms/{farm['id']}", json={"name": "Stolen"}, headers=bearer(bob)
    )
    deleted = await client.delete(f"{api_prefix}/farms/{farm['id']}", headers=bearer(bob))

    assert patched.status_code == 404
    assert deleted.status_code == 404

    # Untouched and still Alice's.
    still = (await client.get(f"{api_prefix}/farms/{farm['id']}", headers=bearer(alice))).json()
    assert still["name"] == "Owned Field"


async def test_one_user_cannot_reach_anothers_plantings(
    auth_on, client: AsyncClient, api_prefix: str
) -> None:
    alice, bob = hs256_token("alice"), hs256_token("bob")
    farm = (await client.post(f"{api_prefix}/farms", json=FARM, headers=bearer(alice))).json()
    catalog = (await client.get(f"{api_prefix}/reference/crops", params={"page_size": 5})).json()
    crop_id = catalog["items"][0]["id"]

    listed = await client.get(f"{api_prefix}/farms/{farm['id']}/crops", headers=bearer(bob))
    added = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crops", json={"crop_id": crop_id}, headers=bearer(bob)
    )

    assert listed.status_code == 404
    assert added.status_code == 404


# --------------------------------------------------------------------------
# Auth disabled — the mode the rest of the suite runs in
# --------------------------------------------------------------------------


async def test_no_token_is_needed_when_auth_is_disabled(
    client: AsyncClient, api_prefix: str
) -> None:
    assert settings.ENABLE_AUTH is False

    assert (await client.get(f"{api_prefix}/farms")).status_code == 200
    assert (await client.post(f"{api_prefix}/farms", json=FARM)).status_code == 201


async def test_a_token_is_ignored_when_auth_is_disabled(
    client: AsyncClient, api_prefix: str
) -> None:
    """Demo mode attributes every request to the demo user, whatever it carries — a
    stray token from a half-wired frontend must not change who owns a farm."""
    from app.db.memory import store

    created = await client.post(
        f"{api_prefix}/farms", json=FARM, headers=bearer("obvious-nonsense")
    )

    assert created.status_code == 201
    stored = store.farms[__import__("uuid").UUID(created.json()["id"])]
    assert stored.user_id == demo_user().id


async def test_demo_farms_belong_to_the_demo_user(client: AsyncClient, api_prefix: str) -> None:
    """Seeded demo farms must remain visible once ownership is enforced."""
    from app.db.memory import store
    from app.db.seed import seed_demo_farms

    seed_demo_farms()

    listed = (await client.get(f"{api_prefix}/farms")).json()

    assert listed["total"] >= 1
    assert all(f.user_id == demo_user().id for f in store.live_farms())


def test_the_demo_user_is_stable() -> None:
    """Derived rather than generated, so demo farms keep their owner across restarts."""
    assert demo_user().id == demo_user().id
    assert demo_user().is_demo is True
    assert demo_user().email == settings.DEMO_USER_EMAIL


# --------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------


def test_the_service_role_key_is_never_read_by_the_auth_path() -> None:
    """That key bypasses every policy in the project. It belongs to server-to-server
    storage calls, never to request authentication."""
    from pathlib import Path

    for module in ("security.py", "deps.py"):
        source = (Path(__file__).resolve().parents[2] / "app" / "core" / module).read_text()
        # The attribute access, not the name: both modules *document* why they do not
        # use this key, and a prose mention is the opposite of a leak.
        assert "settings.SUPABASE_SERVICE_ROLE_KEY" not in source
        assert "SERVICE_ROLE_KEY" not in source.replace("SUPABASE_SERVICE_ROLE_KEY", "")


async def test_an_error_never_echoes_the_token(
    auth_on, client: AsyncClient, api_prefix: str
) -> None:
    """A rejected token must not come back in the envelope, where it would be logged
    by whatever records the response."""
    token = hs256_token()
    response = await client.get(
        f"{api_prefix}/farms", headers={"Authorization": f"Bearer {token}x"}
    )

    assert response.status_code == 401
    assert token not in response.text
    assert SECRET not in response.text


def test_verification_is_not_attempted_without_a_configured_secret(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENABLE_AUTH", True)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", None)
    monkeypatch.setattr(settings, "SUPABASE_URL", None)
    security.reset_jwks_client()

    with pytest.raises(UnauthorizedError):
        security.verify_token(hs256_token())


def test_a_verified_token_is_not_trusted_before_verification() -> None:
    """The `alg` header steers which key is used, and nothing else. A token claiming
    HS256 is still checked against the secret."""
    forged = jwt.encode(
        {"sub": "x", "aud": "authenticated"},
        "some-other-secret-of-sufficient-length-here",
        algorithm="HS256",
    )

    assert security._unverified_algorithm(forged) == "HS256"
    with pytest.raises(UnauthorizedError):
        security.verify_token(forged)


def test_utc_now_is_used_for_expiry(auth_on) -> None:
    """Sanity check that expiry is evaluated against real time, not a frozen clock."""
    token = hs256_token(exp=int(datetime.now(UTC).timestamp()) + 5)

    assert security.verify_token(token).subject == "user-1"
