"""The Gemini narrative provider: prompt construction, and every failure mode
degrading to an `unavailable` ProviderResult rather than raising.

The Gemini SDK itself is never exercised — `gemini._get_client` is monkeypatched to
a stub, so nothing here reaches the network. This mirrors how `tests/providers/`
mocks transport with respx: the boundary being tested is "does this module honour
the ProviderResult contract", not "does the Gemini API work".
"""

import dataclasses
import logging

import pytest
from google.genai import errors as genai_errors

from app.ai import gemini
from app.schemas.common import DataMode

FAKE_KEY = "fake-gemini-key-do-not-log-me"


@pytest.fixture(autouse=True)
def _reset_client():
    """The module caches a client singleton; a stale one from a previous test must
    not leak into the next."""
    gemini.reset_client()
    yield
    gemini.reset_client()


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    """Most tests want a configured provider; the missing-key test overrides this."""
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(gemini.settings, "GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(gemini.settings, "AI_TIMEOUT_S", 5.0)
    monkeypatch.setattr(gemini.settings, "AI_TEMPERATURE", 0.3)


def _facts(**overrides) -> gemini.AnalysisFacts:
    base = {
        "farm_name": "North Field",
        "crop_name": "Maize",
        "overall_score": 62,
        "overall_band": "moderate",
        "weather": gemini.RiskFact(
            level="high",
            score=71,
            explanation="4 forecast days above 34°C during flowering.",
            drivers=("4 days above 34°C", "no rain forecast for 6 days"),
        ),
        "water": gemini.RiskFact(
            level="severe",
            score=85,
            explanation="Water balance is -45 mm over the assessment window.",
            drivers=("45 mm deficit",),
        ),
        "disease": gemini.RiskFact(
            level="moderate",
            score=48,
            explanation="Humidity and temperature favour late blight.",
        ),
        "crop_health_score": 58,
        "crop_health_band": "moderate",
        "crop_health_explanation": "NDVI declined 12% over 14 days.",
        "soil_band": "good",
        "soil_explanation": "pH is within maize's tolerated range.",
        "advisories": (
            gemini.AdvisoryFact(
                title="Irrigate within 48 hours",
                body="Apply about 45 mm of irrigation.",
                rationale="Water balance is -45 mm with no rain forecast.",
                priority="high",
            ),
        ),
        "degraded_sources": ("soil",),
        "simulated_sources": ("vegetation",),
    }
    base.update(overrides)
    return gemini.AnalysisFacts(**base)


class _FakeResponse:
    def __init__(self, text: str | None) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, *, result=None, exc: Exception | None = None, delay: float = 0.0):
        self._result = result
        self._exc = exc
        self._delay = delay
        self.calls: list[dict] = []

    async def generate_content(self, *, model, contents, config):
        import asyncio

        self.calls.append({"model": model, "contents": contents, "config": config})
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc is not None:
            raise self._exc
        return self._result


class _FakeAio:
    def __init__(self, models: _FakeModels) -> None:
        self.models = models


class _FakeClient:
    def __init__(self, models: _FakeModels) -> None:
        self.aio = _FakeAio(models)


def _install_fake_client(monkeypatch, models: _FakeModels) -> None:
    monkeypatch.setattr(gemini, "_get_client", lambda: _FakeClient(models))


# --------------------------------------------------------------------------
# Prompt construction
# --------------------------------------------------------------------------


def test_prompt_contains_the_supplied_facts() -> None:
    facts = _facts()

    prompt = gemini.build_prompt(facts)

    assert "North Field" in prompt
    assert "Maize" in prompt
    assert "62/100" in prompt
    assert "high (71/100)" in prompt
    assert "severe (85/100)" in prompt
    assert "-45 mm over the assessment window" in prompt
    assert "4 days above 34°C" in prompt
    assert "Irrigate within 48 hours" in prompt
    assert "Apply about 45 mm of irrigation" in prompt
    assert "vegetation" in prompt  # simulated source, named
    assert "soil" in prompt  # degraded source, named


