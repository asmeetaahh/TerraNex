"""Crop images, persisted.

Images lived in a process-local dict, so a restart emptied a farm's diagnosis history
while its farms and analysis runs survived — an inconsistency rather than a plain loss,
and the last in-memory island in the product.

**The test that motivated the design is `test_the_same_photograph_is_diagnosed_the_same
_way_after_a_restart`.** The diagnosis is seeded by the image's content digest, and the
digest used to live in a module-global dict outside the store: it was not cleared by
`reset()`, not carried by any storage swap, and gone on restart. Re-analysing then fell
back to seeding from the image's id, so the same photograph came back with a *different*
diagnosis — silently, because nothing errored and a diagnosis was still produced.

`test_the_digest_itself_survives_a_restart` asserts the mechanism rather than the
symptom, because a fallback seed that happened to collide would satisfy the symptom
test while leaving the defect in place.
"""

import io
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.core.deps import demo_user
from app.db import image_repo
from app.db.memory import store
from app.db.seed import demo_id, seed_crops, seed_demo_farms
from tests.conftest import JPEG_BYTES

OTHER_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x11" * 96 + b"\xff\xd9"


def _file(data: bytes = JPEG_BYTES, name: str = "leaf.jpg", mime: str = "image/jpeg"):
    return {"file": (name, io.BytesIO(data), mime)}


@pytest.fixture
def farm_id(sqlite_db) -> str:
    seed_crops()
    seed_demo_farms()
    return str(demo_id("farm", "nakuru-maize-field"))


