"""Gemini vision, at the module boundary.

The SDK is never exercised: `gemini._get_client` is monkeypatched with a fake, the same
way `test_gemini.py` does it, because the boundary worth testing is "does this module
honour its own contract", not "does Google's client work". No test here needs a key or
a network, and none may acquire one.

The load-bearing test is `test_a_non_plant_image_is_never_given_a_diagnosis`. The
simulator this module replaces hardcoded `is_plant_material=True` and would diagnose a
photograph of a keyboard as severe late blight; the field exists precisely to stop that,
and the frontend already branches on it.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from google.genai import errors as genai_errors

from app.ai import gemini, vision
from app.providers.base import ProviderResult
from app.schemas.common import DataMode

FAKE_KEY = "test-key-not-real"
DISCLAIMER = "AI-assisted diagnosis for guidance only."
PIXELS = b"\xff\xd8\xff\xe0not-really-a-jpeg\xff\xd9"


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(vision.settings, "GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(vision.settings, "GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(vision.settings, "AI_TIMEOUT_S", 5.0)
    monkeypatch.setattr(vision.settings, "AI_TEMPERATURE", 0.3)
    gemini.reset_client()
    yield
    gemini.reset_client()


def plant_payload(**overrides) -> str:
    body = {
        "is_plant_material": True,
        "crop_identified": "potato",
        "condition": "late_blight",
        "condition_label": "Late blight",
        "severity": "severe",
        "confidence": 0.82,
        "affected_area_pct": 24.0,
        "symptoms_observed": ["Dark water-soaked lesions"],
        "differential_diagnoses": [
            {
                "condition": "early_blight",
                "condition_label": "Early blight",
                "likelihood": 0.3,
                "distinguishing_features": "Concentric rings.",
            }
        ],
        "immediate_actions": ["Remove affected foliage"],
        "treatment_options": [
            {
                "name": "Copper fungicide",
                "approach": "chemical",
                "description": "Apply at first sign.",
            }
        ],
        "prevention": ["Rotate crops"],
        "disclaimer": "model's own wording",
    }
    body.update(overrides)
    return json.dumps(body)


NON_PLANT_PAYLOAD = json.dumps(
    {
        "is_plant_material": False,
        "crop_identified": None,
        "condition": "not_plant_material",
        "condition_label": "Not plant material",
        "severity": "none",
        "confidence": 0.95,
        "affected_area_pct": None,
        "symptoms_observed": [],
        "differential_diagnoses": [],
        "immediate_actions": [],
        "treatment_options": [],
        "prevention": [],
        "disclaimer": "model's own wording",
    }
)


class _FakeResponse:
    def __init__(self, text: str | None) -> None:
        self.text = text


class _FakeModels:
    """Returns each queued reply in turn, so a repair retry can be observed."""

    def __init__(self, *, results=None, exc: Exception | None = None, delay: float = 0.0):
        self._results = list(results or [])
        self._exc = exc
        self._delay = delay
        self.calls: list[dict] = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc is not None:
            raise self._exc
        return self._results.pop(0) if self._results else _FakeResponse(None)


class _FakeAio:
    def __init__(self, models: _FakeModels) -> None:
        self.models = models


class _FakeClient:
    def __init__(self, models: _FakeModels) -> None:
        self.aio = _FakeAio(models)


def _install(monkeypatch, models: _FakeModels) -> None:
    """Patch where `vision` looked the symbol up, not where it was defined."""
    monkeypatch.setattr(vision, "_get_client", lambda: _FakeClient(models))


async def diagnose(context: vision.ImageContext | None = None, pixels: bytes = PIXELS):
    return await vision.diagnose_image(
        pixels,
        content_type="image/jpeg",
        context=context or vision.ImageContext(),
        disclaimer=DISCLAIMER,
    )


# --------------------------------------------------------------------------
# The request
# --------------------------------------------------------------------------


async def test_the_image_and_schema_are_both_sent(monkeypatch) -> None:
    models = _FakeModels(results=[_FakeResponse(plant_payload())])
    _install(monkeypatch, models)

    await diagnose()

    call = models.calls[0]
    part = call["contents"][0]
    assert part.inline_data.data == PIXELS, "the photograph itself must be sent"
    assert part.inline_data.mime_type == "image/jpeg"
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_schema is not None, "structured output, not prose"


async def test_an_unknown_mime_type_falls_back_to_jpeg(monkeypatch) -> None:
    """Upload validated magic bytes and `_downscale` re-encodes, so a stored image is a
    real image whatever its header claims. Refusing to look over a header would be worse
    than sending a plausible type."""
    models = _FakeModels(results=[_FakeResponse(plant_payload())])
    _install(monkeypatch, models)

    await vision.diagnose_image(
        PIXELS,
        content_type="application/octet-stream",
        context=vision.ImageContext(),
        disclaimer=DISCLAIMER,
    )

    assert models.calls[0]["contents"][0].inline_data.mime_type == "image/jpeg"


# --------------------------------------------------------------------------
# Success
# --------------------------------------------------------------------------


async def test_a_valid_reply_is_parsed_and_returned(monkeypatch) -> None:
    _install(monkeypatch, _FakeModels(results=[_FakeResponse(plant_payload())]))

    result = await diagnose()

    assert result.ok
    assert result.meta.mode is DataMode.live
    assert result.data.condition == "late_blight"
    assert result.data.severity == "severe"
    assert result.data.is_plant_material is True


async def test_the_service_disclaimer_replaces_the_model_wording(monkeypatch) -> None:
    """The disclaimer is a legal and honesty statement. A model that paraphrased or
    dropped it would silently weaken it, so the service's own text always wins."""
    _install(monkeypatch, _FakeModels(results=[_FakeResponse(plant_payload())]))

    result = await diagnose()

    assert result.data.disclaimer == DISCLAIMER


