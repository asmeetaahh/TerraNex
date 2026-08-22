"""Cross-user isolation on every farm-scoped endpoint.

Phase 2b protected `/farms/*` and stopped there. Every other router — analysis,
recommendations, images, environment — called `require_farm(farm_id)` with no user, and
`require_farm` defaulted `user` to `None`, meaning "no ownership filter". The result was
that any authenticated user could read **and write** any other user's farm through
nineteen endpoints, two of which leaked the farm's name and private notes.

These tests walk the whole surface with a second user's valid token. The structural
test at the bottom is the one that stops it recurring: a new route with a `farm_id` in
its path cannot omit the dependency without failing.
"""

import inspect
import time

import jwt
import pytest
from httpx import AsyncClient

from app.core import security
from app.core.config import settings

SECRET = "a-test-signing-secret-not-a-real-one"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 64 + b"\xff\xd9"

ALICE_FARM = {
    "name": "Alice Secret Field",
    "latitude": -1.2864,
    "longitude": 36.8172,
    "notes": "Private agronomic notes",
}


def bearer(subject: str) -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": subject,
            "email": f"{subject}@example.test",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_AUTH", True)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setattr(settings, "SUPABASE_URL", None)
    security.reset_jwks_client()
    yield
    security.reset_jwks_client()


@pytest.fixture
async def alices_farm(auth_on, client: AsyncClient, api_prefix: str) -> dict:
    """A fully populated farm owned by Alice: a planting, an analysis, and an image."""
    created = await client.post(f"{api_prefix}/farms", json=ALICE_FARM, headers=bearer("alice"))
    assert created.status_code == 201, created.text
    farm = created.json()

    catalog = (await client.get(f"{api_prefix}/reference/crops", params={"page_size": 200})).json()
    maize = next(c for c in catalog["items"] if c["code"] == "maize")
    await client.post(
        f"{api_prefix}/farms/{farm['id']}/crops",
        json={
            "crop_id": maize["id"],
            "is_primary": True,
            "status": "growing",
            "growth_stage": "flowering",
        },
        headers=bearer("alice"),
    )
    run = (
        await client.post(f"{api_prefix}/farms/{farm['id']}/analysis", headers=bearer("alice"))
    ).json()
    image = (
        await client.post(
            f"{api_prefix}/farms/{farm['id']}/crop-images",
            files={"file": ("leaf.jpg", JPEG, "image/jpeg")},
            headers=bearer("alice"),
        )
    ).json()

    return {"farm": farm, "run": run, "image": image}


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "suffix",
    [
        "/analysis/latest",
        "/analysis",
        "/dashboard",
        "/risks/weather",
        "/risks/water",
        "/risks/disease",
        "/health",
        "/advisories",
        "/weather",
        "/soil",
        "/vegetation",
        "/recommendations/crops",
        "/recommendations/regenerative",
        "/crop-images",
    ],
)
async def test_another_user_cannot_read_a_farm_subresource(
    alices_farm, client: AsyncClient, api_prefix: str, suffix: str
) -> None:
    farm_id = alices_farm["farm"]["id"]

    response = await client.get(f"{api_prefix}/farms/{farm_id}{suffix}", headers=bearer("bob"))

    assert response.status_code == 404, f"{suffix} returned {response.status_code}"
    assert response.json()["error"]["code"] == "FARM_NOT_FOUND"


@pytest.mark.parametrize(
    "suffix",
    ["/analysis/latest", "/dashboard", "/weather", "/soil", "/advisories"],
)
async def test_no_private_data_leaks_in_the_refusal(
    alices_farm, client: AsyncClient, api_prefix: str, suffix: str
) -> None:
    """Two of these used to return Alice's farm name and notes in full."""
    farm_id = alices_farm["farm"]["id"]

    response = await client.get(f"{api_prefix}/farms/{farm_id}{suffix}", headers=bearer("bob"))

    assert "Alice Secret Field" not in response.text
    assert "Private agronomic notes" not in response.text


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------


