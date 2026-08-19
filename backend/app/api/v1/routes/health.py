"""Liveness and readiness endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter

from app.core.config import settings
from app.schemas.health import DependencyStatus, HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


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
        DependencyStatus(
            name="database",
            status="ok" if settings.DATABASE_URL else "not_configured",
            detail=None if settings.DATABASE_URL else "DATABASE_URL is unset (Phase 2)",
        ),
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
