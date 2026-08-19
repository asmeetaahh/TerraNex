"""Guarantees the whole published contract must uphold, regardless of phase.

These assertions are about the *envelope and the edges*, not about any endpoint's
behaviour: validation runs before handlers, errors always look the same, correlation
works on failure paths, and unknown resources 404 rather than 500.

Endpoint behaviour lives in the per-area test modules.
"""

import io

import pytest
from httpx import AsyncClient

MISSING_FARM = "11111111-1111-4111-8111-111111111111"
MISSING_CROP = "22222222-2222-4222-8222-222222222222"
MISSING_RUN = "33333333-3333-4333-8333-333333333333"
MISSING_IMAGE = "44444444-4444-4444-8444-444444444444"

# Every route that resolves a farm from the path must 404 on an unknown one, in the
# standard envelope — never 500, never an empty body.
FARM_SCOPED_ROUTES = [
    ("GET", f"/farms/{MISSING_FARM}"),
    ("PATCH", f"/farms/{MISSING_FARM}"),
    ("DELETE", f"/farms/{MISSING_FARM}"),
    ("GET", f"/farms/{MISSING_FARM}/crops"),
    ("GET", f"/farms/{MISSING_FARM}/weather"),
    ("GET", f"/farms/{MISSING_FARM}/soil"),
    ("GET", f"/farms/{MISSING_FARM}/vegetation"),
    ("POST", f"/farms/{MISSING_FARM}/analysis"),
    ("GET", f"/farms/{MISSING_FARM}/analysis"),
    ("GET", f"/farms/{MISSING_FARM}/analysis/latest"),
    ("GET", f"/farms/{MISSING_FARM}/dashboard"),
    ("GET", f"/farms/{MISSING_FARM}/risks/weather"),
    ("GET", f"/farms/{MISSING_FARM}/risks/water"),
    ("GET", f"/farms/{MISSING_FARM}/risks/disease"),
    ("GET", f"/farms/{MISSING_FARM}/health"),
    ("GET", f"/farms/{MISSING_FARM}/advisories"),
    ("GET", f"/farms/{MISSING_FARM}/recommendations/crops"),
    ("GET", f"/farms/{MISSING_FARM}/recommendations/regenerative"),
    ("GET", f"/farms/{MISSING_FARM}/crop-images"),
]


@pytest.mark.parametrize(
    ("method", "path"), FARM_SCOPED_ROUTES, ids=[f"{m} {p}" for m, p in FARM_SCOPED_ROUTES]
)
async def test_unknown_farm_is_404_envelope(
    client: AsyncClient, api_prefix: str, method: str, path: str
) -> None:
    resp = await client.request(method, f"{api_prefix}{path}", json={})

    assert resp.status_code == 404, f"{method} {path} returned {resp.status_code}"
    error = resp.json()["error"]
    assert error["code"] == "FARM_NOT_FOUND"
    assert error["details"]["farm_id"] == MISSING_FARM
    assert error["request_id"]


