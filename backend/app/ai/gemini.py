"""Gemini narrative provider — Phase 1.

Turns already-computed TerraNex structured analysis into a concise, farmer-friendly
narrative. This module never computes a score, a risk level, or a recommendation:
every fact it can reference is supplied by the caller in :class:`AnalysisFacts`,
built entirely from the deterministic risk engine's output (see
`app/services/analysis_service.py`). Gemini's only job is to explain those facts in
plain language — never to add to them. The prompt in :data:`SYSTEM_INSTRUCTION`
states this as an explicit constraint, not just a docstring convention.

Follows the same shape every other provider in `app/providers/` uses:

* a :class:`~app.providers.base.ProviderResult` return — this module never raises
  into a caller, exactly like `app/providers/weather.py` and `app/providers/soil.py`;
* a `provider_deadline`-bound call, so a slow Gemini response cannot hold a request
  open indefinitely;
* provenance that can only ever say `live` (a real narrative was produced) or
  `unavailable` (it wasn't) — never a fabricated success.

**Not wired into `analysis_service.py` yet.** This is the provider layer only. The
call site inside `_build_run()` — where `ai_mode` is currently hardcoded to
`AIMode.mock` — is a later phase.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.base import ProviderResult
from app.providers.http import provider_deadline, remaining_budget

logger = get_logger(__name__)

GEMINI_SOURCE = "gemini"


# --------------------------------------------------------------------------
# Structured input: exactly what Gemini is allowed to see.
#
# Frozen and slotted so nothing downstream — including this module — can mutate a
# caller's already-computed data by accident. The provider only ever reads it.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RiskFact:
    """One risk section's already-computed read, reduced to what a narrative needs."""

    level: str
    score: int
    explanation: str
    drivers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AdvisoryFact:
    """One already-generated advisory. Gemini may restate this; it may not add one."""

    title: str
    body: str
    rationale: str
    priority: str


@dataclass(frozen=True, slots=True)
class AnalysisFacts:
    """The complete set of facts a Gemini narrative may draw on — nothing else.

    Every field is copied from engine output the caller already computed. This is
    deliberately a separate, minimal shape rather than the full `AnalysisRun`
    schema, so the prompt can never accidentally include a field (an id, an
    internal note) nobody reviewed for exposure to a third-party model.
    """

    farm_name: str
    crop_name: str | None
    overall_score: int
    overall_band: str
    weather: RiskFact
    water: RiskFact
    disease: RiskFact
    crop_health_score: int
    crop_health_band: str
    crop_health_explanation: str
    soil_band: str
    soil_explanation: str
    advisories: tuple[AdvisoryFact, ...] = ()
    degraded_sources: tuple[str, ...] = ()
    simulated_sources: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------

SYSTEM_INSTRUCTION = """You are TerraNex's narrative assistant for a farmer-facing \
agricultural advisory app. You will be given a fixed set of already-computed facts \
— risk scores, bands, and advisories the deterministic TerraNex risk engine already \
calculated. Your only job is to explain those facts in plain, concise, \
farmer-friendly language.

Rules you must follow exactly:
1. Use only the facts supplied in this prompt. Do not draw on outside knowledge of \
weather, soil, crops, or farming beyond restating and explaining what is given.
2. Never invent a measurement, a number, or a data point that is not present in the \
supplied facts.
3. Never invent weather, soil, or crop information that is not present in the \
supplied facts.
4. Never change, adjust, round differently, or recompute any risk score, band, or \
level. Report them exactly as given.
5. Never create a recommendation or advisory that is not already present in the \
supplied facts. You may rephrase an existing advisory; you may not add a new one.
6. Clearly distinguish data marked as unavailable or simulated from real \
measurements — say so plainly rather than presenting it as an observation.
7. Keep the response concise (3-5 sentences) and written for a farmer, not an \
agronomist — plain language, no jargon."""


