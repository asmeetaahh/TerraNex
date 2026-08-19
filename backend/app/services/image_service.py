"""Crop-image upload and simulated diagnosis.

**No model is called here.** Phase 3 produces a deterministic mock diagnosis so the
full upload → analyse → display flow can be built and demonstrated; every result is
stamped `ai_mode="mock"`. The vision phase replaces `_simulate_diagnosis` and nothing
else — the shapes are already final.

Image bytes are validated and measured, then discarded: without object storage there
is nowhere honest to serve them from, so `url` stays `null` rather than pointing at
something that does not exist.
"""

import hashlib
import io
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.errors import (
    ErrorCode,
    ImageTooLargeError,
    NotFoundError,
    UnsupportedMediaTypeError,
)
from app.db.memory import store
from app.schemas.enums import AIMode, ImageAnalysisStatus, Severity
from app.schemas.image import (
    CropImage,
    CropImageAnalysis,
    CropImageList,
    DifferentialItem,
    TreatmentOption,
)
from app.services.farm_service import require_farm
from app.services.reference_service import get_crop, paginate
from app.services.simulation import seeded_rng

PROMPT_VERSION = "phase3-vision-fixture-v1"

DISCLAIMER = (
    "AI-assisted diagnosis for guidance only — confirm with a qualified agronomist "
    "before applying any treatment. This Phase 3 result is a simulated fixture and "
    "was not produced by an image model."
)

# Magic-byte prefixes, checked so a renamed file cannot pass as an image.
_MAGIC: dict[str, tuple[bytes, ...]] = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}

_CONDITIONS = [
    {
        "key": "healthy",
        "label": "Healthy",
        "severity": Severity.none,
        "symptoms": ["Uniform leaf colour", "No visible lesions", "Turgid canopy"],
        "actions": ["Continue the current management programme"],
        "prevention": ["Maintain balanced nutrition", "Keep scouting weekly"],
        "treatments": [],
    },
    {
        "key": "late_blight",
        "label": "Late blight",
        "severity": Severity.severe,
        "symptoms": [
            "Water-soaked lesions on leaf margins",
            "White sporulation on leaf undersides",
            "Rapid lesion expansion in humid conditions",
        ],
        "actions": [
            "Remove and destroy affected foliage",
            "Apply a protectant fungicide before the next wet period",
            "Avoid overhead irrigation",
        ],
        "prevention": [
            "Plant resistant varieties",
            "Increase row spacing for airflow",
            "Rotate away from solanaceous crops",
        ],
        "treatments": [
            {
                "name": "Copper-based fungicide",
                "approach": "organic",
                "description": "Protectant copper hydroxide spray on a 7-day interval.",
                "timing": "Apply at first sign, repeat after 7 days",
                "precautions": "Observe the pre-harvest interval; copper accumulates in soil.",
            },
            {
                "name": "Mancozeb",
                "approach": "chemical",
                "description": "Broad-spectrum protectant fungicide.",
                "timing": "Apply before a forecast wet period",
                "precautions": "Use protective equipment; rotate modes of action.",
            },
        ],
    },
    {
        "key": "early_blight",
        "label": "Early blight",
        "severity": Severity.moderate,
        "symptoms": [
            "Concentric target-like lesions on older leaves",
            "Yellow halo around lesions",
            "Progression from the lower canopy",
        ],
        "actions": [
            "Remove severely affected lower leaves",
            "Improve airflow at the base of the canopy",
        ],
        "prevention": ["Rotate crops on a three-season cycle", "Mulch to limit soil splash"],
        "treatments": [
            {
                "name": "Chlorothalonil",
                "approach": "chemical",
                "description": "Protectant fungicide effective against early blight.",
                "timing": "Begin at first symptom, repeat every 10 days",
                "precautions": "Follow label rates and pre-harvest intervals.",
            },
        ],
    },
    {
        "key": "nutrient_deficiency",
        "label": "Nutrient deficiency",
        "severity": Severity.mild,
        "symptoms": [
            "Interveinal chlorosis",
            "Uniform pattern across the canopy",
            "No lesions or spore structures",
        ],
        "actions": [
            "Take a leaf tissue test to confirm the limiting nutrient",
            "Apply a corrective foliar feed",
        ],
        "prevention": ["Soil test before each season", "Match fertiliser to crop removal rates"],
        "treatments": [
            {
                "name": "Foliar nitrogen",
                "approach": "cultural",
                "description": "Rapid correction of visible nitrogen shortfall.",
                "timing": "Apply early morning to limit scorch",
                "precautions": "Do not apply in full sun or above 28 °C.",
            },
        ],
    },
    {
        "key": "leaf_spot",
        "label": "Fungal leaf spot",
        "severity": Severity.moderate,
        "symptoms": [
            "Discrete brown spots with defined margins",
            "Scattered distribution across leaves",
        ],
        "actions": ["Scout to establish the infection rate", "Remove heavily infected material"],
        "prevention": [
            "Avoid working the crop while foliage is wet",
            "Maintain rotation away from susceptible hosts",
        ],
        "treatments": [
            {
                "name": "Copper-based fungicide",
                "approach": "organic",
                "description": "Protectant spray suppressing further spread.",
                "timing": "Apply on a 7-10 day interval while conditions persist",
                "precautions": "Observe the pre-harvest interval.",
            },
        ],
    },
]


class ImageNotFoundError(NotFoundError):
    """The crop image does not exist."""

    code = ErrorCode.IMAGE_NOT_FOUND