async def test_another_user_cannot_run_an_analysis(
    alices_farm, client: AsyncClient, api_prefix: str
) -> None:
    farm_id = alices_farm["farm"]["id"]

    response = await client.post(f"{api_prefix}/farms/{farm_id}/analysis", headers=bearer("bob"))

    assert response.status_code == 404


async def test_another_user_cannot_upload_an_image(
    alices_farm, client: AsyncClient, api_prefix: str
) -> None:
    farm_id = alices_farm["farm"]["id"]

    response = await client.post(
        f"{api_prefix}/farms/{farm_id}/crop-images",
        files={"file": ("intruder.jpg", JPEG, "image/jpeg")},
        headers=bearer("bob"),
    )

    assert response.status_code == 404


# --------------------------------------------------------------------------
# Resources addressed by their own id
# --------------------------------------------------------------------------


async def test_another_user_cannot_read_an_analysis_run(
    alices_farm, client: AsyncClient, api_prefix: str
) -> None:
    """`GET /analysis/{run_id}` never sees a farm id, so ownership has to be resolved
    through the run's farm. It used to return the whole run."""
    run_id = alices_farm["run"]["id"]

    response = await client.get(f"{api_prefix}/analysis/{run_id}", headers=bearer("bob"))

    assert response.status_code == 404
    assert "Alice Secret Field" not in response.text


async def test_another_user_cannot_read_or_analyse_an_image(
    alices_farm, client: AsyncClient, api_prefix: str
) -> None:
    image_id = alices_farm["image"]["id"]

    read = await client.get(f"{api_prefix}/crop-images/{image_id}", headers=bearer("bob"))
    analysed = await client.post(
        f"{api_prefix}/crop-images/{image_id}/analyze", headers=bearer("bob")
    )

    assert read.status_code == 404
    assert analysed.status_code == 404


# --------------------------------------------------------------------------
# The owner is unaffected
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "suffix",
    ["/analysis/latest", "/dashboard", "/weather", "/soil", "/vegetation", "/crop-images"],
)
async def test_the_owner_still_reaches_everything(
    alices_farm, client: AsyncClient, api_prefix: str, suffix: str
) -> None:
    """Scoping must refuse the intruder without breaking the owner."""
    farm_id = alices_farm["farm"]["id"]

    response = await client.get(f"{api_prefix}/farms/{farm_id}{suffix}", headers=bearer("alice"))

    assert response.status_code == 200, response.text


async def test_the_owner_still_reaches_their_run_and_image(
    alices_farm, client: AsyncClient, api_prefix: str
) -> None:
    run_id, image_id = alices_farm["run"]["id"], alices_farm["image"]["id"]

    assert (
        await client.get(f"{api_prefix}/analysis/{run_id}", headers=bearer("alice"))
    ).status_code == 200
    assert (
        await client.get(f"{api_prefix}/crop-images/{image_id}", headers=bearer("alice"))
    ).status_code == 200


# --------------------------------------------------------------------------
# Global endpoints stay open
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/reference/enums", "/reference/crops", "/reference/locations?q=Nairobi"]
)
async def test_reference_data_needs_no_token(
    auth_on, client: AsyncClient, api_prefix: str, path: str
) -> None:
    """Reference data belongs to no farm and no user; requiring a token would break the
    registration flow before login."""
    assert (await client.get(f"{api_prefix}{path}")).status_code == 200


async def test_health_needs_no_token(auth_on, client: AsyncClient, api_prefix: str) -> None:
    assert (await client.get(f"{api_prefix}/health")).status_code == 200
    assert (await client.get(f"{api_prefix}/health/ready")).status_code == 200


# --------------------------------------------------------------------------
# Structural guard
# --------------------------------------------------------------------------


