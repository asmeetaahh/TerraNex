"""Health endpoints and the cross-cutting guarantees every route must uphold."""

import pytest
from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.get(f"{api_prefix}/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "TerraNex API"
    assert body["version"]
    assert body["timestamp"]


async def test_readiness_lists_dependencies(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.get(f"{api_prefix}/health/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "degraded", "unavailable"}
    names = {d["name"] for d in body["dependencies"]}
    assert {"database", "ai", "auth"} <= names


async def test_every_response_carries_request_id(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.get(f"{api_prefix}/health")
    assert resp.headers["X-Request-Id"].startswith("req_")


async def test_client_supplied_request_id_is_echoed(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.get(f"{api_prefix}/health", headers={"X-Request-Id": "req_from_frontend"})
    assert resp.headers["X-Request-Id"] == "req_from_frontend"


async def test_unknown_route_uses_the_error_envelope(client: AsyncClient, api_prefix: str) -> None:
    """A 404 must look exactly like every other failure — one parser on the frontend."""
    resp = await client.get(f"{api_prefix}/does-not-exist")

    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "RESOURCE_NOT_FOUND"
    assert isinstance(error["message"], str)
    assert isinstance(error["details"], dict)
    assert error["request_id"]


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173"])
async def test_cors_allows_the_frontend_dev_origins(
    client: AsyncClient, api_prefix: str, origin: str
) -> None:
    resp = await client.get(f"{api_prefix}/health", headers={"Origin": origin})

    assert resp.headers["access-control-allow-origin"] == origin
    assert resp.headers["access-control-allow-credentials"] == "true"
    assert "X-Request-Id" in resp.headers.get("access-control-expose-headers", "")


async def test_cors_preflight_succeeds(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.options(
        f"{api_prefix}/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