def _validate(content_type: str | None, data: bytes, filename: str | None) -> None:
    if content_type not in settings.ALLOWED_IMAGE_TYPES:
        raise UnsupportedMediaTypeError(
            f"{content_type or 'unknown'} is not a supported image type.",
            details={
                "content_type": content_type,
                "allowed": settings.ALLOWED_IMAGE_TYPES,
                "filename": filename,
            },
        )

    if len(data) > settings.max_upload_bytes:
        raise ImageTooLargeError(
            f"Image is {len(data) / 1_048_576:.1f} MB; the limit is {settings.MAX_UPLOAD_MB} MB.",
            details={"size_bytes": len(data), "max_bytes": settings.max_upload_bytes},
        )

    if not data:
        raise UnsupportedMediaTypeError(
            "The uploaded file is empty.", details={"filename": filename}
        )

    prefixes = _MAGIC.get(content_type, ())
    if prefixes and not any(data.startswith(prefix) for prefix in prefixes):
        raise UnsupportedMediaTypeError(
            f"File content does not match the declared type {content_type}.",
            details={"content_type": content_type, "filename": filename},
        )


def _dimensions(data: bytes) -> tuple[int | None, int | None]:
    """Best-effort dimensions. A file that Pillow cannot open still uploads —
    validation already accepted it, and dimensions are optional in the contract."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            return img.width, img.height
    except Exception:  # noqa: BLE001 - dimensions are advisory only
        return None, None


def upload_image(
    farm_id: UUID,
    *,
    data: bytes,
    content_type: str | None,
    filename: str | None,
    farm_crop_id: UUID | None,
    note: str | None,
    analyze: bool,
) -> CropImage:
    require_farm(farm_id)
    _validate(content_type, data, filename)

    if farm_crop_id is not None:
        planting = store.farm_crops.get(farm_crop_id)
        if planting is None or planting.farm_id != farm_id:
            from app.services.farm_service import CropNotFoundError

            raise CropNotFoundError(
                f"Planting {farm_crop_id} does not exist on farm {farm_id}.",
                details={"farm_id": str(farm_id), "farm_crop_id": str(farm_crop_id)},
            )

    width, height = _dimensions(data)
    digest = hashlib.sha256(data).hexdigest()

    image = CropImage(
        id=uuid4(),
        farm_id=farm_id,
        farm_crop_id=farm_crop_id,
        # No object storage in Phase 3, so there is no honest URL to hand back.
        url=None,
        content_type=content_type or "application/octet-stream",
        size_bytes=len(data),
        width=width,
        height=height,
        note=note,
        analysis_status=ImageAnalysisStatus.pending,
        analysis=None,
        analysis_error=None,
        model=None,
        prompt_version=None,
        ai_mode=None,
        uploaded_at=datetime.now(UTC),
        analyzed_at=None,
    )

    with store.lock:
        store.crop_images[image.id] = image

    # The digest seeds the diagnosis, so the same image always yields the same result.
    _DIGESTS[image.id] = digest

    if analyze:
        return analyze_image(image.id)
    return image


# Content digests, kept beside the store so diagnosis is a function of the bytes.
_DIGESTS: dict[UUID, str] = {}


def _simulate_diagnosis(image: CropImage) -> CropImageAnalysis:
    """Deterministic stand-in for the vision model.

    Seeded by the image's content digest, so the same photograph always produces the
    same diagnosis while different photographs differ.
    """
    digest = _DIGESTS.get(image.id, str(image.id))
    rng = seeded_rng("vision", digest)

    crop_name = None
    if image.farm_crop_id is not None:
        planting = store.farm_crops.get(image.farm_crop_id)
        if planting is not None:
            crop = get_crop(planting.crop_id)
            crop_name = crop.code if crop else None

    condition = _CONDITIONS[rng.randrange(len(_CONDITIONS))]
    is_healthy = condition["key"] == "healthy"

    alternatives = [c for c in _CONDITIONS if c["key"] != condition["key"]]
    rng.shuffle(alternatives)
    primary_likelihood = round(rng.uniform(0.55, 0.88), 2)

    differentials = [
        DifferentialItem(
            condition=alt["key"],
            condition_label=alt["label"],
            likelihood=round(primary_likelihood * factor, 2),
            distinguishing_features=(
                f"{alt['label']} would additionally show: {alt['symptoms'][0].lower()}."
            ),
        )
        for alt, factor in zip(alternatives[:2], (0.45, 0.25), strict=False)
    ]

    return CropImageAnalysis(
        is_plant_material=True,
        crop_identified=crop_name,
        condition=condition["key"],
        condition_label=condition["label"],
        severity=condition["severity"],
        confidence=primary_likelihood,
        affected_area_pct=None if is_healthy else round(rng.uniform(3, 40), 1),
        symptoms_observed=condition["symptoms"],
        differential_diagnoses=differentials,
        immediate_actions=condition["actions"],
        treatment_options=[TreatmentOption(**t) for t in condition["treatments"]],
        prevention=condition["prevention"],
        disclaimer=DISCLAIMER,
    )


def analyze_image(image_id: UUID) -> CropImage:
    image = get_image(image_id)
    analysis = _simulate_diagnosis(image)

    updated = image.model_copy(
        update={
            "analysis_status": ImageAnalysisStatus.complete,
            "analysis": analysis,
            "analysis_error": None,
            "model": None,
            "prompt_version": PROMPT_VERSION,
            "ai_mode": AIMode.mock,
            "analyzed_at": datetime.now(UTC),
        }
    )

    with store.lock:
        store.crop_images[image_id] = updated
    return updated


def get_image(image_id: UUID) -> CropImage:
    image = store.crop_images.get(image_id)
    if image is None:
        raise ImageNotFoundError(
            f"Crop image {image_id} does not exist.", details={"image_id": str(image_id)}
        )
    return image


def list_images(farm_id: UUID, *, page: int, page_size: int) -> CropImageList:
    require_farm(farm_id)
    return paginate(CropImageList, store.images_for_farm(farm_id), page, page_size)
