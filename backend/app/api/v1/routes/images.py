"""Multimodal crop-image disease diagnosis.

Upload and analysis are separate calls so the UI can show a thumbnail immediately
while the slower vision request runs. `?analyze=true` on upload collapses both into
one round trip for the simple path.

The frontend uploads to this API and never to an AI provider directly.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile, status

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user
from app.schemas.image import CropImage, CropImageList
from app.services import image_service

router = APIRouter(tags=["crop-images"])

# The resolved identity every farm-scoped operation is checked against. Contributes
# nothing to the OpenAPI document, so the frozen contract stays byte-identical.
Caller = Annotated[CurrentUser, Depends(get_current_user)]

FarmId = Annotated[UUID, Path(description="Farm identifier.")]
ImageId = Annotated[UUID, Path(description="Crop image identifier.")]


@router.post(
    "/farms/{farm_id}/crop-images",
    response_model=CropImage,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a crop image",
    description=(
        f"Accepts `multipart/form-data`. Max {settings.MAX_UPLOAD_MB} MB; allowed types: "
        f"{', '.join(settings.ALLOWED_IMAGE_TYPES)}.\n\n"
        'Returns immediately with `analysis_status: "pending"` so the UI can render the '
        "thumbnail at once. Pass `analyze=true` to run the diagnosis in the same request."
    ),
    responses={
        404: {"description": "FARM_NOT_FOUND"},
        413: {"description": "IMAGE_TOO_LARGE"},
        415: {"description": "UNSUPPORTED_MEDIA_TYPE"},
    },
)
async def upload_crop_image(
    farm_id: FarmId,
    user: Caller,
    file: Annotated[UploadFile, File(description="The crop photograph.")],
    farm_crop_id: Annotated[
        UUID | None, Form(description="Which planting this image belongs to.")
    ] = None,
    note: Annotated[
        str | None, Form(max_length=1000, description="Optional context for the diagnosis.")
    ] = None,
    analyze: Annotated[
        bool, Query(description="Run the diagnosis immediately instead of returning pending.")
    ] = False,
) -> CropImage:
    return image_service.upload_image(
        farm_id,
        data=await file.read(),
        content_type=file.content_type,
        filename=file.filename,
        user=user,
        farm_crop_id=farm_crop_id,
        note=note,
        analyze=analyze,
    )


@router.post(
    "/crop-images/{image_id}/analyze",
    response_model=CropImage,
    summary="Diagnose a crop image",
    description=(
        "Runs multimodal analysis on a previously uploaded image, using the farm's crop "
        "and weather context to sharpen the diagnosis.\n\n"
        "The result always states `is_plant_material`, lists `differential_diagnoses` "
        "rather than a single overconfident answer, and carries a `disclaimer`. Check "
        "`ai_mode` to see whether a real model or a mock produced it."
    ),
    responses={
        404: {"description": "IMAGE_NOT_FOUND"},
        409: {"description": "ANALYSIS_IN_PROGRESS"},
        503: {"description": "AI_UNAVAILABLE"},
    },
)
async def analyze_crop_image(image_id: ImageId, user: Caller) -> CropImage:
    return image_service.analyze_image(image_id, user)


@router.get(
    "/crop-images/{image_id}",
    response_model=CropImage,
    summary="Get a crop image",
    description="Poll this while `analysis_status` is `pending` or `analyzing`.",
    responses={404: {"description": "IMAGE_NOT_FOUND"}},
)
async def get_crop_image(image_id: ImageId, user: Caller) -> CropImage:
    return image_service.get_image(image_id, user)


@router.get(
    "/farms/{farm_id}/crop-images",
    response_model=CropImageList,
    summary="List a farm's crop images",
    description="Uploaded images with their diagnoses, newest first.",
    responses={404: {"description": "FARM_NOT_FOUND"}},
)
async def list_crop_images(
    farm_id: FarmId,
    user: Caller,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> CropImageList:
    return image_service.list_images(farm_id, page=page, page_size=page_size, user=user)
