"""Aggregates every v1 route module into a single router.

Order matters for OpenAPI tag ordering and for path matching: `farms` is registered
before `analysis` and `environment` so that literal segments are declared alongside
their owning resource.
"""

from fastapi import APIRouter

from app.api.v1.routes import (
    analysis,
    environment,
    farms,
    health,
    images,
    recommendations,
    reference,
)

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(reference.router)
api_router.include_router(farms.router)
api_router.include_router(environment.router)
api_router.include_router(analysis.router)
api_router.include_router(recommendations.router)
api_router.include_router(images.router)