def build_prompt(facts: AnalysisFacts) -> str:
    """Render `facts` into the prompt sent alongside `SYSTEM_INSTRUCTION`.

    Pure and deterministic: identical facts always render identical text, which is
    what makes the prompt itself unit-testable without calling the model.
    """
    lines = [
        f"Farm: {facts.farm_name}",
        f"Current crop: {facts.crop_name or 'none registered'}",
        f"Overall health score: {facts.overall_score}/100 ({facts.overall_band})",
        "",
        f"Weather risk: {facts.weather.level} ({facts.weather.score}/100) — "
        f"{facts.weather.explanation}",
    ]
    if facts.weather.drivers:
        lines.append("  Drivers: " + "; ".join(facts.weather.drivers))

    lines.append(
        f"Water risk: {facts.water.level} ({facts.water.score}/100) — {facts.water.explanation}"
    )
    if facts.water.drivers:
        lines.append("  Drivers: " + "; ".join(facts.water.drivers))

    lines.append(
        f"Disease risk: {facts.disease.level} ({facts.disease.score}/100) — "
        f"{facts.disease.explanation}"
    )
    if facts.disease.drivers:
        lines.append("  Drivers: " + "; ".join(facts.disease.drivers))

    lines.append(
        f"Crop health: {facts.crop_health_score}/100 ({facts.crop_health_band}) — "
        f"{facts.crop_health_explanation}"
    )
    lines.append(f"Soil: {facts.soil_band} — {facts.soil_explanation}")

    if facts.advisories:
        lines.append("")
        lines.append("Existing advisories (do not add to this list, only explain it):")
        for advisory in facts.advisories:
            lines.append(
                f"- [{advisory.priority}] {advisory.title}: {advisory.body} ({advisory.rationale})"
            )

    if facts.simulated_sources:
        lines.append("")
        lines.append(
            "Simulated, not a real observation — label as such if mentioned: "
            + ", ".join(facts.simulated_sources)
        )
    if facts.degraded_sources:
        lines.append(
            "Unavailable this run — label as such if mentioned: "
            + ", ".join(facts.degraded_sources)
        )

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Secret hygiene
# --------------------------------------------------------------------------


def _redact(text: str) -> str:
    """Strip the configured API key out of any string before it reaches a log or a
    `ProviderResult` note.

    Defence in depth: the SDK sends the key in a request header, never in an error
    body we've observed, but a future SDK version or an intermediary proxy echoing
    the request is not something this module gets to assume away.
    """
    key = settings.GEMINI_API_KEY
    if key:
        text = text.replace(key, "***")
    return text


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """The shared Gemini client, created lazily. Assumes the caller already checked
    `settings.GEMINI_API_KEY` is set."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


def reset_client() -> None:
    """Drop the cached client. Test-only hook — production never rotates
    `GEMINI_API_KEY` without a process restart."""
    global _client
    _client = None


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


async def generate_narrative(facts: AnalysisFacts) -> ProviderResult[str]:
    """Ask Gemini to narrate `facts`.

    Never raises: every failure mode below — a missing key, a timeout, an API
    error, a malformed or empty response — is converted into an `unavailable`
    `ProviderResult`, exactly like every other provider in `app/providers/`. A
    caller can degrade to `AIMode.fallback` without wrapping this in its own
    try/except.
    """
    if not settings.GEMINI_API_KEY:
        logger.warning("gemini_missing_api_key", extra={"source": GEMINI_SOURCE})
        return ProviderResult.unavailable(
            GEMINI_SOURCE, "GEMINI_API_KEY is not configured; narrative generation skipped."
        )

    prompt = build_prompt(facts)
    client = _get_client()

    with provider_deadline(settings.AI_TIMEOUT_S):
        budget = remaining_budget()
        timeout = settings.AI_TIMEOUT_S if budget is None else max(0.1, budget)

        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=settings.GEMINI_MODEL,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION,
                        temperature=settings.AI_TEMPERATURE,
                    ),
                ),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning(
                "gemini_timeout",
                extra={
                    "source": GEMINI_SOURCE,
                    "model": settings.GEMINI_MODEL,
                    "timeout_s": round(timeout, 2),
                },
            )
            return ProviderResult.unavailable(
                GEMINI_SOURCE, f"Gemini did not respond within {timeout:.1f}s."
            )
        except genai_errors.APIError as exc:
            reason = _redact(f"{type(exc).__name__}: {exc.message or exc.status or exc.code}")
            logger.warning(
                "gemini_api_error",
                extra={"source": GEMINI_SOURCE, "model": settings.GEMINI_MODEL, "reason": reason},
            )
            return ProviderResult.unavailable(GEMINI_SOURCE, f"Gemini API error: {reason}")
        except Exception as exc:  # transport-level surprises: DNS, TLS, connection reset
            reason = _redact(str(exc))
            logger.warning(
                "gemini_transport_error",
                extra={"source": GEMINI_SOURCE, "model": settings.GEMINI_MODEL, "reason": reason},
            )
            return ProviderResult.unavailable(GEMINI_SOURCE, f"Gemini request failed: {reason}")

    text = (response.text or "").strip()
    if not text:
        logger.warning(
            "gemini_empty_response",
            extra={"source": GEMINI_SOURCE, "model": settings.GEMINI_MODEL},
        )
        return ProviderResult.unavailable(
            GEMINI_SOURCE, "Gemini returned no usable text (empty or blocked response)."
        )

    return ProviderResult.live(
        text, GEMINI_SOURCE, note=f"Narrative generated by {settings.GEMINI_MODEL}."
    )
