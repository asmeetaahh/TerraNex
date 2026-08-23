"""Crop-image upload and diagnosis.

**Which engine answers depends on `AI_PROVIDER`, and the payload always says which.**
With the default `mock`, `_simulate_diagnosis` produces a deterministic result seeded
from the file's digest and every payload records `ai_mode="mock"` — no model is called
and nothing is billed. With `gemini`, a vision model examines the stored photograph and
the result records `ai_mode="gemini"`. When a model was configured but could not answer,
the simulator's result is served as `ai_mode="fallback"` with the reason in
`analysis_error`, so a degraded answer is never mistaken for a real one.

That distinction is the whole point of `AIMode`: the simulator has never looked at an
image, and a payload must never let a seeded guess pass as an observation.

The photograph is stored downscaled so the diagnosis request — a separate HTTP call from
the upload — still has something to examine. The bytes are never served to a client;
there is no object storage, so `url` stays `null` rather than pointing at something that
does not exist.

**Storage is dual-path.** With `DATABASE_URL` set, images and their diagnoses live in
`crop_images` and survive a restart; with it unset the in-memory store remains the
implementation, so the suite needs no database and a fresh clone boots with nothing
provisioned. Both paths answer identically — the only difference is how long the answer
lasts.
"""

import hashlib
import io
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.deps import CurrentUser
from app.core.errors import (
    ErrorCode,
    ImageTooLargeError,
    NotFoundError,
    UnsupportedMediaTypeError,
)
from app.db import image_repo
from app.db.memory import ImageMetadata, store
from app.db.session import database_enabled
from app.schemas.enums import AIMode, ImageAnalysisStatus, Severity
from app.schemas.image import (
    CropImage,
    CropImageAnalysis,
    CropImageList,
    DifferentialItem,
    TreatmentOption,
)
from app.services.farm_service import (
    _require_planting,
    find_planting,
    require_farm,
)
from app.services.reference_service import get_crop, paginate
from app.services.simulation import seeded_rng

PROMPT_VERSION = "phase3-vision-fixture-v1"
"""Stamped on the deterministic simulator's output.

Unchanged: it identifies the fixture that produced the result, and the fixture has not
changed. A run stamped with this was not produced by a model.
"""

VISION_PROMPT_VERSION = "phase5-vision-v1"
"""Stamped on a diagnosis a vision model produced.

Separate from `PROMPT_VERSION` because the two are different questions asked of different
engines, and `prompt_version` exists so a stored diagnosis can be traced to the exact
prompt behind it. Stamping the fixture's version on a Gemini result made every vision
answer look like it came from `_simulate_diagnosis` — which is precisely the confusion
`ai_mode` and `prompt_version` are there to prevent.

Bump this whenever `app.ai.vision.SYSTEM_INSTRUCTION` or `build_prompt` changes in a way
that could move a diagnosis, so stored results stay attributable to the wording that
produced them.
"""

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


MAX_STORED_EDGE_PX = 1568
"""Longest edge kept in storage.

The resolution ceiling a vision model works to, so reducing to it costs no diagnostic
detail — a lesion legible at 1568 px is legible to the model, and pixels beyond that are
downsampled before it ever sees them. It is the difference between a row of a few
hundred kilobytes and one of ten megabytes.
"""