async def upload(
    client: AsyncClient,
    api_prefix: str,
    farm_id: str,
    data: bytes = JPEG_BYTES,
    *,
    analyze: bool = False,
    **form,
) -> dict:
    """`analyze` is a query parameter; `farm_crop_id` and `note` are form fields.

    Sending a form field as a query string is silently ignored by FastAPI — the upload
    still succeeds, just unattached — which is how the first version of this helper
    produced an image with a null `farm_crop_id` and no error to show for it.
    """
    query = "?analyze=true" if analyze else ""
    response = await client.post(
        f"{api_prefix}/farms/{farm_id}/crop-images{query}",
        files=_file(data),
        data={k: str(v) for k, v in form.items()},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def analyze(client: AsyncClient, api_prefix: str, image_id: str) -> dict:
    response = await client.post(f"{api_prefix}/crop-images/{image_id}/analyze")
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------
# The row lands in the database
# --------------------------------------------------------------------------


async def test_an_upload_writes_a_row(farm_id, client: AsyncClient, api_prefix: str) -> None:
    await upload(client, api_prefix, farm_id)

    assert image_repo.count_images(UUID(farm_id)) == 1


async def test_nothing_is_written_to_the_memory_store(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    """The defect in one assertion: with a database configured the store must stay
    empty, or a restart still loses everything."""
    await upload(client, api_prefix, farm_id)

    assert store.crop_images == {}
    assert store.image_metadata == {}


async def test_an_image_survives_a_restart(farm_id, client: AsyncClient, api_prefix: str) -> None:
    """Clearing the store is what a fresh process looks like."""
    created = await upload(client, api_prefix, farm_id)

    store.reset()

    restored = (await client.get(f"{api_prefix}/crop-images/{created['id']}")).json()

    assert restored["id"] == created["id"]
    assert restored["size_bytes"] == created["size_bytes"]


async def test_history_survives_a_restart(farm_id, client: AsyncClient, api_prefix: str) -> None:
    first = await upload(client, api_prefix, farm_id)
    second = await upload(client, api_prefix, farm_id, OTHER_JPEG)

    store.reset()
    history = (await client.get(f"{api_prefix}/farms/{farm_id}/crop-images")).json()

    assert history["total"] == 2
    assert {item["id"] for item in history["items"]} == {first["id"], second["id"]}


async def test_the_dashboard_shows_persisted_images(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    """The dashboard read the store directly, so a farm whose images were in the
    database rendered an empty diagnosis panel."""
    created = await upload(client, api_prefix, farm_id)
    store.reset()

    dashboard = (await client.get(f"{api_prefix}/farms/{farm_id}/dashboard")).json()

    assert [image["id"] for image in dashboard["recent_images"]] == [created["id"]]


# --------------------------------------------------------------------------
# Determinism across a restart — the regression this step exists for
# --------------------------------------------------------------------------


async def test_the_same_photograph_is_diagnosed_the_same_way_after_a_restart(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    """Upload, analyse, restart, analyse the *same persisted image* again.

    The diagnosis is a function of the bytes, so it must not move. Before the digest was
    stored, the second analysis re-seeded from the image's id and returned a different
    condition, severity and confidence for a photograph that had not changed.
    """
    created = await upload(client, api_prefix, farm_id)
    before = await analyze(client, api_prefix, created["id"])

    store.reset()

    after = await analyze(client, api_prefix, created["id"])

    assert after["analysis"] == before["analysis"]


async def test_the_digest_itself_survives_a_restart(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    """The mechanism, not the symptom.

    A fallback seed that happened to produce the same diagnosis would satisfy the test
    above while leaving the defect in place, so the stored digest is asserted directly:
    it is the SHA-256 of the uploaded bytes, and it is still that after a restart.
    """
    import hashlib

    created = await upload(client, api_prefix, farm_id)
    expected = hashlib.sha256(JPEG_BYTES).hexdigest()

    assert image_repo.get_digest(UUID(created["id"])) == expected

    store.reset()

    stored = image_repo.get_digest(UUID(created["id"]))
    assert stored == expected
    assert stored != str(created["id"]), "the digest must not degrade to the image id"


async def test_a_different_photograph_still_gets_a_different_seed(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    """Determinism must not collapse into 'always the same answer'."""
    one = await upload(client, api_prefix, farm_id)
    two = await upload(client, api_prefix, farm_id, OTHER_JPEG)

    assert image_repo.get_digest(UUID(one["id"])) != image_repo.get_digest(UUID(two["id"]))


async def test_analysing_updates_the_row_rather_than_inserting_another(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    """An image is one thing that acquires a diagnosis, not a new observation."""
    created = await upload(client, api_prefix, farm_id)
    await analyze(client, api_prefix, created["id"])
    await analyze(client, api_prefix, created["id"])

    assert image_repo.count_images(UUID(farm_id)) == 1


# --------------------------------------------------------------------------
# Payload fidelity
# --------------------------------------------------------------------------


async def test_the_diagnosis_round_trips(farm_id, client: AsyncClient, api_prefix: str) -> None:
    created = await upload(client, api_prefix, farm_id, analyze=True)
    store.reset()

    restored = (await client.get(f"{api_prefix}/crop-images/{created['id']}")).json()

    assert restored == created


@pytest.mark.parametrize(
    "field",
    [
        "symptoms_observed",
        "differential_diagnoses",
        "immediate_actions",
        "treatment_options",
        "prevention",
    ],
)
async def test_every_nested_diagnosis_section_survives_storage(
    farm_id, client: AsyncClient, api_prefix: str, field: str
) -> None:
    """A section lost in serialisation shows as an empty card, not an error.

    Emptiness is only asserted where the diagnosis actually produced something: a
    *healthy* verdict legitimately carries no treatment options, so requiring every
    section to be populated would assert bad agronomy rather than good serialisation.
    """
    created = await upload(client, api_prefix, farm_id, analyze=True)
    store.reset()

    restored = (await client.get(f"{api_prefix}/crop-images/{created['id']}")).json()

    assert restored["analysis"][field] == created["analysis"][field]
    if created["analysis"][field]:
        assert restored["analysis"][field], f"{field} was populated but came back empty"


async def test_the_stored_image_still_has_no_url(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    """There is no object storage in this phase, so persistence must not invent a URL."""
    created = await upload(client, api_prefix, farm_id)
    store.reset()

    restored = (await client.get(f"{api_prefix}/crop-images/{created['id']}")).json()

    assert restored["url"] is None


async def test_the_owner_is_recorded(farm_id, client: AsyncClient, api_prefix: str) -> None:
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import CropImageORM

    await upload(client, api_prefix, farm_id)

    with session_scope() as db:
        row = db.scalars(select(CropImageORM)).first()

    assert row is not None
    assert row.user_id == demo_user().id


async def test_an_unknown_image_id_is_not_found(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    response = await client.get(f"{api_prefix}/crop-images/{uuid4()}")

    assert response.status_code == 404


# --------------------------------------------------------------------------
# PostgreSQL only
# --------------------------------------------------------------------------


@pytest.mark.postgres
async def test_the_farm_foreign_key_cascades_in_postgres(
    postgres_db, client: AsyncClient, api_prefix: str
) -> None:
    """SQLite runs with foreign keys off, so this is the only place the cascade is
    proven — and therefore the guarantee that deleting a farm does not orphan images."""
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import CropImageORM, FarmORM

    seed_crops()
    seed_demo_farms()
    farm = demo_id("farm", "nakuru-maize-field")

    await upload(client, api_prefix, str(farm))
    assert image_repo.count_images(farm) == 1

    with session_scope() as db:
        db.delete(db.get(FarmORM, farm))

    with session_scope() as db:
        assert db.scalars(select(CropImageORM)).all() == []


@pytest.mark.postgres
async def test_deleting_a_planting_keeps_the_image_in_postgres(
    postgres_db, client: AsyncClient, api_prefix: str
) -> None:
    """`ON DELETE SET NULL`, the decision this step made explicitly: a diagnosis is
    evidence, so deleting the planting costs the photograph its crop link, not its
    existence. A CASCADE here would destroy the record instead."""
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import CropImageORM, FarmCropORM

    seed_crops()
    seed_demo_farms()
    farm = demo_id("farm", "nakuru-maize-field")

    crops = (await client.get(f"{api_prefix}/farms/{farm}/crops")).json()["items"]
    assert crops, "the seeded demo farm is expected to have a planting"
    planting_id = crops[0]["id"]

    created = await upload(client, api_prefix, str(farm), farm_crop_id=planting_id)
    assert created["farm_crop_id"] == planting_id

    with session_scope() as db:
        db.delete(db.get(FarmCropORM, UUID(planting_id)))

    with session_scope() as db:
        row = db.scalars(select(CropImageORM)).first()

    assert row is not None, "the image must outlive the planting"
    assert row.farm_crop_id is None