def test_prompt_labels_simulated_and_unavailable_data_distinctly() -> None:
    prompt = gemini.build_prompt(_facts())

    assert "Simulated, not a real observation" in prompt
    assert "Unavailable this run" in prompt


def test_prompt_omits_optional_sections_when_absent() -> None:
    facts = _facts(advisories=(), degraded_sources=(), simulated_sources=())

    prompt = gemini.build_prompt(facts)

    assert "Existing advisories" not in prompt
    assert "Simulated" not in prompt
    assert "Unavailable" not in prompt


def test_prompt_is_deterministic() -> None:
    facts = _facts()

    assert gemini.build_prompt(facts) == gemini.build_prompt(facts)


def test_system_instruction_states_every_required_constraint() -> None:
    """The prompt-safety rules the task specifies must actually be in the text sent
    to the model, not just asserted in a docstring."""
    instruction = gemini.SYSTEM_INSTRUCTION

    assert "only the facts supplied" in instruction
    assert "Never invent a measurement" in instruction
    assert "Never invent weather, soil, or crop information" in instruction
    assert "Never change, adjust, round differently, or recompute" in instruction
    assert "Never create a recommendation or advisory" in instruction
    assert "unavailable or simulated" in instruction
    assert "concise" in instruction.lower()


# --------------------------------------------------------------------------
# Structural immutability: Gemini cannot alter structured risk data
# --------------------------------------------------------------------------


def test_analysis_facts_is_frozen() -> None:
    """Structurally enforced, not just a convention: an attempted mutation raises
    rather than silently altering an already-computed risk figure."""
    facts = _facts()

    with pytest.raises(dataclasses.FrozenInstanceError):
        facts.overall_score = 999  # type: ignore[misc]

    with pytest.raises(dataclasses.FrozenInstanceError):
        facts.weather.score = 0  # type: ignore[misc]


async def test_generate_narrative_does_not_alter_the_supplied_facts(monkeypatch) -> None:
    facts = _facts()
    snapshot = dataclasses.replace(facts)  # a value-equal copy taken before the call
    _install_fake_client(monkeypatch, _FakeModels(result=_FakeResponse("A calm summary.")))

    await gemini.generate_narrative(facts)

    assert facts == snapshot


# --------------------------------------------------------------------------
# Successful call
# --------------------------------------------------------------------------


async def test_successful_response_is_live(monkeypatch) -> None:
    models = _FakeModels(result=_FakeResponse("  Irrigate soon; heat stress is elevated.  "))
    _install_fake_client(monkeypatch, models)

    result = await gemini.generate_narrative(_facts())

    assert result.ok
    assert result.data == "Irrigate soon; heat stress is elevated."  # whitespace trimmed
    assert result.meta.mode is DataMode.live
    assert result.meta.source == gemini.GEMINI_SOURCE
    assert models.calls[0]["model"] == "gemini-2.5-flash"
    assert models.calls[0]["config"].system_instruction == gemini.SYSTEM_INSTRUCTION
    assert models.calls[0]["config"].temperature == 0.3


async def test_successful_call_sends_the_built_prompt(monkeypatch) -> None:
    facts = _facts()
    models = _FakeModels(result=_FakeResponse("ok"))
    _install_fake_client(monkeypatch, models)

    await gemini.generate_narrative(facts)

    assert models.calls[0]["contents"] == gemini.build_prompt(facts)


# --------------------------------------------------------------------------
# Missing API key
# --------------------------------------------------------------------------


async def test_missing_api_key_is_unavailable_without_calling_the_client(monkeypatch) -> None:
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", None)

    def _fail_if_called():
        raise AssertionError("the client must not be constructed with no API key")

    monkeypatch.setattr(gemini, "_get_client", _fail_if_called)

    result = await gemini.generate_narrative(_facts())

    assert result.ok is False
    assert result.data is None
    assert result.meta.mode is DataMode.unavailable
    assert "not configured" in (result.meta.note or "")


async def test_empty_string_api_key_is_treated_as_unset(monkeypatch) -> None:
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", "")

    result = await gemini.generate_narrative(_facts())

    assert result.ok is False
    assert result.meta.mode is DataMode.unavailable


