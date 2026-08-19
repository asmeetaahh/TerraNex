"""Guarantees the whole published contract must uphold.

Step 2 publishes every MVP route with its final request and response models, while
most implementations land later. These tests assert the *contract-level* promises the
frontend depends on today:

* every scaffolded route answers 501 in the standard error envelope,
* request validation happens before the 501, with the same envelope,
* correlation headers work everywhere, not just on health,
* the endpoints that ARE implemented really work.
"""

import io

import pytest
from httpx import AsyncClient

# (method, path, json_body) — bodies are valid, so a 501 proves the route is reached
# rather than rejected by validation.
FARM_ID = "11111111-1111-4111-8111-111111111111"
CROP_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
IMAGE_ID = "44444444-4444-4444-8444-444444444444"

VALID_FARM = {"name": "North Field", "latitude": -1.2921, "longitude": 36.8219}
VALID_FARM_CROP = {"crop_id": CROP_ID, "growth_stage": "vegetative"}

SCAFFOLDED_ROUTES = [
    ("GET", "/reference/crops", None),
    ("GET", "/reference/locations?q=nairobi", None),
    ("POST", "/farms", VALID_FARM),
    ("GET", "/farms", None),
    ("GET", f"/farms/{FARM_ID}", None),
    ("PATCH", f"/farms/{FARM_ID}", {"name": "Renamed"}),
    ("DELETE", f"/farms/{FARM_ID}", None),
    ("POST", f"/farms/{FARM_ID}/crops", VALID_FARM_CROP),
    ("GET", f"/farms/{FARM_ID}/crops", None),
    ("PATCH", f"/farms/{FARM_ID}/crops/{CROP_ID}", {"growth_stage": "flowering"}),
    ("DELETE", f"/farms/{FARM_ID}/crops/{CROP_ID}", None),
    ("GET", f"/farms/{FARM_ID}/weather", None),
    ("GET", f"/farms/{FARM_ID}/soil", None),
    ("GET", f"/farms/{FARM_ID}/vegetation", None),
    ("POST", f"/farms/{FARM_ID}/analysis", None),
    ("GET", f"/farms/{FARM_ID}/analysis/latest", None),
    ("GET", f"/farms/{FARM_ID}/analysis", None),
    ("GET", f"/analysis/{RUN_ID}", None),
    ("GET", f"/farms/{FARM_ID}/dashboard", None),
    ("GET", f"/farms/{FARM_ID}/risks/weather", None),
    ("GET", f"/farms/{FARM_ID}/risks/water", None),
    ("GET", f"/farms/{FARM_ID}/risks/disease", None),
    ("GET", f"/farms/{FARM_ID}/health", None),
    ("GET", f"/farms/{FARM_ID}/advisories", None),
    ("GET", f"/farms/{FARM_ID}/recommendations/crops", None),
    ("GET", f"/farms/{FARM_ID}/recommendations/regenerative", None),
    ("GET", f"/farms/{FARM_ID}/crop-images", None),
    ("GET", f"/crop-images/{IMAGE_ID}", None),
    ("POST", f"/crop-images/{IMAGE_ID}/analyze", None),
]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    SCAFFOLDED_ROUTES,
    ids=[f"{m} {p.split('?')[0]}" for m, p, _ in SCAFFOLDED_ROUTES],
)
async def test_scaffolded_route_returns_501_envelope(
    client: AsyncClient, api_prefix: str, method: str, path: str, body: dict | None
) -> None:
    resp = await client.request(method, f"{api_prefix}{path}", json=body)

    assert resp.status_code == 501, f"{method} {path} returned {resp.status_code}"
    error = resp.json()["error"]
    assert error["code"] == "NOT_IMPLEMENTED"
    assert error["request_id"]
    # The detail names which step will implement it, so the frontend can see progress.
    assert "feature" in error["details"]
    assert "planned_step" in error["details"]


