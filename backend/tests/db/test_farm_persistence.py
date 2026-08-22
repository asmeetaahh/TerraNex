"""Farms and plantings, served from the database.

These drive the real API through the ASGI client with a database configured, so what
is under test is the whole stack — route, service, repository, SQL — not a repository
in isolation. The payloads asserted here are the same ones the existing in-memory
tests assert, because the storage swap must be invisible to a client.

The property the phase exists for is `test_a_farm_survives_a_restart`: dispose the
engine, reconnect, and the farm is still there. Nothing else in this file matters as
much.
"""

import pytest
from httpx import AsyncClient

from app.db import farm_repo
from app.db.session import session_scope
from app.models import FarmCropORM, FarmORM

NAIROBI = {
    "name": "North Field",
    "latitude": -1.2864,
    "longitude": 36.8172,
    "country_code": "ke",
    "region": "Nairobi County",
    "area_hectares": 12.5,
}


async def create_farm(client: AsyncClient, api_prefix: str, **overrides) -> dict:
    response = await client.post(f"{api_prefix}/farms", json={**NAIROBI, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


async def maize(client: AsyncClient, api_prefix: str) -> dict:
    response = await client.get(f"{api_prefix}/reference/crops", params={"page_size": 200})
    return next(c for c in response.json()["items"] if c["code"] == "maize")


# --------------------------------------------------------------------------
# CRUD reaches the database
# --------------------------------------------------------------------------


async def test_a_created_farm_is_a_row(sqlite_db, client: AsyncClient, api_prefix: str) -> None:
    body = await create_farm(client, api_prefix)

    with session_scope() as db:
        rows = db.query(FarmORM).all()

    assert len(rows) == 1
    assert str(rows[0].id) == body["id"]
    assert rows[0].name == "North Field"


async def test_coordinates_survive_the_round_trip(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """Coordinates are the one field a farm cannot afford to have rounded — every
    provider call and every downstream figure derives from them."""
    body = await create_farm(client, api_prefix, latitude=19.99727, longitude=73.79096)

    fetched = (await client.get(f"{api_prefix}/farms/{body['id']}")).json()

    assert fetched["latitude"] == pytest.approx(19.99727, abs=1e-6)
    assert fetched["longitude"] == pytest.approx(73.79096, abs=1e-6)
    assert isinstance(fetched["latitude"], float)


async def test_country_code_is_still_upper_cased(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """Existing normalisation must not be lost in the move."""
    body = await create_farm(client, api_prefix, country_code="ke")

    assert body["country_code"] == "KE"


async def test_update_persists(sqlite_db, client: AsyncClient, api_prefix: str) -> None:
    body = await create_farm(client, api_prefix)

    patched = await client.patch(
        f"{api_prefix}/farms/{body['id']}", json={"name": "South Field", "country_code": "tz"}
    )

    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "South Field"
    assert patched.json()["country_code"] == "TZ"

    with session_scope() as db:
        assert db.query(FarmORM).one().name == "South Field"


async def test_delete_is_soft(sqlite_db, client: AsyncClient, api_prefix: str) -> None:
    """The row stays so an analysis that referenced it still resolves; the API stops
    returning it."""
    body = await create_farm(client, api_prefix)

    assert (await client.delete(f"{api_prefix}/farms/{body['id']}")).status_code == 204
    assert (await client.get(f"{api_prefix}/farms/{body['id']}")).status_code == 404
    assert (await client.get(f"{api_prefix}/farms")).json()["items"] == []

    with session_scope() as db:
        row = db.query(FarmORM).one()
        assert row.deleted_at is not None


async def test_missing_farm_is_a_404_envelope(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    from uuid import uuid4

    response = await client.get(f"{api_prefix}/farms/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FARM_NOT_FOUND"


# --------------------------------------------------------------------------
# Persistence across a restart — the point of the phase
# --------------------------------------------------------------------------


async def test_a_farm_survives_a_restart(sqlite_db, client: AsyncClient, api_prefix: str) -> None:
    """Dispose the engine and every pooled connection, reconnect, and read again.

    Under the in-memory store this data was gone the moment the process ended.
    """
    from app.db import session as session_module

    created = await create_farm(client, api_prefix, name="Persistent Field")
    crop = await maize(client, api_prefix)
    await client.post(
        f"{api_prefix}/farms/{created['id']}/crops",
        json={"crop_id": crop["id"], "is_primary": True, "status": "growing"},
    )

    session_module.dispose_engine()  # as close to a restart as one process gets

    fetched = await client.get(f"{api_prefix}/farms/{created['id']}")
    plantings = await client.get(f"{api_prefix}/farms/{created['id']}/crops")

    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]
    assert fetched.json()["name"] == "Persistent Field"
    assert fetched.json()["crop_count"] == 1
    assert plantings.json()["items"][0]["crop"]["code"] == "maize"


# --------------------------------------------------------------------------
# Plantings
# --------------------------------------------------------------------------


async def test_planting_embeds_the_catalog_entry(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """`FarmCrop.crop` is embedded so the frontend renders a card without a second
    request. It must resolve against the database catalog."""
    farm = await create_farm(client, api_prefix)
    crop = await maize(client, api_prefix)

    response = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crops",
        json={"crop_id": crop["id"], "growth_stage": "flowering", "status": "growing"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["crop"]["id"] == crop["id"]
    assert response.json()["crop"]["code"] == "maize"


async def test_an_unknown_crop_is_rejected(sqlite_db, client: AsyncClient, api_prefix: str) -> None:
    from uuid import uuid4

    farm = await create_farm(client, api_prefix)

    response = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crops", json={"crop_id": str(uuid4())}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CROP_NOT_FOUND"


async def test_only_one_planting_stays_primary(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """The demote-and-promote happens in one transaction, which is why this is not a
    partial unique index — a constraint would fire mid-swap."""
    farm = await create_farm(client, api_prefix)
    catalog = (await client.get(f"{api_prefix}/reference/crops", params={"page_size": 5})).json()

    for entry in catalog["items"][:3]:
        response = await client.post(
            f"{api_prefix}/farms/{farm['id']}/crops",
            json={"crop_id": entry["id"], "is_primary": True},
        )
        assert response.status_code == 201, response.text

    with session_scope() as db:
        primaries = db.query(FarmCropORM).filter(FarmCropORM.is_primary.is_(True)).all()

    assert len(primaries) == 1


async def test_deleting_a_planting_persists(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    farm = await create_farm(client, api_prefix)
    crop = await maize(client, api_prefix)
    planting = (
        await client.post(f"{api_prefix}/farms/{farm['id']}/crops", json={"crop_id": crop["id"]})
    ).json()

    deleted = await client.delete(f"{api_prefix}/farms/{farm['id']}/crops/{planting['id']}")

    assert deleted.status_code == 204
    with session_scope() as db:
        assert db.query(FarmCropORM).count() == 0


async def test_crop_count_is_derived_not_stored(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    farm = await create_farm(client, api_prefix)
    catalog = (await client.get(f"{api_prefix}/reference/crops", params={"page_size": 5})).json()

    for entry in catalog["items"][:2]:
        await client.post(f"{api_prefix}/farms/{farm['id']}/crops", json={"crop_id": entry["id"]})

    assert (await client.get(f"{api_prefix}/farms/{farm['id']}")).json()["crop_count"] == 2
    assert (await client.get(f"{api_prefix}/farms")).json()["items"][0]["crop_count"] == 2


async def test_listing_farms_counts_plantings_in_one_query(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """`GET /farms` is the dashboard's first call. Counting per farm would be an N+1
    the in-memory store never had."""
    crop = await maize(client, api_prefix)
    for index in range(4):
        farm = await create_farm(client, api_prefix, name=f"Field {index}")
        await client.post(f"{api_prefix}/farms/{farm['id']}/crops", json={"crop_id": crop["id"]})

    from uuid import UUID

    listed = (await client.get(f"{api_prefix}/farms")).json()["items"]
    counts = farm_repo.count_plantings_by_farm([UUID(row["id"]) for row in listed])

    assert len(counts) == 4
    assert set(counts.values()) == {1}


async def test_farms_are_listed_in_creation_order(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """The in-memory store gave this free through dict insertion order."""
    names = [f"Field {index}" for index in range(4)]
    for name in names:
        await create_farm(client, api_prefix, name=name)

    listed = (await client.get(f"{api_prefix}/farms")).json()["items"]

    assert [row["name"] for row in listed] == names


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------


def test_a_failed_transaction_writes_nothing(sqlite_db) -> None:
    """`session_scope` rolls back on any exception, so a half-finished operation
    cannot leave a partial farm behind."""
    import uuid
    from datetime import UTC, datetime

    from app.db.memory import FarmRecord

    now = datetime.now(UTC)
    record = FarmRecord(
        id=uuid.uuid4(),
        name="Doomed",
        latitude=0.0,
        longitude=0.0,
        area_hectares=None,
        country_code=None,
        region=None,
        elevation_m=None,
        irrigation_type="rainfed",
        farming_practice="conventional",
        notes=None,
        created_at=now,
        updated_at=now,
    )
    farm_repo.insert_farm(record)

    with pytest.raises(ValueError), session_scope() as db:
        db.add(
            FarmORM(
                id=uuid.uuid4(),
                name="Also doomed",
                latitude=1.0,
                longitude=1.0,
                irrigation_type="rainfed",
                farming_practice="conventional",
                created_at=now,
                updated_at=now,
            )
        )
        raise ValueError("boom")

    with session_scope() as db:
        assert db.query(FarmORM).count() == 1


# --------------------------------------------------------------------------
# Regressions: services that read plantings without going through farm_service
#
# Both of these passed on the in-memory store and failed the moment plantings moved
# into the database, because `analysis_service` and `image_service` reached into the
# store directly instead of dispatching. They are the reason `plantings_for_farm` and
# `find_planting` are public.
# --------------------------------------------------------------------------


async def test_the_dashboard_lists_persisted_plantings(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """The dashboard used to report zero crops for a farm whose own `crop_count` in
    the same payload said otherwise."""
    farm = await create_farm(client, api_prefix)
    crop = await maize(client, api_prefix)
    await client.post(
        f"{api_prefix}/farms/{farm['id']}/crops",
        json={"crop_id": crop["id"], "is_primary": True, "status": "growing"},
    )

    dashboard = (await client.get(f"{api_prefix}/farms/{farm['id']}/dashboard")).json()

    assert len(dashboard["crops"]) == 1
    assert dashboard["crops"][0]["crop"]["code"] == "maize"
    # The two must agree; their disagreeing is what exposed the bug.
    assert dashboard["farm"]["crop_count"] == len(dashboard["crops"])


async def test_an_image_can_be_attached_to_a_persisted_planting(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """Attaching an image to a planting 404'd purely because the database was on."""
    from tests.conftest import JPEG_BYTES

    farm = await create_farm(client, api_prefix)
    crop = await maize(client, api_prefix)
    planting = (
        await client.post(
            f"{api_prefix}/farms/{farm['id']}/crops",
            json={"crop_id": crop["id"], "status": "growing"},
        )
    ).json()

    response = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crop-images",
        files={"file": ("leaf.jpg", JPEG_BYTES, "image/jpeg")},
        data={"farm_crop_id": planting["id"]},
    )

    assert response.status_code == 201, response.text
    assert response.json()["farm_crop_id"] == planting["id"]


async def test_an_image_for_an_unknown_planting_still_404s(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """The fix must not have made the check permissive — the error envelope is
    unchanged, including its details."""
    from uuid import uuid4

    from tests.conftest import JPEG_BYTES

    farm = await create_farm(client, api_prefix)
    missing = str(uuid4())

    response = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crop-images",
        files={"file": ("leaf.jpg", JPEG_BYTES, "image/jpeg")},
        data={"farm_crop_id": missing},
    )

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "CROP_NOT_FOUND"
    assert error["details"] == {"farm_id": farm["id"], "farm_crop_id": missing}


async def test_a_planting_on_another_farm_is_rejected(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """Farm scoping must survive the dispatch — a planting that exists, but not here."""
    from tests.conftest import JPEG_BYTES

    crop = await maize(client, api_prefix)
    mine = await create_farm(client, api_prefix, name="Mine")
    theirs = await create_farm(client, api_prefix, name="Theirs")
    elsewhere = (
        await client.post(f"{api_prefix}/farms/{theirs['id']}/crops", json={"crop_id": crop["id"]})
    ).json()

    response = await client.post(
        f"{api_prefix}/farms/{mine['id']}/crop-images",
        files={"file": ("leaf.jpg", JPEG_BYTES, "image/jpeg")},
        data={"farm_crop_id": elsewhere["id"]},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CROP_NOT_FOUND"


async def test_image_analysis_resolves_the_planting_crop(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """The second lookup site: diagnosis reads the attached planting's crop."""
    from tests.conftest import JPEG_BYTES

    farm = await create_farm(client, api_prefix)
    crop = await maize(client, api_prefix)
    planting = (
        await client.post(
            f"{api_prefix}/farms/{farm['id']}/crops",
            json={"crop_id": crop["id"], "status": "growing"},
        )
    ).json()
    image = (
        await client.post(
            f"{api_prefix}/farms/{farm['id']}/crop-images",
            files={"file": ("leaf.jpg", JPEG_BYTES, "image/jpeg")},
            data={"farm_crop_id": planting["id"]},
        )
    ).json()

    analysed = await client.post(f"{api_prefix}/crop-images/{image['id']}/analyze")

    assert analysed.status_code == 200, analysed.text
    assert analysed.json()["analysis"] is not None
