"""Crop and regenerative-agriculture recommendations.

Both are projections of the latest analysis run — no external calls, no AI work at
request time. Ranking is deterministic (soil/climate suitability scoring and
soil-condition rules); the AI only supplies the narrative `rationale`, so the order a
user sees is reproducible.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query

from app.schemas.recommendation import CropRecommendationList, RegenerativeRecommendationList
from app.services import analysis_service

router = APIRouter(prefix="/farms", tags=["recommendations"])

FarmId = Annotated[UUID, Path(description="Farm identifier.")]

_ERRORS = {404: {"description": "FARM_NOT_FOUND or NO_ANALYSIS_YET"}}


@router.get(
    "/{farm_id}/recommendations/crops",
    response_model=CropRecommendationList,
    summary="Crop recommendations",
    description=(
        "Catalog crops ranked by deterministic suitability against this farm's soil and "
        "climate, with the AI's reasoning attached to each."
    ),
    responses=_ERRORS,
)
async def get_crop_recommendations(
    farm_id: FarmId,
    limit: Annotated[int, Query(ge=1, le=25, description="How many to return.")] = 5,
) -> CropRecommendationList:
    return analysis_service.crop_recommendations(farm_id, limit=limit)


@router.get(
    "/{farm_id}/recommendations/regenerative",
    response_model=RegenerativeRecommendationList,
    summary="Regenerative agriculture recommendations",
    description=(
        "Regenerative practices scored against this farm's soil organic carbon, texture "
        "and current farming practice, with expected soil-carbon and water-retention effects."
    ),
    responses=_ERRORS,
)
async def get_regenerative_recommendations(
    farm_id: FarmId,
    limit: Annotated[int, Query(ge=1, le=25)] = 5,
) -> RegenerativeRecommendationList:
    return analysis_service.regenerative_recommendations(farm_id, limit=limit)