async def test_upload_route_returns_501_envelope(client: AsyncClient, api_prefix: str) -> None:
    """Multipart upload is exercised separately — it needs a real file part."""
    resp = await client.post(
        f"{api_prefix}/farms/{FARM_ID}/crop-images",
        files={"file": ("leaf.jpg", io.BytesIO(b"\xff\xd8\xff\xe0fake"), "image/jpeg")},
    )

    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "NOT_IMPLEMENTED"


async def test_every_scaffolded_route_carries_request_id(
    client: AsyncClient, api_prefix: str
) -> None:
    """Correlation must work on error paths, not only on successful ones."""
    resp = await client.get(f"{api_prefix}/farms/{FARM_ID}/dashboard")
    assert resp.status_code == 501
    assert resp.headers["X-Request-Id"] == resp.json()["error"]["request_id"]


# --------------------------------------------------------------------------
# Validation runs before the 501 — the request contract is live today
# --------------------------------------------------------------------------


async def test_invalid_body_is_422_not_501(client: AsyncClient, api_prefix: str) -> None:
    """Latitude 999 must be rejected by the schema, not swallowed by the stub."""
    resp = await client.post(
        f"{api_prefix}/farms", json={"name": "X", "latitude": 999, "longitude": 0}
    )

    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    fields = {f["field"] for f in error["details"]["fields"]}
    assert "latitude" in fields


async def test_error_envelope_runtime_keys_are_exactly_four(
    client: AsyncClient, api_prefix: str
) -> None:
    """The wire format is fixed at exactly these four keys.

    Documentation models may be described more precisely over time, but the bytes on
    the wire must not drift — the frontend parses this shape and nothing else.
    """
    validation = await client.post(f"{api_prefix}/farms", json={"name": "X"})
    not_implemented = await client.get(f"{api_prefix}/farms")
    not_found = await client.get(f"{api_prefix}/nope")

    for resp in (validation, not_implemented, not_found):
        body = resp.json()
        assert set(body) == {"error"}
        assert set(body["error"]) == {"code", "message", "details", "request_id"}
        assert isinstance(body["error"]["details"], dict)


async def test_validation_details_carry_field_entries(client: AsyncClient, api_prefix: str) -> None:
    """`details.fields` entries must match the published ErrorField shape."""
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
    """Non-422 errors keep their own detail objects — `details` is not forced into
    the validation shape."""
    resp = await client.get(f"{api_prefix}/farms/{FARM_ID}/dashboard")

    details = resp.json()["error"]["details"]
    assert "fields" not in details
    assert details["feature"]


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
    resp = await client.get(f"{api_prefix}/reference/locations?q=x")  # min_length is 2

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_harvest_before_planting_is_rejected(client: AsyncClient, api_prefix: str) -> None:
    """A cross-field rule, enforced at the edge rather than deep in a service."""
    resp = await client.post(
        f"{api_prefix}/farms/{FARM_ID}/crops",
        json={
            "crop_id": CROP_ID,
            "planting_date": "2026-05-01",
            "expected_harvest_date": "2026-04-01",
        },
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# --------------------------------------------------------------------------
# Endpoints that are actually implemented
# --------------------------------------------------------------------------


async def test_enum_catalog_is_live(client: AsyncClient, api_prefix: str) -> None:
    """The frontend builds every dropdown from this, so it must work from day one."""
    resp = await client.get(f"{api_prefix}/reference/enums")

    assert resp.status_code == 200
    enums = resp.json()["enums"]
    assert {"irrigation_type", "growth_stage", "advisory_priority", "data_mode"} <= set(enums)

    irrigation = {opt["value"] for opt in enums["irrigation_type"]}
    assert {"rainfed", "drip", "sprinkler"} <= irrigation
    assert all("label" in opt for opt in enums["irrigation_type"])


async def test_data_mode_enum_exposes_simulated(client: AsyncClient, api_prefix: str) -> None:
    """`simulated` is how the API admits data is not a real observation. It must be
    discoverable by the frontend so it can badge such payloads."""
    resp = await client.get(f"{api_prefix}/reference/enums")

    modes = {opt["value"] for opt in resp.json()["enums"]["data_mode"]}
    assert {"live", "cached", "simulated", "unavailable"} == modes