def _walk_routes(router):
    """Every route in the app, including those nested inside an included router.

    `app.routes` is not flat: FastAPI wraps `include_router` in an `_IncludedRouter`
    whose children live on `original_router`. Iterating only the top level finds five
    routes — none of them farm-scoped — which is how the first version of this guard
    passed while checking nothing.
    """
    for route in getattr(router, "routes", []):
        if getattr(route, "endpoint", None) is not None:
            yield route
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _walk_routes(nested)
        elif hasattr(route, "routes"):
            yield from _walk_routes(route)


def _declares_caller(endpoint) -> bool:
    from app.core.deps import get_current_user

    for parameter in inspect.signature(endpoint).parameters.values():
        if getattr(parameter.default, "dependency", None) is get_current_user:
            return True
        for meta in getattr(parameter.annotation, "__metadata__", ()):
            if getattr(meta, "dependency", None) is get_current_user:
                return True
    return False


def test_the_route_walk_actually_finds_farm_scoped_routes(app) -> None:
    """Guards the guard.

    If `_walk_routes` stops finding routes — a FastAPI change to how routers nest, say
    — the check below would pass on an empty list and protect nothing.
    """
    farm_scoped = [r for r in _walk_routes(app) if "{farm_id}" in getattr(r, "path", "")]

    assert len(farm_scoped) >= 15, f"only found {len(farm_scoped)} farm-scoped routes"


def test_every_farm_scoped_route_resolves_a_caller(app) -> None:
    """The guard that stops this recurring.

    Any route with `{farm_id}` in its path must declare the current-user dependency.
    A new endpoint that forgets it fails here rather than silently serving one user's
    farm to another — which is exactly how the original bypass survived review.
    """
    offenders = [
        f"{sorted(getattr(route, 'methods', []))} {route.path}"
        for route in _walk_routes(app)
        if "{farm_id}" in getattr(route, "path", "") and not _declares_caller(route.endpoint)
    ]

    assert not offenders, "farm-scoped routes without an identity dependency: " + ", ".join(
        offenders
    )


def test_the_guard_detects_a_route_that_forgets_the_dependency() -> None:
    """A negative control, because a structural guard that cannot fail is decoration.

    Builds a router with one compliant and one forgetful farm-scoped route and checks
    the detector separates them.
    """
    from typing import Annotated
    from uuid import UUID

    from fastapi import APIRouter, Depends

    from app.core.deps import CurrentUser, get_current_user

    caller = Annotated[CurrentUser, Depends(get_current_user)]
    probe = APIRouter()

    @probe.get("/farms/{farm_id}/forgetful")
    async def forgetful(farm_id: UUID) -> dict:
        return {}

    @probe.get("/farms/{farm_id}/compliant")
    async def compliant(farm_id: UUID, user: caller) -> dict:
        return {}

    flagged = {route.path for route in _walk_routes(probe) if not _declares_caller(route.endpoint)}

    assert "/farms/{farm_id}/forgetful" in flagged
    assert "/farms/{farm_id}/compliant" not in flagged


def test_require_farm_has_no_default_user() -> None:
    """A defaulted `user` is what made the bypass possible: every caller outside
    `farm_service` simply omitted it and got an unscoped read."""
    from app.services.farm_service import require_farm

    parameter = inspect.signature(require_farm).parameters["user"]

    assert parameter.default is inspect.Parameter.empty, (
        "require_farm(user=...) has a default again; a caller that omits it would "
        "silently bypass ownership"
    )


def test_resources_addressed_by_their_own_id_take_a_user() -> None:
    """`get_run` and `get_image` never see a farm id, so they are the two places where
    a missing user cannot be caught by a `{farm_id}` path check."""
    from app.services.analysis_service import get_run
    from app.services.image_service import get_image

    for function in (get_run, get_image):
        parameters = inspect.signature(function).parameters
        assert "user" in parameters, f"{function.__name__} does not take a user"
        assert parameters["user"].default is inspect.Parameter.empty
