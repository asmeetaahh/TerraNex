"""Crop-image upload and simulated diagnosis.

No model is called anywhere in this flow; the tests assert that the payload says so.
"""

import io

from httpx import AsyncClient

from tests.conftest import JPEG_BYTES

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 48


def _file(data: bytes = JPEG_BYTES, name: str = "leaf.jpg", mime: str = "image/jpeg"):
    return {"file": (name, io.BytesIO(data), mime)}


async def test_upload_returns_pending_without_analysis(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    """Upload returns immediately so the UI can show a thumbnail at once."""
    resp = await client.post(f"{api_prefix}/farms/{farm['id']}/crop-images", files=_file())

    assert resp.status_code == 201
    body = resp.json()
    assert body["analysis_status"] == "pending"
    assert body["analysis"] is None
    assert body["size_bytes"] == len(JPEG_BYTES)
    assert body["content_type"] == "image/jpeg"
    assert body["farm_id"] == farm["id"]
    assert body["uploaded_at"]


async def test_upload_has_no_url_because_there_is_no_storage(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    """Phase 3 has no object storage, so `url` must stay null rather than point at
    something that does not exist."""
    resp = await client.post(f"{api_prefix}/farms/{farm['id']}/crop-images", files=_file())
    assert resp.json()["url"] is None


async def test_upload_with_analyze_returns_a_diagnosis(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    resp = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crop-images",
        files=_file(),
        params={"analyze": True},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["analysis_status"] == "complete"
    assert body["analysis"] is not None
    assert body["analyzed_at"]


async def test_two_step_upload_then_analyze(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    uploaded = await client.post(f"{api_prefix}/farms/{farm['id']}/crop-images", files=_file())
    image_id = uploaded.json()["id"]

    analyzed = await client.post(f"{api_prefix}/crop-images/{image_id}/analyze")

    assert analyzed.status_code == 200
    assert analyzed.json()["analysis_status"] == "complete"
    assert analyzed.json()["id"] == image_id

    fetched = await client.get(f"{api_prefix}/crop-images/{image_id}")
    assert fetched.json()["analysis"] == analyzed.json()["analysis"]


async def test_diagnosis_declares_mock_and_carries_a_disclaimer(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    resp = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crop-images", files=_file(), params={"analyze": True}
    )

    body = resp.json()
    assert body["ai_mode"] == "mock"
    assert body["model"] is None
    assert body["prompt_version"]
    assert body["analysis"]["disclaimer"]


async def test_diagnosis_includes_the_honesty_fields(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    """`is_plant_material` and `differential_diagnoses` are what stop a diagnosis
    reading as a single overconfident answer."""
    resp = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crop-images", files=_file(), params={"analyze": True}
    )

    analysis = resp.json()["analysis"]
    assert analysis["is_plant_material"] is True
    assert analysis["condition"] and analysis["condition_label"]
    assert analysis["severity"] in {"none", "mild", "moderate", "severe"}
    assert 0 <= analysis["confidence"] <= 1
    assert analysis["differential_diagnoses"]
    for item in analysis["differential_diagnoses"]:
        assert item["condition"] != analysis["condition"]
        assert 0 <= item["likelihood"] <= 1
    assert analysis["immediate_actions"]
    assert analysis["prevention"]


async def test_same_image_yields_the_same_diagnosis(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    """The diagnosis is seeded from the image bytes, so it is reproducible."""
    first = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crop-images", files=_file(), params={"analyze": True}
    )
    second = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crop-images", files=_file(), params={"analyze": True}
    )

    a, b = first.json()["analysis"], second.json()["analysis"]
    assert a["condition"] == b["condition"]
    assert a["confidence"] == b["confidence"]
    assert a["severity"] == b["severity"]


async def test_different_images_can_differ(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    """Determinism must not mean every photograph gets the same answer."""
    conditions = set()
    for index in range(8):
        payload = JPEG_BYTES[:-1] + bytes([index]) + b"\xd9"
        resp = await client.post(
            f"{api_prefix}/farms/{farm['id']}/crop-images",
            files=_file(payload),
            params={"analyze": True},
        )
        conditions.add(resp.json()["analysis"]["condition"])

    assert len(conditions) > 1


async def test_upload_accepts_png(client: AsyncClient, api_prefix: str, farm: dict) -> None:
    resp = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crop-images",
        files=_file(PNG_BYTES, "leaf.png", "image/png"),
    )
    assert resp.status_code == 201
    assert resp.json()["content_type"] == "image/png"


async def test_mismatched_magic_bytes_are_rejected(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    """A renamed file must not pass as an image just because the MIME type says so."""
    resp = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crop-images",
        files=_file(b"this is plain text pretending to be a jpeg"),
    )

    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


async def test_empty_upload_is_rejected(client: AsyncClient, api_prefix: str, farm: dict) -> None:
    resp = await client.post(f"{api_prefix}/farms/{farm['id']}/crop-images", files=_file(b""))
    assert resp.status_code == 415


async def test_oversized_upload_is_rejected(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    from app.core.config import settings

    oversized = JPEG_BYTES[:3] + b"\x00" * (settings.max_upload_bytes + 1)
    resp = await client.post(f"{api_prefix}/farms/{farm['id']}/crop-images", files=_file(oversized))

    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "IMAGE_TOO_LARGE"
    assert resp.json()["error"]["details"]["max_bytes"] == settings.max_upload_bytes


async def test_note_and_planting_are_attached(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    listing = await client.get(f"{api_prefix}/farms/{planted_farm['id']}/crops")
    planting_id = listing.json()["items"][0]["id"]

    resp = await client.post(
        f"{api_prefix}/farms/{planted_farm['id']}/crop-images",
        files=_file(),
        data={"farm_crop_id": planting_id, "note": "Lower canopy, north corner"},
        params={"analyze": True},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["farm_crop_id"] == planting_id
    assert body["note"] == "Lower canopy, north corner"
    # Crop context reaches the diagnosis.
    assert body["analysis"]["crop_identified"] == "maize"


async def test_planting_from_another_farm_is_rejected(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    other = await client.post(
        f"{api_prefix}/farms", json={"name": "Other", "latitude": 10.0, "longitude": 10.0}
    )
    listing = await client.get(f"{api_prefix}/farms/{planted_farm['id']}/crops")
    planting_id = listing.json()["items"][0]["id"]

    resp = await client.post(
        f"{api_prefix}/farms/{other.json()['id']}/crop-images",
        files=_file(),
        data={"farm_crop_id": planting_id},
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CROP_NOT_FOUND"


async def test_listing_is_newest_first(client: AsyncClient, api_prefix: str, farm: dict) -> None:
    for index in range(3):
        await client.post(
            f"{api_prefix}/farms/{farm['id']}/crop-images",
            files=_file(JPEG_BYTES[:-1] + bytes([index]) + b"\xd9"),
        )

    resp = await client.get(f"{api_prefix}/farms/{farm['id']}/crop-images")

    assert resp.json()["total"] == 3
    timestamps = [i["uploaded_at"] for i in resp.json()["items"]]
    assert timestamps == sorted(timestamps, reverse=True)


async def test_images_appear_on_the_dashboard(
    client: AsyncClient, api_prefix: str, analyzed_farm: dict
) -> None:
    await client.post(
        f"{api_prefix}/farms/{analyzed_farm['id']}/crop-images",
        files=_file(),
        params={"analyze": True},
    )

    dashboard = await client.get(f"{api_prefix}/farms/{analyzed_farm['id']}/dashboard")
    assert len(dashboard.json()["recent_images"]) == 1