# --------------------------------------------------------------------------
# Timeout
# --------------------------------------------------------------------------


async def test_timeout_is_unavailable_not_an_exception(monkeypatch) -> None:
    monkeypatch.setattr(gemini.settings, "AI_TIMEOUT_S", 0.05)
    _install_fake_client(monkeypatch, _FakeModels(result=_FakeResponse("too slow"), delay=1.0))

    result = await gemini.generate_narrative(_facts())

    assert result.ok is False
    assert result.meta.mode is DataMode.unavailable
    assert "did not respond" in (result.meta.note or "")


# --------------------------------------------------------------------------
# HTTP / API failure
# --------------------------------------------------------------------------


async def test_api_error_is_unavailable_not_an_exception(monkeypatch) -> None:
    exc = genai_errors.APIError(
        code=500, response_json={"error": {"status": "INTERNAL", "message": "server exploded"}}
    )
    _install_fake_client(monkeypatch, _FakeModels(exc=exc))

    result = await gemini.generate_narrative(_facts())

    assert result.ok is False
    assert result.meta.mode is DataMode.unavailable
    assert "Gemini API error" in (result.meta.note or "")


async def test_transport_error_is_unavailable_not_an_exception(monkeypatch) -> None:
    _install_fake_client(monkeypatch, _FakeModels(exc=ConnectionError("no route to host")))

    result = await gemini.generate_narrative(_facts())

    assert result.ok is False
    assert result.meta.mode is DataMode.unavailable
    assert "Gemini request failed" in (result.meta.note or "")


# --------------------------------------------------------------------------
# Malformed response
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text", [None, "", "   "])
async def test_empty_or_missing_text_is_unavailable(monkeypatch, text) -> None:
    _install_fake_client(monkeypatch, _FakeModels(result=_FakeResponse(text)))

    result = await gemini.generate_narrative(_facts())

    assert result.ok is False
    assert result.meta.mode is DataMode.unavailable
    assert "no usable text" in (result.meta.note or "")


# --------------------------------------------------------------------------
# No secret appears in logs or errors
# --------------------------------------------------------------------------


async def test_api_error_message_containing_the_key_is_redacted(monkeypatch, caplog) -> None:
    """Worst case: the upstream error body echoes something containing the key
    (e.g. a proxy re-quoting the failed request). The key must never survive into
    the returned note or the log line."""
    exc = genai_errors.APIError(
        code=401,
        response_json={"error": {"status": "UNAUTHENTICATED", "message": f"bad key {FAKE_KEY}"}},
    )
    _install_fake_client(monkeypatch, _FakeModels(exc=exc))

    with caplog.at_level(logging.WARNING, logger="app.ai.gemini"):
        result = await gemini.generate_narrative(_facts())

    assert FAKE_KEY not in (result.meta.note or "")
    assert FAKE_KEY not in caplog.text
    assert "***" in (result.meta.note or "")


async def test_transport_error_message_containing_the_key_is_redacted(monkeypatch, caplog) -> None:
    _install_fake_client(monkeypatch, _FakeModels(exc=RuntimeError(f"leaked {FAKE_KEY} in url")))

    with caplog.at_level(logging.WARNING, logger="app.ai.gemini"):
        result = await gemini.generate_narrative(_facts())

    assert FAKE_KEY not in (result.meta.note or "")
    assert FAKE_KEY not in caplog.text


def test_redact_replaces_the_configured_key(monkeypatch) -> None:
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", FAKE_KEY)

    assert gemini._redact(f"error near {FAKE_KEY} in request") == "error near *** in request"


def test_redact_is_a_noop_with_no_key_configured(monkeypatch) -> None:
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", None)

    assert gemini._redact("plain error text") == "plain error text"


async def test_missing_key_log_line_never_contains_a_key_value(monkeypatch, caplog) -> None:
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", None)

    with caplog.at_level(logging.WARNING, logger="app.ai.gemini"):
        await gemini.generate_narrative(_facts())

    assert FAKE_KEY not in caplog.text
