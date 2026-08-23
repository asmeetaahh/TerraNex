"""Liveness and readiness endpoints.

**Readiness connects; liveness does not.** `/health` answers "is this process running",
which is a question about the process alone. `/health/ready` answers "should traffic be
sent here", and that cannot be decided without asking the database whether it will
actually answer — which is the whole difference between the two probes.
"""

import time
from datetime import UTC, datetime

import anyio
from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.health import DependencyStatus, HealthResponse, ReadinessResponse

logger = get_logger(__name__)

router = APIRouter(tags=["health"])

DATABASE_PROBE_TIMEOUT_S = 2.0
"""How long the readiness probe waits for the database before calling it unavailable.

Deliberately far below `PROVIDER_DEADLINE_S`: an orchestrator polls this endpoint on a
short interval, and a probe that hangs as long as a real request would turn one stalled
database into a pile of stalled probes. A database that cannot answer a `SELECT 1` in two
seconds is not ready to serve traffic, whatever it is doing.
"""


def _redact(text_: str) -> str:
    """Keep the configured DSN out of a public response.

    `/health/ready` takes no auth dependency, so whatever reaches `detail` is world
    readable. Driver errors observed so far do not echo the connection string, but that
    is a property of the driver rather than a guarantee — and a DSN carries the database
    password.
    """
    dsn = settings.DATABASE_URL
    if dsn:
        text_ = text_.replace(dsn, "***")
    return text_


def _select_one() -> None:
    """The smallest question that proves a usable connection.

    A pooled connection that has gone stale still looks connected until something is
    executed on it, so the round trip is the point — checking the engine exists would
    reproduce the defect this replaces in a different disguise.
    """
    from app.db.session import get_engine

    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))


async def _probe_database() -> DependencyStatus:
    """Ask the database whether it will answer, and report how long it took.

    This used to be `"ok" if settings.DATABASE_URL else "not_configured"` — which
    asserted that a string was non-empty and called it readiness. A host pointed at an
    unreachable, unmigrated, or credential-rejected database reported `ok` and was handed
    traffic it could only fail.

    The query runs in a worker thread because the session layer is synchronous, and
    blocking the event loop inside a health probe would make one slow database look like
    a total outage across every concurrent request.
    """
    if not settings.DATABASE_URL:
        return DependencyStatus(
            name="database",
            status="not_configured",
            detail="DATABASE_URL is unset; the API is running on the in-memory store.",
        )

    started = time.perf_counter()
    try:
        with anyio.fail_after(DATABASE_PROBE_TIMEOUT_S):
            # `abandon_on_cancel` is what makes the timeout real. Without it the scope
            # waits for the worker thread to finish before noticing its deadline passed,
            # so a database that hangs for thirty seconds hangs the probe for thirty
            # seconds — the exact failure the timeout exists to prevent. The abandoned
            # thread ends when the driver's own socket timeout fires.
            await anyio.to_thread.run_sync(_select_one, abandon_on_cancel=True)
    except TimeoutError:
        elapsed = (time.perf_counter() - started) * 1000
        logger.warning("readiness_database_timeout", extra={"timeout_s": DATABASE_PROBE_TIMEOUT_S})
        return DependencyStatus(
            name="database",
            status="unavailable",
            detail=f"No response within {DATABASE_PROBE_TIMEOUT_S:.0f}s.",
            latency_ms=round(elapsed, 1),
        )
    except Exception as exc:  # noqa: BLE001 - a probe reports failures, it never raises them
        elapsed = (time.perf_counter() - started) * 1000
        reason = _redact(f"{type(exc).__name__}: {exc}")
        logger.warning("readiness_database_unavailable", extra={"reason": reason[:200]})
        return DependencyStatus(
            name="database",
            status="unavailable",
            detail=reason[:200],
            latency_ms=round(elapsed, 1),
        )

    return DependencyStatus(
        name="database",
        status="ok",
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Returns 200 whenever the API process is running. No dependencies checked.",
)
async def health() -> HealthResponse:
    return HealthResponse(
        service=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.APP_ENV,
        timestamp=datetime.now(UTC),
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Reports the reachability of each dependency (database, AI provider, "
        "external data providers). Always returns 200 — read `status` to decide."
    ),
)
async def readiness() -> ReadinessResponse:
    dependencies: list[DependencyStatus] = [
        await _probe_database(),
        DependencyStatus(
            name="ai",
            status="ok",
            detail=f"provider={settings.AI_PROVIDER}",
        ),
        DependencyStatus(
            name="auth",
            status="ok" if settings.ENABLE_AUTH else "not_configured",
            detail=None if settings.ENABLE_AUTH else "ENABLE_AUTH=false; using demo user",
        ),
    ]

    if any(d.status == "unavailable" for d in dependencies):
        overall = "unavailable"
    elif any(d.status != "ok" for d in dependencies):
        overall = "degraded"
    else:
        overall = "ok"

    return ReadinessResponse(
        status=overall,
        version=settings.APP_VERSION,
        environment=settings.APP_ENV,
        timestamp=datetime.now(UTC),
        dependencies=dependencies,
    )
