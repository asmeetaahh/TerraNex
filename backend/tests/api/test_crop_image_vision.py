"""Vision through the service, and the honesty of `ai_mode`.

`tests/ai/test_vision.py` covers the provider module. This file covers what the endpoint
does with it — chiefly that a caller can always tell which engine answered:

* `mock`     — the simulator, unchanged, no model called
* `gemini`   — a vision model examined the photograph
* `fallback` — a model was configured and could not answer; the simulator's result is
  served with the reason in `analysis_error`

The simulator has never looked at an image, so a payload that let its guess pass as a
model observation would be the exact dishonesty `AIMode` exists to prevent.

No test here needs a key or a network: `vision.diagnose_image` is patched at the seam
the service imports it through.
"""

from __future__ import annotations

import io

import pytest
from httpx import AsyncClient

from app.ai import vision
from app.providers.base import ProviderResult
from app.schemas.image import CropImageAnalysis
from app.services import image_service
from tests.conftest import JPEG_BYTES


def _file(data: bytes = JPEG_BYTES, name: str = "leaf.jpg", mime: str = "image/jpeg"):
    return {"file": (name, io.BytesIO(data), mime)}


PLANT = CropImageAnalysis(
    is_plant_material=True,
    crop_identified="potato",
    condition="late_blight",
    condition_label="Late blight",
    severity="severe",
    confidence=0.82,
    affected_area_pct=24.0,
    symptoms_observed=["Dark water-soaked lesions"],
    differential_diagnoses=[],
    immediate_actions=["Remove affected foliage"],
    treatment_options=[],
    prevention=["Rotate crops"],
    disclaimer="placeholder",
)

NOT_PLANT = CropImageAnalysis(
    is_plant_material=False,
    crop_identified=None,
    condition="not_plant_material",
    condition_label="Not plant material",
    severity="none",
    confidence=0.95,
    affected_area_pct=None,
    symptoms_observed=[],
    differential_diagnoses=[],
    immediate_actions=[],
    treatment_options=[],
    prevention=[],
    disclaimer="placeholder",
)


