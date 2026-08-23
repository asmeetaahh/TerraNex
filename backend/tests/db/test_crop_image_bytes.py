"""The photograph itself, persisted.

Upload and diagnosis are separate HTTP requests. The uploaded bytes were validated,
measured for `size_bytes`/`width`/`height`, hashed, and then dropped when
`upload_image` returned — so by the time `POST /crop-images/{id}/analyze` ran, the
picture no longer existed anywhere in the system. No vision model could ever have been
shown it.

These cover the property that makes a model call possible at all:
`test_the_photograph_survives_a_restart`, and its counterpart
`test_re_analysis_does_not_discard_the_photograph`.

**Nothing here asserts that the diagnosis reads the pixels** — it does not yet.
`_simulate_diagnosis` is still seeded from the digest, and replacing it is the next
step. What is proven here is that the bytes are there when it is.
"""

import io

import pytest
from httpx import AsyncClient

from app.db import image_repo
from app.db.memory import store
from app.db.seed import demo_id, seed_crops, seed_demo_farms
from app.services import image_service
from tests.conftest import JPEG_BYTES


def _file(data: bytes = JPEG_BYTES, name: str = "leaf.jpg", mime: str = "image/jpeg"):
    return {"file": (name, io.BytesIO(data), mime)}


def big_jpeg(width: int = 4000, height: int = 3000) -> bytes:
    """A genuinely oversized photograph, larger than the storage ceiling on both edges.

    Built rather than fixtured because the point is the pixel dimensions, and a file
    committed to the repo at this size would dwarf everything else in it.
    """
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (34, 89, 51)).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


@pytest.fixture
def farm_id(sqlite_db) -> str:
    seed_crops()
    seed_demo_farms()
    return str(demo_id("farm", "nakuru-maize-field"))


async def upload(client: AsyncClient, api_prefix: str, farm_id: str, data: bytes = JPEG_BYTES):
    response = await client.post(f"{api_prefix}/farms/{farm_id}/crop-images", files=_file(data))
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------
# The bytes are kept
# --------------------------------------------------------------------------


async def test_an_upload_stores_the_photograph(farm_id, client: AsyncClient, api_prefix: str):
    from uuid import UUID

    created = await upload(client, api_prefix, farm_id)

    assert image_repo.get_bytes(UUID(created["id"])) is not None


async def test_the_photograph_survives_a_restart(farm_id, client: AsyncClient, api_prefix: str):
    """The property the whole step exists for: a restart between upload and diagnosis
    must not leave the analyse endpoint with nothing to look at."""
    from uuid import UUID

    created = await upload(client, api_prefix, farm_id)

    store.reset()

    assert image_repo.get_bytes(UUID(created["id"])) is not None


async def test_re_analysis_does_not_discard_the_photograph(
    farm_id, client: AsyncClient, api_prefix: str
):
    """`update_analysis` writes only the diagnosis columns. If it ever wrote the whole
    row, the pixels would be lost the first time an image was diagnosed."""
    from uuid import UUID

    created = await upload(client, api_prefix, farm_id)
    image_id = UUID(created["id"])
    before = image_repo.get_bytes(image_id)

    await client.post(f"{api_prefix}/crop-images/{created['id']}/analyze")

    assert image_repo.get_bytes(image_id) == before


async def test_an_unknown_image_has_no_bytes(farm_id):
    from uuid import uuid4

    assert image_repo.get_bytes(uuid4()) is None


# --------------------------------------------------------------------------
# Downscaling
# --------------------------------------------------------------------------


def test_a_large_photograph_is_downscaled_before_storage():
    from PIL import Image

    original = big_jpeg()
    stored = image_service._downscale(original)

    with Image.open(io.BytesIO(stored)) as img:
        assert max(img.width, img.height) <= image_service.MAX_STORED_EDGE_PX
    assert len(stored) < len(original)


def test_a_small_photograph_is_left_alone():
    """Re-encoding a thumbnail would cost quality for no benefit."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 20, 30)).save(buffer, format="JPEG")
    small = buffer.getvalue()

    assert image_service._downscale(small) == small


def test_an_unreadable_file_is_stored_as_uploaded():
    """Validation already accepted the upload, so a file Pillow cannot open must still
    be stored rather than failing the request for a convenience step."""
    assert image_service._downscale(JPEG_BYTES) == JPEG_BYTES


def test_transparency_does_not_break_re_encoding():
    """JPEG has no alpha channel, so an RGBA PNG must be converted rather than raise —
    which would silently fall back to storing a multi-megabyte original."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGBA", (3000, 2000), (200, 30, 30, 128)).save(buffer, format="PNG")
    original = buffer.getvalue()

    stored = image_service._downscale(original)

    assert stored != original
    with Image.open(io.BytesIO(stored)) as img:
        assert max(img.width, img.height) <= image_service.MAX_STORED_EDGE_PX


async def test_the_reported_metadata_describes_the_original_upload(
    farm_id, client: AsyncClient, api_prefix: str
):
    """`size_bytes`, `width` and `height` report what the user sent. Reporting the
    downscaled figures would tell them their 4000 px photograph was a 1568 px one."""
    from uuid import UUID

    original = big_jpeg()
    created = await upload(client, api_prefix, farm_id, original)

    assert created["size_bytes"] == len(original)
    assert created["width"] == 4000
    assert created["height"] == 3000

    stored = image_repo.get_bytes(UUID(created["id"]))
    assert len(stored) < created["size_bytes"], "storage keeps the smaller copy"


# --------------------------------------------------------------------------
# Re-analysis reuses the stored diagnosis
# --------------------------------------------------------------------------


async def test_a_second_analysis_returns_the_stored_diagnosis(
    farm_id, client: AsyncClient, api_prefix: str
):
    """The photograph is immutable, so a repeat analysis is the same question. Once a
    model does this work it is also a second bill; the contract has no `force`."""
    created = await upload(client, api_prefix, farm_id)

    first = (await client.post(f"{api_prefix}/crop-images/{created['id']}/analyze")).json()
    second = (await client.post(f"{api_prefix}/crop-images/{created['id']}/analyze")).json()

    assert second == first


async def test_reuse_survives_a_restart(farm_id, client: AsyncClient, api_prefix: str):
    created = await upload(client, api_prefix, farm_id)
    first = (await client.post(f"{api_prefix}/crop-images/{created['id']}/analyze")).json()

    store.reset()

    second = (await client.post(f"{api_prefix}/crop-images/{created['id']}/analyze")).json()

    assert second["analysis"] == first["analysis"]
    assert second["analyzed_at"] == first["analyzed_at"], "a reused diagnosis is not re-stamped"


# --------------------------------------------------------------------------
# Offline path parity
# --------------------------------------------------------------------------


async def test_the_memory_path_also_keeps_the_photograph(client: AsyncClient, api_prefix: str):
    """No `sqlite_db` fixture here: the offline store must carry the pixels too, or the
    two supported configurations disagree about what an uploaded image is."""
    from uuid import UUID

    assert image_service._persisted() is False

    resp = await client.post(
        f"{api_prefix}/farms", json={"name": "Mem", "latitude": 1.0, "longitude": 36.0}
    )
    farm = resp.json()
    created = await upload(client, api_prefix, farm["id"])

    assert store.image_bytes(UUID(created["id"])) is not None
