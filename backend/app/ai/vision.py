"""Gemini vision: a diagnosis derived from the photograph itself.

The simulator this replaces never looked at an image. It seeded a random choice from the
SHA-256 of the file, so a photograph of a keyboard came back as *late blight, severe,
confidence 0.73* — with `is_plant_material` hardcoded `True`, because nothing was
examining anything. The contract designed that field as the guard against exactly this
and the frontend already branches on it; this module is what finally lets it be false.

**Structured output, not prose parsing.** The response schema *is*
:class:`~app.schemas.image.CropImageAnalysis`, handed to the SDK as
`response_schema`, so the model fills a form rather than writing text somebody has to
scrape. The reply is then re-validated with `model_validate_json` before it leaves this
module: `response_schema` constrains generation, it does not guarantee the result, and a
payload that no longer satisfies the schema must fail here rather than reach a response
half-formed.

**One repair retry**, as `docs/ARCHITECTURE.md` §7 has always specified — the validation
errors are fed back and the model is asked once more. Narrative never needed this because
it returns a plain string; a structure does.

Everything else is `app/ai/gemini.py`'s: the same client, the same key check, the same
redaction, the same wall-clock budget, the same four-branch error ladder. This module
adds vision, not a second Gemini integration.

**Never raises.** Every failure — missing key, timeout, API error, transport surprise,
blocked or malformed reply — becomes an `unavailable` `ProviderResult`, exactly like
every provider in `app/providers/`. The caller degrades to `AIMode.fallback` without
wrapping this in a try/except of its own.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import ValidationError

from app.ai.gemini import GEMINI_SOURCE, _get_client, _redact
from app.core.config import settings
from app.core.logging import get_logger
from app.providers.base import ProviderResult
from app.providers.http import provider_deadline, remaining_budget
from app.schemas.image import CropImageAnalysis

logger = get_logger(__name__)

#: MIME types the model is offered. Matches `settings.ALLOWED_IMAGE_TYPES`, which upload
#: validation already enforced, so anything reaching here is one of these.
SUPPORTED_MIME = ("image/jpeg", "image/png", "image/webp")

DEFAULT_MIME = "image/jpeg"
"""What an unrecognised content type is sent as.

Upload validation checks magic bytes, and `_downscale` re-encodes to JPEG, so a stored
image is a real image whatever its declared type claims. Sending a plausible type beats
refusing to look at a photograph over a header.
"""


@dataclass(frozen=True, slots=True)
class ImageContext:
    """What the service knows about the photograph, other than its pixels.

    Deliberately tiny. The model is being asked to read an image, not to be told what it
    will find — the more context it receives, the more it can agree with the context
    instead of with the picture.
    """

    #: The crop the farmer attached this image to, if any. A hint, never an instruction:
    #: see `SYSTEM_INSTRUCTION`.
    crop_name: str | None = None

    #: The farmer's own note on the upload.
    note: str | None = None


SYSTEM_INSTRUCTION = """You are TerraNex's crop diagnosis assistant. You examine ONE \
photograph and report what is actually visible in it.

Rules you must follow exactly:

1. LOOK AT THE IMAGE FIRST. Every field you emit describes what you can see in this \
photograph. Never describe what a crop of this type usually suffers from.

2. NON-PLANT IMAGES. If the image does not show plant or crop material — a person, an \
animal, a document, a screenshot, a machine, a landscape with no identifiable plant \
subject, a blank or unreadable frame — then set is_plant_material to false and:
   - set condition to "not_plant_material" and condition_label to "Not plant material"
   - set severity to "none" and affected_area_pct to null
   - leave symptoms_observed, differential_diagnoses, immediate_actions, \
treatment_options and prevention as empty lists
   - set crop_identified to null
   Do NOT invent a disease, a severity, a symptom or a treatment for something that is \
not a plant. Reporting honestly that you cannot diagnose the image is the correct and \
expected answer, not a failure.

