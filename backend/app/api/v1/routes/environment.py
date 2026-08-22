"""Raw environmental data for a farm: weather, soil, vegetation.

These are cached read-throughs to external providers, independent of any analysis run
— they exist so the frontend can draw charts without triggering AI reasoning. No
endpoint here involves Gemini.

Every response carries `meta.mode`. Until real providers are wired in it reports
`simulated`, and the UI must badge it accordingly.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from app.core.deps import CurrentUser, get_current_user
from app.schemas.soil import SoilProfile
from app.schemas.vegetation import VegetationSeries
from app.schemas.weather import WeatherBundle
from app.services import environment_service

router = APIRouter(prefix="/farms", tags=["environment"])

# The resolved identity every farm-scoped operation is checked against. Contributes
# nothing to the OpenAPI document, so the frozen contract stays byte-identical.
Caller = Annotated[CurrentUser, Depends(get_current_user)]

FarmId = Annotated[UUID, Path(description="Farm identifier.")]

_NOT_FOUND = {404: {"description": "FARM_NOT_FOUND"}}


@router.get(
    "/{farm_id}/weather",
    response_model=WeatherBundle,
    summary="Weather for a farm",
    description=(
        "Current conditions, hourly and daily forecast, and a summary of the "
        "historical window — in one payload, because the dashboard needs all of it.\n\n"
        "Hourly resolution matters: disease rules are defined over consecutive hours "
        "of humidity and temperature, not daily means."
    ),
    responses=_NOT_FOUND,
)
async def get_weather(
    farm_id: FarmId,
    user: Caller,
    forecast_days: Annotated[int, Query(ge=1, le=16)] = 7,
    history_days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> WeatherBundle:
    return await environment_service.weather_for_farm(
        farm_id, forecast_days=forecast_days, history_days=history_days, user=user
    )


@router.get(
    "/{farm_id}/soil",
    response_model=SoilProfile,
    summary="Soil profile for a farm",
    description=(
        "Physical and chemical soil properties at the farm's coordinates, plus the "
        "derived plant-available water capacity that feeds the water balance."
    ),
    responses=_NOT_FOUND,
)
async def get_soil(farm_id: FarmId, user: Caller) -> SoilProfile:
    return await environment_service.soil_for_farm(farm_id, user)


@router.get(
    "/{farm_id}/vegetation",
    response_model=VegetationSeries,
    summary="Vegetation index series for a farm",
    description=(
        "NDVI/EVI time series with a trend summary. The vegetation provider is an "
        "abstraction: a real satellite source can replace the current one with no "
        "contract change. Read `meta.mode` to know which produced these values."
    ),
    responses=_NOT_FOUND,
)
async def get_vegetation(
    farm_id: FarmId,
    user: Caller,
    days: Annotated[int, Query(ge=7, le=365)] = 90,
) -> VegetationSeries:
    return await environment_service.vegetation_for_farm(farm_id, days=days, user=user)
