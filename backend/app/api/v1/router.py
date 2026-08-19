"""Aggregates every v1 route module into a single router.

Phase 1 adds: farms, analysis, recommendations, images, reference.
"""

from fastapi import APIRouter

from app.api.v1.routes import health

api_router = APIRouter()
api_router.include_router(health.router)