3. THE CROP HINT IS NOT EVIDENCE. You may be told which crop the farmer believes this \
image shows. That is a hint about their field records, not about this photograph. If the \
image shows a different plant, say what you see and set crop_identified accordingly. If \
the image shows no plant at all, is_plant_material is false NO MATTER WHAT the hint says. \
The hint can never make an image plant material.

4. CONFIDENCE AND ALTERNATIVES. confidence is your genuine certainty in the primary \
finding, between 0 and 1. When plant material is present and you are not certain, list \
plausible alternatives in differential_diagnoses, most likely first, each with a \
likelihood below your primary confidence. A single overconfident answer is worse than an \
honest ranked list.

5. HEALTHY IS A DIAGNOSIS. If the plant looks healthy, set condition to "healthy", \
severity to "none", and leave treatment_options empty.

6. condition is a lowercase machine key with underscores, for example "late_blight" or \
"nutrient_deficiency". condition_label is its human-readable form.

7. Advice must be practical for a smallholder farmer and must not assume any particular \
country, currency, product brand or regulatory regime.

Return only the structured object requested. Do not add commentary around it."""


def build_prompt(context: ImageContext) -> str:
    """The user-turn text accompanying the image.

    Kept short and free of leading detail. The system instruction carries the rules; this
    carries only what the farmer supplied, clearly labelled as unverified so the model
    weighs the picture more heavily than the paperwork.
    """
    lines = ["Diagnose the attached crop photograph."]

    if context.crop_name:
        lines.append(
            f"\nThe farmer has this image filed under the crop: {context.crop_name}. "
            "Treat that as an unverified hint about their records, not as evidence about "
            "the photograph. If the image shows something else — or no plant at all — "
            "report what you actually see."
        )
    if context.note:
        lines.append(f"\nThe farmer's note on the upload: {context.note}")

    lines.append(
        "\nIf this image does not show plant material, set is_plant_material to false and "
        "do not invent a diagnosis."
    )
    return "".join(lines)


def _repair_prompt(context: ImageContext, errors: str) -> str:
    """The second and final attempt, with the validation failure fed back.

    `docs/ARCHITECTURE.md` §7 specifies one repair retry. One, not a loop: a model that
    cannot satisfy the schema twice is not going to on the third try, and the caller has
    a deterministic fallback that costs nothing.
    """
    return (
        f"{build_prompt(context)}\n\n"
        "Your previous reply did not satisfy the required structure. "
        f"The validation errors were:\n{errors}\n"
        "Return a corrected object that satisfies every constraint. Do not change your "
        "findings to make validation easier — if the image is not plant material, say so."
    )


def _mime_for(content_type: str | None) -> str:
    return content_type if content_type in SUPPORTED_MIME else DEFAULT_MIME


def _sanitise(analysis: CropImageAnalysis, disclaimer: str) -> CropImageAnalysis:
    """Enforce the non-plant contract in code, not only in the prompt.

    A model told not to invent a diagnosis for a keyboard will usually comply. "Usually"
    is not the standard for a field the UI branches on, so when `is_plant_material` is
    false every diagnostic field is cleared here regardless of what came back. The guard
    is then a property of the system rather than of the model's mood.

    The disclaimer is always the service's own — it is a legal and honesty statement, and
    a model that paraphrased or omitted it would silently weaken it.
    """
    if analysis.is_plant_material:
        return analysis.model_copy(update={"disclaimer": disclaimer})

    return analysis.model_copy(
        update={
            "crop_identified": None,
            "severity": "none",
            "affected_area_pct": None,
            "symptoms_observed": [],
            "differential_diagnoses": [],
            "immediate_actions": [],
            "treatment_options": [],
            "prevention": [],
            "disclaimer": disclaimer,
        }
    )


async def diagnose_image(
    image_bytes: bytes,
    *,
    content_type: str | None,
    context: ImageContext,
    disclaimer: str,
) -> ProviderResult[CropImageAnalysis]:
    """Ask Gemini what is in this photograph.

    Returns a validated `CropImageAnalysis` on success, or an `unavailable`
    `ProviderResult` the caller can degrade from. A confident "this is not plant
    material" is a **success**, not a failure — the distinction matters, because the
    caller falls back to the simulator on failure and must not do so here.
    """
    if not settings.GEMINI_API_KEY:
        logger.warning("vision_missing_api_key", extra={"source": GEMINI_SOURCE})
        return ProviderResult.unavailable(
            GEMINI_SOURCE, "GEMINI_API_KEY is not configured; image diagnosis skipped."
        )

    if not image_bytes:
        # Distinct from a provider failure: there is nothing to look at, which the caller
        # already knows how to handle. Recorded as unavailable so the mode is explicit.
        return ProviderResult.unavailable(
            GEMINI_SOURCE, "No stored image data; the photograph cannot be examined."
        )

    client = _get_client()
    mime = _mime_for(content_type)
    # Only the size is logged. The bytes are a photograph of someone's field and never
    # reach a log line, and neither does the model's raw reply.
    log_extra = {
        "source": GEMINI_SOURCE,
        "model": settings.GEMINI_MODEL,
        "mime": mime,
        "bytes": len(image_bytes),
    }

    with provider_deadline(settings.AI_TIMEOUT_S):
        prompt = build_prompt(context)

        for attempt in (1, 2):
            budget = remaining_budget()
            timeout = settings.AI_TIMEOUT_S if budget is None else max(0.1, budget)

            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=settings.GEMINI_MODEL,
                        contents=[
                            genai_types.Part.from_bytes(data=image_bytes, mime_type=mime),
                            prompt,
                        ],
                        config=genai_types.GenerateContentConfig(
                            system_instruction=SYSTEM_INSTRUCTION,
                            temperature=settings.AI_TEMPERATURE,
                            response_mime_type="application/json",
                            response_schema=CropImageAnalysis,
                        ),
                    ),
                    timeout=timeout,
                )
            except TimeoutError:
                logger.warning(
                    "vision_timeout", extra={**log_extra, "timeout_s": round(timeout, 2)}
                )
                return ProviderResult.unavailable(
                    GEMINI_SOURCE, f"Gemini did not respond within {timeout:.1f}s."
                )
            except genai_errors.APIError as exc:
                reason = _redact(f"{type(exc).__name__}: {exc.message or exc.status or exc.code}")
                logger.warning("vision_api_error", extra={**log_extra, "reason": reason})
                return ProviderResult.unavailable(GEMINI_SOURCE, f"Gemini API error: {reason}")
            except Exception as exc:  # transport-level surprises: DNS, TLS, reset
                reason = _redact(str(exc))
                logger.warning("vision_transport_error", extra={**log_extra, "reason": reason})
                return ProviderResult.unavailable(GEMINI_SOURCE, f"Gemini request failed: {reason}")

            text = (response.text or "").strip()
            if not text:
                logger.warning("vision_empty_response", extra={**log_extra, "attempt": attempt})
                return ProviderResult.unavailable(
                    GEMINI_SOURCE, "Gemini returned no usable output (empty or blocked response)."
                )

            try:
                analysis = CropImageAnalysis.model_validate_json(text)
            except ValidationError as exc:
                # `response_schema` constrains generation; it does not guarantee the
                # result, so the reply is validated rather than trusted.
                errors = _redact(str(exc))
                logger.warning(
                    "vision_invalid_output",
                    extra={**log_extra, "attempt": attempt, "errors": errors[:500]},
                )
                if attempt == 1:
                    prompt = _repair_prompt(context, errors)
                    continue
                return ProviderResult.unavailable(
                    GEMINI_SOURCE, "Gemini output failed validation after one repair attempt."
                )

            return ProviderResult.live(
                _sanitise(analysis, disclaimer),
                GEMINI_SOURCE,
                "Diagnosis produced by a vision model from the uploaded photograph.",
            )

    # Unreachable: the loop either returns or continues exactly once.
    return ProviderResult.unavailable(GEMINI_SOURCE, "Gemini image diagnosis did not complete.")
