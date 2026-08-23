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


# --------------------------------------------------------------------------
# Database readiness
#
# `/health/ready` reported `database: "ok"` whenever `DATABASE_URL` was a non-empty
# string. That asserted a setting was present, not that a database would answer — so a
# host pointed at an unreachable, unmigrated or credential-rejected database advertised
# itself as ready and was handed traffic it could only fail.
#
# The probe now executes `SELECT 1`. `test_an_unreachable_database_is_unavailable` is the
# regression: it passes against the old code only because the old code never looked.
# --------------------------------------------------------------------------


def database_status(body: dict) -> dict:
    return next(d for d in body["dependencies"] if d["name"] == "database")


async def test_no_database_configured_is_not_configured_not_ok(
    client: AsyncClient, api_prefix: str
) -> None:
    """The suite default. An in-memory deployment is a supported configuration, not a
    failure — but it is not `ok` either, because there is no database to be ready."""
    body = (await client.get(f"{api_prefix}/health/ready")).json()

    database = database_status(body)
    assert database["status"] == "not_configured"
    assert "in-memory" in (database["detail"] or "")
    assert body["status"] == "degraded", "a partially-configured host is not ready"


async def test_a_reachable_database_is_ok_and_reports_latency(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """A real database, really queried. `latency_ms` is evidence the round trip happened
    rather than being asserted from configuration."""
    body = (await client.get(f"{api_prefix}/health/ready")).json()

    database = database_status(body)
    assert database["status"] == "ok"
    assert database["latency_ms"] is not None
    assert database["latency_ms"] >= 0


async def test_an_unreachable_database_is_unavailable(
    client: AsyncClient, api_prefix: str, monkeypatch
) -> None:
    """The regression, stated exactly.

    A configured but unreachable database must report `unavailable`, not `ok`. Under the
    previous implementation this returned `ok` — the string was non-empty, so the check
    passed and the orchestrator sent traffic to a host that could not serve it.
    """
    from app.api.v1.routes import health

    monkeypatch.setattr(health.settings, "DATABASE_URL", "postgresql+psycopg://x@127.0.0.1:1/none")

    def refuse() -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(health, "_select_one", refuse)

    body = (await client.get(f"{api_prefix}/health/ready")).json()

    database = database_status(body)
    assert database["status"] == "unavailable"
    assert body["status"] == "unavailable", "an unreachable database makes the host not ready"


async def test_a_hanging_database_times_out_rather_than_hanging_the_probe(
    client: AsyncClient, api_prefix: str, monkeypatch
) -> None:
    """An orchestrator polls this on a short interval. A probe that waits as long as a
    real request would turn one stalled database into a pile of stalled probes."""
    import time

    from app.api.v1.routes import health

    monkeypatch.setattr(health.settings, "DATABASE_URL", "postgresql+psycopg://x@127.0.0.1:1/none")
    monkeypatch.setattr(health, "DATABASE_PROBE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(health, "_select_one", lambda: time.sleep(5))

    started = time.perf_counter()
    body = (await client.get(f"{api_prefix}/health/ready")).json()
    elapsed = time.perf_counter() - started

    assert database_status(body)["status"] == "unavailable"
    assert elapsed < 3.0, "the probe must not wait for a hung database"


async def test_the_probe_never_leaks_the_connection_string(
    client: AsyncClient, api_prefix: str, monkeypatch
) -> None:
    """`/health/ready` takes no auth dependency, so `detail` is world-readable and a DSN
    carries the database password."""
    from app.api.v1.routes import health

    dsn = "postgresql+psycopg://admin:SUPERSECRET@db.internal:5432/terranex"
    monkeypatch.setattr(health.settings, "DATABASE_URL", dsn)

    def leak() -> None:
        raise OSError(f"could not connect using {dsn}")

    monkeypatch.setattr(health, "_select_one", leak)

    body = (await client.get(f"{api_prefix}/health/ready")).json()

    detail = database_status(body)["detail"] or ""
    assert "SUPERSECRET" not in detail
    assert dsn not in detail
    assert "***" in detail


async def test_readiness_never_raises_whatever_the_database_does(
    client: AsyncClient, api_prefix: str, monkeypatch
) -> None:
    """A readiness probe reports failures; it never becomes one. A 500 here would read as
    a dead process rather than a dependency problem."""
    from app.api.v1.routes import health

    monkeypatch.setattr(health.settings, "DATABASE_URL", "postgresql+psycopg://x@127.0.0.1:1/none")

    for boom in (RuntimeError("boom"), OSError("refused"), ValueError("bad dsn")):

        def raise_it(exc=boom) -> None:
            raise exc

        monkeypatch.setattr(health, "_select_one", raise_it)
        resp = await client.get(f"{api_prefix}/health/ready")

        assert resp.status_code == 200
        assert database_status(resp.json())["status"] == "unavailable"


async def test_liveness_still_checks_nothing(client: AsyncClient, api_prefix: str, monkeypatch):
    """`/health` answers "is this process running", which a database cannot change. It
    must stay 200 while the database is down, or a dependency outage would restart every
    healthy pod."""
    from app.api.v1.routes import health

    monkeypatch.setattr(health.settings, "DATABASE_URL", "postgresql+psycopg://x@127.0.0.1:1/none")

    def refuse() -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(health, "_select_one", refuse)

    resp = await client.get(f"{api_prefix}/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


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
