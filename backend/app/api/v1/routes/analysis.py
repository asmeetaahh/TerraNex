"""Farm analysis: the product's core, plus cheap projections of a stored run.

`POST /farms/{farm_id}/analysis` is the **only** endpoint that performs farm reasoning.
It gathers provider data, runs the deterministic risk engine, makes one AI call,
validates the output, and persists an immutable run.

Every other endpoint in this module reads that stored run. They perform no external
calls, no recompute, and no AI work — so the dashboard can render a dozen panels at
the cost of one analysis.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query

from app.schemas.advisory import AdvisoryList
from app.schemas.analysis import AnalysisRun, AnalysisRunList, FarmDashboard
from app.schemas.enums import AdvisoryCategory, AdvisoryPriority
from app.schemas.risk import DiseaseRisk, WaterRisk, WeatherRisk
from app.schemas.vegetation import CropHealth
from app.services import analysis_service

router = APIRouter(tags=["analysis"])

FarmId = Annotated[UUID, Path(description="Farm identifier.")]

# Projections all fail the same two ways.
_PROJECTION_ERRORS = {404: {"description": "FARM_NOT_FOUND or NO_ANALYSIS_YET"}}


# --------------------------------------------------------------------------
# Producing an analysis
# --------------------------------------------------------------------------


@router.post(
    "/farms/{farm_id}/analysis",
    response_model=AnalysisRun,
    summary="Run a farm analysis",
    description=(
        "Gathers weather, soil and vegetation data, runs the deterministic risk engine, "
        "then asks the AI to interpret the computed results. Persists an immutable run.\n\n"
        "**This is the only endpoint that performs farm AI reasoning.** All other analysis "
        "endpoints read the stored result.\n\n"
        "A run with identical inputs inside the cache window is returned as-is; pass "
        "`force_refresh=true` to recompute.\n\n"
        "Partial failure does not fail the request: unavailable providers are listed in "
        '`degraded_sources` with `status: "partial"`, and if the AI is unavailable the '
        'narrative falls back to deterministic text with `ai_mode: "fallback"`. Check '
        "`ai_mode` and `sources[].mode` to see exactly what produced the result."
    ),
    responses={
        404: {"description": "FARM_NOT_FOUND"},
        409: {"description": "ANALYSIS_IN_PROGRESS"},
        502: {"description": "PROVIDER_UNAVAILABLE — every data source failed"},
        503: {"description": "AI_UNAVAILABLE — AI failed and no fallback was possible"},
    },
)
async def run_analysis(
    farm_id: FarmId,
    force_refresh: Annotated[
        bool, Query(description="Bypass the cache and recompute from scratch.")
    ] = False,
) -> AnalysisRun:
    return await analysis_service.run_analysis(farm_id, force_refresh=force_refresh)


@router.get(
    "/farms/{farm_id}/analysis/latest",
    response_model=AnalysisRun,
    summary="Latest analysis run",
    description="The most recent stored run. Returns NO_ANALYSIS_YET if none exists.",
    responses=_PROJECTION_ERRORS,
)
async def get_latest_analysis(farm_id: FarmId) -> AnalysisRun:
    return analysis_service.latest_analysis(farm_id)


@router.get(
    "/farms/{farm_id}/analysis",
    response_model=AnalysisRunList,
    summary="Analysis history",
    description="Lightweight run summaries, newest first — the source for trend charts.",
    responses={404: {"description": "FARM_NOT_FOUND"}},
)
async def list_analysis_runs(
    farm_id: FarmId,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> AnalysisRunList:
    return analysis_service.list_runs(farm_id, page=page, page_size=page_size)


@router.get(
    "/analysis/{run_id}",
    response_model=AnalysisRun,
    summary="Get an analysis run by id",
    description="Permalink to a specific run. Runs are immutable, so this is stable.",
    responses={404: {"description": "RESOURCE_NOT_FOUND"}},
    tags=["analysis"],
)
async def get_analysis_run(
    run_id: Annotated[UUID, Path(description="Analysis run identifier.")],
) -> AnalysisRun:
    return analysis_service.get_run(run_id)


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


@router.get(
    "/farms/{farm_id}/dashboard",
    response_model=FarmDashboard,
    summary="Unified farm dashboard",
    description=(
        "Everything the dashboard needs in one request: the farm, its crops, the latest "
        "analysis, current weather, recent images, and per-panel data provenance.\n\n"
        "**Does not 404 when no analysis exists.** It returns `has_analysis: false` and "
        "`analysis: null` so the UI can render a 'Run analysis' empty state instead of "
        "handling an error."
    ),
    responses={404: {"description": "FARM_NOT_FOUND"}},
)
async def get_dashboard(farm_id: FarmId) -> FarmDashboard:
    return await analysis_service.dashboard(farm_id)


# --------------------------------------------------------------------------
# Projections of the latest run — no external calls, no AI, no recompute
# --------------------------------------------------------------------------


@router.get(
    "/farms/{farm_id}/risks/weather",
    response_model=WeatherRisk,
    summary="Weather risk",
    description="Weather-risk section of the latest analysis run.",
    responses=_PROJECTION_ERRORS,
)
async def get_weather_risk(farm_id: FarmId) -> WeatherRisk:
    return analysis_service.weather_risk(farm_id)


@router.get(
    "/farms/{farm_id}/risks/water",
    response_model=WaterRisk,
    summary="Water and irrigation risk",
    description=(
        "Water-risk section of the latest analysis run: soil water balance, projected "
        "days to stress, and a recommended irrigation depth."
    ),
    responses=_PROJECTION_ERRORS,
)
async def get_water_risk(farm_id: FarmId) -> WaterRisk:
    return analysis_service.water_risk(farm_id)


@router.get(
    "/farms/{farm_id}/risks/disease",
    response_model=DiseaseRisk,
    summary="Disease risk",
    description=(
        "Disease-risk section of the latest analysis run. Each pathogen is evaluated by "
        "an explicit agronomic rule over temperature, humidity, duration and growth stage."
    ),
    responses=_PROJECTION_ERRORS,
)
async def get_disease_risk(farm_id: FarmId) -> DiseaseRisk:
    return analysis_service.disease_risk(farm_id)


@router.get(
    "/farms/{farm_id}/health",
    response_model=CropHealth,
    summary="Crop health",
    description=(
        "Crop-health section of the latest analysis run: vigour, growth stage, stress signals."
    ),
    responses=_PROJECTION_ERRORS,
)
async def get_crop_health(farm_id: FarmId) -> CropHealth:
    return analysis_service.crop_health(farm_id)


@router.get(
    "/farms/{farm_id}/advisories",
    response_model=AdvisoryList,
    summary="AI agricultural advisories",
    description=(
        "Advisories from the latest analysis run, highest priority first. Each carries a "
        "`rationale` citing the computed values behind it."
    ),
    responses=_PROJECTION_ERRORS,
)
async def list_advisories(
    farm_id: FarmId,
    category: Annotated[AdvisoryCategory | None, Query()] = None,
    priority: Annotated[AdvisoryPriority | None, Query()] = None,
    include_dismissed: Annotated[bool, Query()] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AdvisoryList:
    return analysis_service.advisories(
        farm_id,
        category=category,
        priority=priority,
        include_dismissed=include_dismissed,
        page=page,
        page_size=page_size,
    )