# --------------------------------------------------------------------------
# The plant guard
# --------------------------------------------------------------------------


async def test_a_non_plant_image_is_never_given_a_diagnosis(monkeypatch) -> None:
    """The reason this module exists. A confident "not a plant" is a **success** — the
    caller must not fall back to the simulator, which would invent a disease."""
    _install(monkeypatch, _FakeModels(results=[_FakeResponse(NON_PLANT_PAYLOAD)]))

    result = await diagnose()

    assert result.ok, "an honest non-plant verdict is a result, not a failure"
    assert result.data.is_plant_material is False
    assert result.data.severity == "none"
    assert result.data.symptoms_observed == []
    assert result.data.treatment_options == []
    assert result.data.affected_area_pct is None
    assert result.data.disclaimer == DISCLAIMER


async def test_a_non_plant_verdict_is_sanitised_even_if_the_model_contradicts_itself(
    monkeypatch,
) -> None:
    """Enforced in code, not only in the prompt. A model that says "not a plant" while
    also filling in a severe disease must not have that reach the payload — the UI
    branches on this field."""
    contradictory = json.dumps(
        {
            **json.loads(plant_payload()),
            "is_plant_material": False,
        }
    )
    _install(monkeypatch, _FakeModels(results=[_FakeResponse(contradictory)]))

    result = await diagnose()

    assert result.data.is_plant_material is False
    assert result.data.crop_identified is None
    assert result.data.severity == "none"
    assert result.data.symptoms_observed == []
    assert result.data.differential_diagnoses == []
    assert result.data.treatment_options == []
    assert result.data.immediate_actions == []
    assert result.data.prevention == []


async def test_the_crop_hint_is_labelled_as_unverified(monkeypatch) -> None:
    """The hint describes the farmer's records, not the photograph. The prompt must say
    so, or the model agrees with the paperwork instead of the picture."""
    models = _FakeModels(results=[_FakeResponse(plant_payload())])
    _install(monkeypatch, models)

    await diagnose(vision.ImageContext(crop_name="Potato"))

    prompt = models.calls[0]["contents"][1]
    assert "Potato" in prompt
    assert "unverified hint" in prompt
    assert "report what you actually see" in prompt


def test_the_system_instruction_forbids_the_hint_forcing_plant_material() -> None:
    text = vision.SYSTEM_INSTRUCTION
    assert "NO MATTER WHAT the hint says" in text
    assert "never make an image plant material" in text
    assert "Do NOT invent a disease" in text


# --------------------------------------------------------------------------
# Malformed output → one repair retry
# --------------------------------------------------------------------------


async def test_invalid_output_triggers_exactly_one_repair_retry(monkeypatch) -> None:
    """`docs/ARCHITECTURE.md` §7 specifies one repair retry with the errors fed back."""
    models = _FakeModels(
        results=[_FakeResponse('{"is_plant_material": true}'), _FakeResponse(plant_payload())]
    )
    _install(monkeypatch, models)

    result = await diagnose()

    assert len(models.calls) == 2, "exactly one retry"
    assert result.ok
    assert result.data.condition == "late_blight"


async def test_the_repair_prompt_carries_the_validation_errors(monkeypatch) -> None:
    models = _FakeModels(
        results=[_FakeResponse('{"is_plant_material": true}'), _FakeResponse(plant_payload())]
    )
    _install(monkeypatch, models)

    await diagnose()

    repair = models.calls[1]["contents"][1]
    assert "did not satisfy the required structure" in repair
    assert "condition" in repair, "the failing field names must be fed back"
    assert "Do not change your findings" in repair


