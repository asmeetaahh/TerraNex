"""TerraNex API — application factory and entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.db.seed import seed_crops
from app.providers.http import close_client
from app.schemas.errors import ErrorResponse

logger = get_logger(__name__)

DESCRIPTION = """
**TerraNex** is the central intelligence layer for agricultural decision support.

It fuses farm, crop, soil, weather and vegetation data with AI reasoning to produce
farm health analysis, weather and water risk, disease risk, agricultural advisories,
crop and regenerative-agriculture recommendations, and multimodal crop-image diagnosis.

### For frontend developers

* Every endpoint lives under `/api/v1`. Field casing is `snake_case` throughout.
* Every failure returns the same envelope:
  `{"error": {"code", "message", "details", "request_id"}}`.
  **Branch on `code`** — `message` is for humans and may change.
* Every response carries `X-Request-Id`; include it when reporting a bug.
* Generate typed bindings instead of hand-writing them:
  `npx openapi-typescript ../contracts/openapi.json -o src/api/types.gen.ts`
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.LOG_LEVEL, json_output=settings.APP_ENV != "local")

    # Phase 3: the crop catalog lives in the in-memory store. This moves to a
    # database migration in the persistence phase.
    crop_count = seed_crops()

    logger.info(
        "startup",
        extra={
            "env": settings.APP_ENV,
            "version": settings.APP_VERSION,
            "ai_provider": settings.AI_PROVIDER,
            "auth_enabled": settings.ENABLE_AUTH,
            "cors_origins": settings.CORS_ORIGINS,
            "crops_seeded": crop_count,
            "weather_provider": settings.WEATHER_PROVIDER,
            "geocoding_provider": settings.GEOCODING_PROVIDER,
        },
    )
    yield

    await close_client()
    logger.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=DESCRIPTION,
        docs_url=settings.docs_url,
        redoc_url=None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # Order matters: RequestContextMiddleware must wrap everything so that
    # request.state.request_id exists by the time an error handler runs.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,  # explicit list, never "*" with credentials
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )

    register_exception_handlers(app)

    # `default` documents "any non-declared status returns the error envelope",
    # which is literally true here and publishes ErrorResponse into the schema so
    # the frontend can generate a typed ApiError from it.
    app.include_router(
        api_router,
        prefix=settings.API_V1_PREFIX,
        responses={"default": {"model": ErrorResponse, "description": "Error envelope"}},
    )

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": settings.docs_url or "disabled",
            "api": settings.API_V1_PREFIX,
        }

    return app


app = create_app()
