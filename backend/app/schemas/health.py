"""Health / readiness schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness: the process is up and serving."""

    status: Literal["ok"] = "ok"
    service: str = Field(examples=["TerraNex API"])
    version: str = Field(examples=["0.1.0"])
    environment: str = Field(examples=["local"])
    timestamp: datetime


class DependencyStatus(BaseModel):
    name: str = Field(examples=["database"])
    status: Literal["ok", "degraded", "unavailable", "not_configured"]
    detail: str | None = None
    latency_ms: float | None = None


class ReadinessResponse(BaseModel):
    """Readiness: the process is up *and* its dependencies are reachable."""

    status: Literal["ok", "degraded", "unavailable"]
    version: str
    environment: str
    timestamp: datetime
    dependencies: list[DependencyStatus]