def _downscale(data: bytes) -> bytes:
    """The photograph as it will be stored: long edge capped, re-encoded as JPEG.

    Returns the original bytes unchanged when Pillow cannot open the file, when the
    image is already small enough, or when re-encoding would *grow* it. Storing
    something is what matters here; storing it optimally does not, and an upload that
    validation accepted must never fail because a convenience step could not run.

    The caller keeps reporting the original `size_bytes`, `width` and `height` — those
    describe what the user sent.
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            if max(img.width, img.height) <= MAX_STORED_EDGE_PX:
                return data

            img.thumbnail((MAX_STORED_EDGE_PX, MAX_STORED_EDGE_PX))
            # JPEG cannot hold an alpha channel or a palette; converting first means a
            # PNG screenshot with transparency re-encodes instead of raising.
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85, optimize=True)
            reduced = buffer.getvalue()
    except Exception:  # noqa: BLE001 - storage is best-effort, validation already passed
        return data

    return reduced if len(reduced) < len(data) else data


async def upload_image(
    farm_id: UUID,
    *,
    data: bytes,
    content_type: str | None,
    filename: str | None,
    farm_crop_id: UUID | None,
    note: str | None,
    analyze: bool,
    user: CurrentUser,
) -> CropImage:
    require_farm(farm_id, user)
    _validate(content_type, data, filename)

    if farm_crop_id is not None:
        # `_require_planting` dispatches to whichever backend holds plantings and
        # raises the same CropNotFoundError this used to build by hand, so the error
        # envelope is unchanged. Reading the store directly meant attaching an image
        # to a planting 404'd as soon as plantings lived in the database.
        _require_planting(farm_id, farm_crop_id)

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

    # The digest seeds the diagnosis, so the same image always yields the same result —
    # which is only true for as long as the digest outlives the image. The pixels are
    # kept for the same reason: diagnosis is a separate request, and until now the
    # photograph no longer existed by the time it arrived.
    stored_bytes = _downscale(data)
    _store_image(image, digest, stored_bytes, user)

    if analyze:
        # The bytes are still in hand, so the diagnosis reads them directly rather than
        # loading back what was written a line ago.
        return await analyze_image(image.id, user, pixels=stored_bytes)
    return image


def _persisted() -> bool:
    """Whether crop images live in the database on this deployment."""
    return database_enabled()


def _store_image(image: CropImage, digest: str, pixels: bytes | None, user: CurrentUser) -> None:
    """Write a newly uploaded image, with its digest and pixels, to configured storage."""
    if _persisted():
        image_repo.insert_image(image, sha256=digest, image_bytes=pixels, user_id=user.id)
        return
    store.record_image(image, ImageMetadata(sha256=digest, image_bytes=pixels))


def _update_image(image: CropImage) -> None:
    """Write a diagnosis onto an already-stored image.

    Neither the digest nor the pixels are passed, and both storage paths update only the
    diagnosis fields — so a re-diagnosis cannot discard the photograph it was derived
    from.
    """
    if _persisted():
        image_repo.update_analysis(image)
        return
    store.update_image(image)


def _pixels_for(image_id: UUID) -> bytes | None:
    """The stored photograph, from whichever storage holds it.

    None for an image uploaded before pixels were kept, or one whose bytes could not be
    stored. A caller must treat that as "cannot look at the picture", not as an error.
    """
    if _persisted():
        return image_repo.get_bytes(image_id)
    return store.image_bytes(image_id)


def _digest_for(image_id: UUID) -> str | None:
    """The stored content digest for an image, from whichever storage holds it."""
    if _persisted():
        return image_repo.get_digest(image_id)
    return store.image_digest(image_id)


def _simulate_diagnosis(image: CropImage, digest: str | None) -> CropImageAnalysis:
    """Deterministic stand-in for the vision model.

    Seeded by the image's content digest, so the same photograph always produces the
    same diagnosis while different photographs differ. The digest is passed in rather
    than looked up here, because *where* it is stored is the caller's concern and a
    module-global cache of them is what previously broke this guarantee across a
    restart.

    Falling back to the image id keeps a digest-less row diagnosable rather than
    erroring, but it is a different seed and therefore a different diagnosis — which is
    precisely why the digest is now stored rather than held in memory.
    """
    rng = seeded_rng("vision", digest or str(image.id))

    crop_name = None
    if image.farm_crop_id is not None:
        planting = find_planting(image.farm_id, image.farm_crop_id)
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


async def analyze_image(
    image_id: UUID, user: CurrentUser, *, pixels: bytes | None = None
) -> CropImage:
    """Diagnose one uploaded image.

    **A completed diagnosis is returned as-is rather than recomputed.** The photograph is
    immutable, so a second analysis of the same bytes is the same question — and once a
    model is doing the work it is also a second bill. The contract has no `force`
    parameter, which makes reuse the only behaviour a caller can ask for; re-uploading
    the image is how you get a fresh look at it.

    `pixels` lets the upload path hand over bytes it already holds instead of loading
    back what it wrote a moment earlier. Everything else resolves them from storage.
    """
    # `get_image` resolves ownership through the image's farm, so analysing someone
    # else's image is refused before any work is done.
    image = get_image(image_id, user)

    if image.analysis is not None and image.analysis_status is ImageAnalysisStatus.complete:
        return image

    if pixels is None:
        pixels = _pixels_for(image_id)

    analysis, ai_mode, model_name, error, prompt_version = await _diagnose(image, pixels)

    updated = image.model_copy(
        update={
            "analysis_status": ImageAnalysisStatus.complete,
            "analysis": analysis,
            "analysis_error": error,
            "model": model_name,
            "prompt_version": prompt_version,
            "ai_mode": ai_mode,
            "analyzed_at": datetime.now(UTC),
        }
    )

    _update_image(updated)
    return updated


async def _diagnose(
    image: CropImage, pixels: bytes | None
) -> tuple[CropImageAnalysis, AIMode, str | None, str | None, str]:
    """Produce one diagnosis, and say honestly what produced it.

    Returns the analysis alongside the three fields that make it traceable: `ai_mode`,
    the model name, and the `prompt_version` of the wording that actually produced it.
    A fallback carries the *simulator's* version, because the simulator is what answered.

    Three outcomes, and `ai_mode` distinguishes them because the contract exists to let a
    caller tell real model output from a canned answer:

    * **mock** — `AI_PROVIDER=mock`, the default. The deterministic simulator, unchanged.
      No model is called, nothing is billed, and the result is reproducible.
    * **gemini** — a vision model examined the photograph. Includes a confident *"this is
      not plant material"*, which is a real finding rather than a failure.
    * **fallback** — a model was configured but could not answer: no key, no stored
      pixels, a timeout, an API error, or output that failed validation twice. The
      simulator's answer is served with the reason recorded in `analysis_error`, so the
      degradation is visible rather than silently indistinguishable from success.

    An image uploaded before pixels were stored lands in the third case. Nothing is
    fabricated to stand in for the photograph — there is simply no image to examine, and
    the mode says so.
    """
    simulated = _simulate_diagnosis(image, _digest_for(image.id))

    if settings.AI_PROVIDER != "gemini":
        return simulated, AIMode.mock, None, None, PROMPT_VERSION

    if not pixels:
        # Not a provider failure — there is nothing to look at. Still `fallback`, because
        # a model was configured and did not produce this answer.
        return (
            simulated,
            AIMode.fallback,
            None,
            "No stored image data; this image predates image retention and cannot be "
            "examined by a model.",
            PROMPT_VERSION,
        )

    from app.ai import vision

    crop_name = None
    if image.farm_crop_id is not None:
        planting = find_planting(image.farm_id, image.farm_crop_id)
        if planting is not None:
            crop = get_crop(planting.crop_id)
            crop_name = crop.name if crop else None

    result = await vision.diagnose_image(
        pixels,
        content_type=image.content_type,
        context=vision.ImageContext(crop_name=crop_name, note=image.note),
        disclaimer=DISCLAIMER,
    )

    if result.ok and result.data is not None:
        return result.data, AIMode.gemini, settings.GEMINI_MODEL, None, VISION_PROMPT_VERSION

    # The simulator answered, so it is the simulator's prompt version that describes this
    # result — stamping the vision one would attribute a fixture to a model.
    return simulated, AIMode.fallback, None, result.meta.note, PROMPT_VERSION


def get_image(image_id: UUID, user: CurrentUser) -> CropImage:
    """A stored image, scoped to its farm's owner.

    The image is addressed by its own id, so ownership is resolved through the farm it
    belongs to. Someone else's image is `FARM_NOT_FOUND`, deliberately
    indistinguishable from an image that does not exist.
    """
    image = image_repo.get_image(image_id) if _persisted() else store.crop_images.get(image_id)
    if image is None:
        raise ImageNotFoundError(
            f"Crop image {image_id} does not exist.", details={"image_id": str(image_id)}
        )
    require_farm(UUID(str(image.farm_id)), user)
    return image


def images_for_farm(farm_id: UUID) -> list[CropImage]:
    """Every image for a farm, newest first, from whichever storage is configured.

    Ownership is the caller's to enforce — `list_images` and the dashboard both resolve
    the farm first.
    """
    if _persisted():
        return image_repo.images_for_farm(farm_id)
    return store.images_for_farm(farm_id)


def list_images(farm_id: UUID, *, page: int, page_size: int, user: CurrentUser) -> CropImageList:
    require_farm(farm_id, user)
    return paginate(CropImageList, images_for_farm(farm_id), page, page_size)