@pytest.fixture
def gemini_mode(monkeypatch):
    monkeypatch.setattr(image_service.settings, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(image_service.settings, "GEMINI_MODEL", "gemini-2.5-flash")


def patch_vision(monkeypatch, result: ProviderResult):
    """Patch the module attribute the service resolves at call time."""

    async def fake(*args, **kwargs):
        fake.calls.append(kwargs)
        return result

    fake.calls = []
    monkeypatch.setattr(vision, "diagnose_image", fake)
    return fake


async def upload(client: AsyncClient, api_prefix: str, farm_id: str, analyze: bool = False):
    query = "?analyze=true" if analyze else ""
    resp = await client.post(f"{api_prefix}/farms/{farm_id}/crop-images{query}", files=_file())
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------------------
# The mock path is untouched
# --------------------------------------------------------------------------


async def test_mock_remains_the_default_and_calls_no_model(
    client: AsyncClient, api_prefix: str, farm: dict, monkeypatch
) -> None:
    called = patch_vision(monkeypatch, ProviderResult.unavailable("gemini", "should not run"))

    body = await upload(client, api_prefix, farm["id"], analyze=True)

    assert body["ai_mode"] == "mock"
    assert body["model"] is None
    assert body["prompt_version"] == "phase3-vision-fixture-v1", "the fixture's own version"
    assert body["analysis_error"] is None
    assert called.calls == [], "AI_PROVIDER=mock must never reach a model"


async def test_mock_stays_deterministic(client: AsyncClient, api_prefix: str, farm: dict) -> None:
    first = await upload(client, api_prefix, farm["id"], analyze=True)
    second = await upload(client, api_prefix, farm["id"], analyze=True)

    assert first["analysis"]["condition"] == second["analysis"]["condition"]
    assert first["analysis"]["confidence"] == second["analysis"]["confidence"]


# --------------------------------------------------------------------------
# Gemini success
# --------------------------------------------------------------------------


async def test_a_successful_diagnosis_is_labelled_gemini(
    client: AsyncClient, api_prefix: str, farm: dict, monkeypatch, gemini_mode
) -> None:
    patch_vision(monkeypatch, ProviderResult.live(PLANT, "gemini"))

    body = await upload(client, api_prefix, farm["id"], analyze=True)

    assert body["ai_mode"] == "gemini"
    assert body["model"] == "gemini-2.5-flash"
    assert body["prompt_version"] == "phase5-vision-v1"
    assert body["analysis_error"] is None
    assert body["analysis"]["condition"] == "late_blight"
    assert body["analysis"]["disclaimer"], "the honesty fields survive the model path"


async def test_each_engine_stamps_its_own_prompt_version(
    client: AsyncClient, api_prefix: str, farm: dict, monkeypatch, gemini_mode
) -> None:
    """`prompt_version` exists so a stored diagnosis can be traced to the wording behind
    it. Stamping the fixture's version on a model result made every vision answer look
    like it came from `_simulate_diagnosis`.

    A fallback carries the *simulator's* version, because the simulator is what answered.
    """
    patch_vision(monkeypatch, ProviderResult.live(PLANT, "gemini"))
    from_model = await upload(client, api_prefix, farm["id"], analyze=True)

    patch_vision(monkeypatch, ProviderResult.unavailable("gemini", "quota exceeded"))
    from_fallback = await upload(client, api_prefix, farm["id"], analyze=True)

    assert from_model["prompt_version"] == image_service.VISION_PROMPT_VERSION
    assert from_fallback["prompt_version"] == image_service.PROMPT_VERSION
    assert from_model["prompt_version"] != from_fallback["prompt_version"]


def test_the_two_prompt_versions_are_distinct() -> None:
    """A shared constant would make the traceability above impossible to assert."""
    assert image_service.VISION_PROMPT_VERSION == "phase5-vision-v1"
    assert image_service.PROMPT_VERSION == "phase3-vision-fixture-v1"
    assert image_service.VISION_PROMPT_VERSION != image_service.PROMPT_VERSION


async def test_the_stored_photograph_is_what_is_examined(
    client: AsyncClient, api_prefix: str, farm: dict, monkeypatch, gemini_mode
) -> None:
    called = patch_vision(monkeypatch, ProviderResult.live(PLANT, "gemini"))

    await upload(client, api_prefix, farm["id"], analyze=True)

    assert called.calls, "the model must actually be consulted"
    assert called.calls[0]["content_type"] == "image/jpeg"


async def test_a_non_plant_result_is_a_success_not_a_fallback(
    client: AsyncClient, api_prefix: str, farm: dict, monkeypatch, gemini_mode
) -> None:
    """The load-bearing one. A confident "not a plant" must NOT degrade to the simulator,
    which would invent a disease for a photograph of a keyboard."""
    patch_vision(monkeypatch, ProviderResult.live(NOT_PLANT, "gemini"))

    body = await upload(client, api_prefix, farm["id"], analyze=True)

    assert body["ai_mode"] == "gemini", "an honest non-plant verdict is a real answer"
    assert body["analysis"]["is_plant_material"] is False
    assert body["analysis"]["condition"] == "not_plant_material"
    assert body["analysis"]["symptoms_observed"] == []
    assert body["analysis"]["treatment_options"] == []
    assert body["analysis"]["severity"] == "none"


# --------------------------------------------------------------------------
# Fallback
# --------------------------------------------------------------------------


async def test_a_provider_failure_falls_back_and_says_so(
    client: AsyncClient, api_prefix: str, farm: dict, monkeypatch, gemini_mode
) -> None:
    patch_vision(
        monkeypatch, ProviderResult.unavailable("gemini", "Gemini API error: quota exceeded")
    )

    body = await upload(client, api_prefix, farm["id"], analyze=True)

    assert body["ai_mode"] == "fallback"
    assert body["model"] is None
    assert "quota exceeded" in body["analysis_error"]
    assert body["analysis"] is not None, "a degraded answer is still an answer"
    assert body["analysis"]["disclaimer"]


async def test_a_fallback_still_carries_the_simulator_result(
    client: AsyncClient, api_prefix: str, farm: dict, monkeypatch, gemini_mode
) -> None:
    """Degrading must not mean returning nothing — the dashboard still has to render."""
    patch_vision(monkeypatch, ProviderResult.unavailable("gemini", "timeout"))

    body = await upload(client, api_prefix, farm["id"], analyze=True)

    assert body["analysis"]["condition"]
    assert body["analysis"]["immediate_actions"]


async def test_an_image_with_no_stored_bytes_falls_back_without_calling_a_model(
    client: AsyncClient, api_prefix: str, farm: dict, monkeypatch, gemini_mode
) -> None:
    """A row uploaded before pixels were retained. Nothing is fabricated to stand in for
    the photograph, and the mode says a model did not produce this."""
    called = patch_vision(monkeypatch, ProviderResult.live(PLANT, "gemini"))
    created = await upload(client, api_prefix, farm["id"])

    monkeypatch.setattr(image_service, "_pixels_for", lambda _id: None)
    body = (await client.post(f"{api_prefix}/crop-images/{created['id']}/analyze")).json()

    assert body["ai_mode"] == "fallback"
    assert "No stored image data" in body["analysis_error"]
    assert called.calls == [], "there is nothing to send, so nothing is sent"


# --------------------------------------------------------------------------
# Reuse and persistence are unaffected
# --------------------------------------------------------------------------


async def test_a_completed_diagnosis_is_not_re_billed(
    client: AsyncClient, api_prefix: str, farm: dict, monkeypatch, gemini_mode
) -> None:
    """Reuse matters more once each analysis is a paid model call."""
    called = patch_vision(monkeypatch, ProviderResult.live(PLANT, "gemini"))
    created = await upload(client, api_prefix, farm["id"], analyze=True)

    again = (await client.post(f"{api_prefix}/crop-images/{created['id']}/analyze")).json()

    assert len(called.calls) == 1, "a stored diagnosis must not trigger a second call"
    assert again["analysis"] == created["analysis"]
    assert again["ai_mode"] == "gemini"


async def test_the_digest_is_unaffected_by_the_vision_path(
    client: AsyncClient, api_prefix: str, farm: dict, monkeypatch, gemini_mode
) -> None:
    from uuid import UUID

    patch_vision(monkeypatch, ProviderResult.live(PLANT, "gemini"))
    created = await upload(client, api_prefix, farm["id"], analyze=True)

    assert image_service._digest_for(UUID(created["id"])) is not None