async def test_unknown_run_is_404(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.get(f"{api_prefix}/analysis/{MISSING_RUN}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NO_ANALYSIS_YET"


async def test_unknown_image_is_404(client: AsyncClient, api_prefix: str) -> None:
    for method, path in [
        ("GET", f"/crop-images/{MISSING_IMAGE}"),
        ("POST", f"/crop-images/{MISSING_IMAGE}/analyze"),
    ]:
        resp = await client.request(method, f"{api_prefix}{path}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "IMAGE_NOT_FOUND"


async def test_unknown_crop_on_valid_farm_is_404(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    resp = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crops", json={"crop_id": MISSING_CROP}
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CROP_NOT_FOUND"


async def test_error_paths_carry_request_id(client: AsyncClient, api_prefix: str) -> None:
    """Correlation must work on failures, not only on successes."""
    resp = await client.get(f"{api_prefix}/farms/{MISSING_FARM}")
    assert resp.headers["X-Request-Id"] == resp.json()["error"]["request_id"]


# --------------------------------------------------------------------------
# The envelope itself
# --------------------------------------------------------------------------


async def test_error_envelope_runtime_keys_are_exactly_four(
    client: AsyncClient, api_prefix: str
) -> None:
    """The wire format is fixed at exactly these four keys."""
    validation = await client.post(f"{api_prefix}/farms", json={"name": "X"})
    not_found = await client.get(f"{api_prefix}/nope")
    farm_missing = await client.get(f"{api_prefix}/farms/{MISSING_FARM}")

    for resp in (validation, not_found, farm_missing):
        body = resp.json()
        assert set(body) == {"error"}
        assert set(body["error"]) == {"code", "message", "details", "request_id"}
        assert isinstance(body["error"]["details"], dict)


async def test_validation_details_carry_field_entries(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.post(
        f"{api_prefix}/farms", json={"name": "X", "latitude": 999, "longitude": 0}
    )

    assert resp.status_code == 422
    entries = resp.json()["error"]["details"]["fields"]
    assert entries
    for entry in entries:
        assert set(entry) == {"field", "message", "type"}
        assert all(isinstance(v, str) for v in entry.values())


async def test_non_validation_details_stay_free_form(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.get(f"{api_prefix}/farms/{MISSING_FARM}")

    details = resp.json()["error"]["details"]
    assert "fields" not in details
    assert details["farm_id"]


# --------------------------------------------------------------------------
# Validation runs before any handler
# --------------------------------------------------------------------------


async def test_invalid_body_is_422(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.post(
        f"{api_prefix}/farms", json={"name": "X", "latitude": 999, "longitude": 0}
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    fields = {f["field"] for f in resp.json()["error"]["details"]["fields"]}
    assert "latitude" in fields


async def test_missing_required_field_is_422(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.post(f"{api_prefix}/farms", json={"name": "No coordinates"})

    assert resp.status_code == 422
    fields = {f["field"] for f in resp.json()["error"]["details"]["fields"]}
    assert {"latitude", "longitude"} <= fields


async def test_malformed_uuid_path_param_is_422(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.get(f"{api_prefix}/farms/not-a-uuid")

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_query_param_bounds_are_enforced(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.get(f"{api_prefix}/reference/locations", params={"q": "x"})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_harvest_before_planting_is_rejected(
    client: AsyncClient, api_prefix: str, farm: dict, maize_crop: dict
) -> None:
    """A cross-field rule, enforced at the edge rather than deep in a service."""
    resp = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crops",
        json={
            "crop_id": maize_crop["id"],
            "planting_date": "2026-05-01",
            "expected_harvest_date": "2026-04-01",
        },
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_upload_rejects_unsupported_media_type(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    resp = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crop-images",
        files={"file": ("notes.txt", io.BytesIO(b"not an image"), "text/plain")},
    )

    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


# --------------------------------------------------------------------------
# Endpoints answered without any farm
# --------------------------------------------------------------------------


async def test_enum_catalog_is_live(client: AsyncClient, api_prefix: str) -> None:
    """The frontend builds every dropdown from this."""
    resp = await client.get(f"{api_prefix}/reference/enums")

    assert resp.status_code == 200
    enums = resp.json()["enums"]
    assert {"irrigation_type", "growth_stage", "advisory_priority", "data_mode"} <= set(enums)
    assert {"rainfed", "drip", "sprinkler"} <= {o["value"] for o in enums["irrigation_type"]}
    assert all("label" in o for o in enums["irrigation_type"])


async def test_data_mode_enum_exposes_simulated(client: AsyncClient, api_prefix: str) -> None:
    """`simulated` is how the API admits data is not a real observation."""
    resp = await client.get(f"{api_prefix}/reference/enums")

    modes = {o["value"] for o in resp.json()["enums"]["data_mode"]}
    assert {"live", "cached", "simulated", "unavailable"} == modes