async def test_output_invalid_twice_degrades_rather_than_looping(monkeypatch) -> None:
    models = _FakeModels(results=[_FakeResponse("{}"), _FakeResponse("{}")])
    _install(monkeypatch, models)

    result = await diagnose()

    assert len(models.calls) == 2, "one retry, not a loop"
    assert result.ok is False
    assert "validation" in (result.meta.note or "")


async def test_non_json_prose_is_not_scraped(monkeypatch) -> None:
    """Fragile string manipulation is exactly what structured output replaces."""
    models = _FakeModels(
        results=[
            _FakeResponse("I think this might be late blight, roughly 80% sure."),
            _FakeResponse("Still prose, sorry."),
        ]
    )
    _install(monkeypatch, models)

    result = await diagnose()

    assert result.ok is False


# --------------------------------------------------------------------------
# Provider failure
# --------------------------------------------------------------------------


async def test_a_missing_key_is_unavailable_not_an_exception(monkeypatch) -> None:
    monkeypatch.setattr(vision.settings, "GEMINI_API_KEY", None)

    result = await diagnose()

    assert result.ok is False
    assert result.data is None
    assert "GEMINI_API_KEY" in (result.meta.note or "")


async def test_absent_pixels_are_reported_distinctly(monkeypatch) -> None:
    """An image with no stored bytes is not a provider failure — there is nothing to
    look at — but it is still not a diagnosis."""
    result = await diagnose(pixels=b"")

    assert result.ok is False
    assert "No stored image data" in (result.meta.note or "")


async def test_a_timeout_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(vision.settings, "AI_TIMEOUT_S", 0.01)
    _install(monkeypatch, _FakeModels(results=[_FakeResponse(plant_payload())], delay=0.5))

    result = await diagnose()

    assert result.ok is False
    assert "did not respond" in (result.meta.note or "")


async def test_an_api_error_is_unavailable(monkeypatch) -> None:
    exc = genai_errors.APIError(429, {"message": "quota exceeded"})
    _install(monkeypatch, _FakeModels(exc=exc))

    result = await diagnose()

    assert result.ok is False
    assert "Gemini API error" in (result.meta.note or "")


async def test_a_transport_error_is_unavailable(monkeypatch) -> None:
    _install(monkeypatch, _FakeModels(exc=ConnectionResetError("connection reset by peer")))

    result = await diagnose()

    assert result.ok is False
    assert "request failed" in (result.meta.note or "")


async def test_an_empty_or_blocked_response_is_unavailable(monkeypatch) -> None:
    _install(monkeypatch, _FakeModels(results=[_FakeResponse(None)]))

    result = await diagnose()

    assert result.ok is False
    assert "no usable output" in (result.meta.note or "")


async def test_nothing_raises_whatever_the_provider_does(monkeypatch) -> None:
    """The caller degrades on a `ProviderResult`, never on an exception."""
    for exc in (
        RuntimeError("boom"),
        ConnectionResetError("reset"),
        genai_errors.APIError(500, {"message": "server error"}),
    ):
        _install(monkeypatch, _FakeModels(exc=exc))
        result = await diagnose()
        assert isinstance(result, ProviderResult)
        assert result.ok is False


# --------------------------------------------------------------------------
# Secret hygiene
# --------------------------------------------------------------------------


async def test_the_api_key_never_reaches_a_note(monkeypatch) -> None:
    _install(monkeypatch, _FakeModels(exc=RuntimeError(f"auth failed for key {FAKE_KEY}")))

    result = await diagnose()

    assert FAKE_KEY not in (result.meta.note or "")
    assert "***" in (result.meta.note or "")


async def test_image_bytes_never_reach_a_log(monkeypatch, caplog) -> None:
    """A photograph of someone's field is not log material."""
    import logging

    secret_pixels = b"\xff\xd8\xffSECRETFIELDPHOTO\xff\xd9"
    _install(monkeypatch, _FakeModels(exc=RuntimeError("failed")))

    with caplog.at_level(logging.WARNING):
        await vision.diagnose_image(
            secret_pixels,
            content_type="image/jpeg",
            context=vision.ImageContext(),
            disclaimer=DISCLAIMER,
        )

    combined = "".join(record.getMessage() + str(record.__dict__) for record in caplog.records)
    assert "SECRETFIELDPHOTO" not in combined
    assert str(len(secret_pixels)) in combined, "the size is fine to log; the bytes are not"
