"""Gemini wiring in the analysis flow: `_build_run` narrating its own already-
computed numbers through `app.ai.gemini.generate_narrative`, never raising into
`POST /farms/{id}/analysis` and never altering the deterministic figures.

`gemini.generate_narrative` is monkeypatched at the module level analysis_service
imports (`app.ai.gemini`), so nothing here reaches the network or requires a real
API key — the boundary under test is the wiring, not the provider itself (that is
`tests/ai/test_gemini.py`'s job).
"""

import pytest
from httpx import AsyncClient

from app.ai import gemini
from app.providers.base import ProviderResult

DETERMINISTIC_FIELDS = (
    "overall_health_score",
    "overall_band",
    "factors",
    "weather_risk",
    "water_risk",
    "disease_risk",
    "crop_health",
    "soil_assessment",
    "crop_recommendations",
    "regenerative_recommendations",
)


def _advisory_content(advisories: list[dict]) -> list[dict]:
    """Advisory fields that must never move, with the per-run `id`/`analysis_run_id`/
    `created_at` stripped out."""
    return [
        {k: v for k, v in advisory.items() if k not in {"id", "analysis_run_id", "created_at"}}
        for advisory in advisories
    ]


# --------------------------------------------------------------------------
# Mock mode: today's existing behaviour, untouched
# --------------------------------------------------------------------------


async def test_mock_mode_is_unchanged(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    """The default `AI_PROVIDER=mock` suite configuration must never call Gemini."""
    monkeypatch.setattr(gemini, "generate_narrative", pytest.fail)  # any call fails the test

    resp = await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")

    assert resp.status_code == 200
    run = resp.json()
    assert run["ai_mode"] == "mock"
    assert run["model"] is None
    assert run["summary"]


# --------------------------------------------------------------------------
# Successful Gemini mode
# --------------------------------------------------------------------------


async def test_successful_gemini_narrative_is_used_as_the_summary(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    monkeypatch.setattr(gemini.settings, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(gemini.settings, "GEMINI_MODEL", "gemini-2.5-flash")

    async def fake_generate_narrative(facts: gemini.AnalysisFacts) -> ProviderResult[str]:
        return ProviderResult.live(
            "Your farm is doing well; keep an eye on water.", gemini.GEMINI_SOURCE
        )

    monkeypatch.setattr(gemini, "generate_narrative", fake_generate_narrative)

    resp = await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")

    assert resp.status_code == 200
    run = resp.json()
    assert run["ai_mode"] == "gemini"
    assert run["model"] == "gemini-2.5-flash"
    assert run["summary"] == "Your farm is doing well; keep an eye on water."


# --------------------------------------------------------------------------
# Gemini failure -> deterministic fallback
# --------------------------------------------------------------------------


async def test_gemini_failure_falls_back_to_the_deterministic_summary(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    monkeypatch.setattr(gemini.settings, "AI_PROVIDER", "gemini")

    async def failing_generate_narrative(facts: gemini.AnalysisFacts) -> ProviderResult[str]:
        return ProviderResult.unavailable(gemini.GEMINI_SOURCE, "Gemini API error: 500 INTERNAL")

    monkeypatch.setattr(gemini, "generate_narrative", failing_generate_narrative)

    # What mock mode would have produced for the same farm, for comparison.
    mock_resp = await client.post(
        f"{api_prefix}/farms/{planted_farm['id']}/analysis", params={"force_refresh": True}
    )
    monkeypatch.setattr(gemini.settings, "AI_PROVIDER", "mock")
    deterministic_summary = mock_resp.json()["summary"]
    monkeypatch.setattr(gemini.settings, "AI_PROVIDER", "gemini")

    resp = await client.post(
        f"{api_prefix}/farms/{planted_farm['id']}/analysis", params={"force_refresh": True}
    )

    assert resp.status_code == 200
    run = resp.json()
    assert run["ai_mode"] == "fallback"
    assert run["model"] is None
    assert run["summary"] == deterministic_summary


async def test_gemini_failure_never_raises_into_the_endpoint(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    """End-to-end through the real (unmocked) `generate_narrative`: with
    `AI_PROVIDER=gemini` and no API key configured, the request must still succeed.
    This exercises the actual wiring, not a stand-in for it — `gemini.py`'s own
    exhaustive failure-mode coverage (timeout, API error, malformed response) lives
    in `tests/ai/test_gemini.py`.
    """
    monkeypatch.setattr(gemini.settings, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", None)
    gemini.reset_client()

    resp = await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")

    assert resp.status_code == 200
    run = resp.json()
    assert run["ai_mode"] == "fallback"
    assert run["model"] is None
    assert run["summary"]


# --------------------------------------------------------------------------
# Gemini timeout -> fallback
# --------------------------------------------------------------------------


async def test_gemini_timeout_falls_back(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    monkeypatch.setattr(gemini.settings, "AI_PROVIDER", "gemini")

    async def timed_out_generate_narrative(facts: gemini.AnalysisFacts) -> ProviderResult[str]:
        # Exactly what `generate_narrative` itself returns on a real timeout —
        # see `tests/ai/test_gemini.py::test_timeout_is_unavailable_not_an_exception`.
        return ProviderResult.unavailable(
            gemini.GEMINI_SOURCE, "Gemini did not respond within 5.0s."
        )

    monkeypatch.setattr(gemini, "generate_narrative", timed_out_generate_narrative)

    resp = await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")

    assert resp.status_code == 200
    run = resp.json()
    assert run["ai_mode"] == "fallback"
    assert run["model"] is None
    assert run["summary"]  # the deterministic summary, still present


# --------------------------------------------------------------------------
# Gemini receives the computed facts
# --------------------------------------------------------------------------


async def test_gemini_receives_the_computed_facts(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    monkeypatch.setattr(gemini.settings, "AI_PROVIDER", "gemini")

    captured: list[gemini.AnalysisFacts] = []

    async def capturing_generate_narrative(facts: gemini.AnalysisFacts) -> ProviderResult[str]:
        captured.append(facts)
        return ProviderResult.live("narrated", gemini.GEMINI_SOURCE)

    monkeypatch.setattr(gemini, "generate_narrative", capturing_generate_narrative)

    resp = await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")

    assert resp.status_code == 200
    run = resp.json()
    assert len(captured) == 1
    facts = captured[0]

    assert facts.farm_name == planted_farm["name"]
    assert facts.overall_score == run["overall_health_score"]
    assert facts.overall_band == run["overall_band"]
    assert facts.weather.level == run["weather_risk"]["level"]
    assert facts.weather.score == run["weather_risk"]["score"]
    assert facts.water.level == run["water_risk"]["level"]
    assert facts.disease.level == run["disease_risk"]["level"]
    assert facts.crop_health_score == run["crop_health"]["score"]
    assert facts.soil_band == run["soil_assessment"]["band"]
    assert [a.title for a in facts.advisories] == [a["title"] for a in run["advisories"]]
    assert [a.priority for a in facts.advisories] == [a["priority"] for a in run["advisories"]]


# --------------------------------------------------------------------------
# Deterministic values are unaffected by Gemini
# --------------------------------------------------------------------------


async def test_risk_scores_and_advisories_are_identical_with_and_without_gemini(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    """The strongest guarantee: the same farm, scored twice — once under `mock`,
    once under `gemini` with a narrative that bears no resemblance to the
    deterministic summary — must produce byte-identical risk scores, bands,
    factors, crop/regenerative recommendations, and advisories (content, not id).
    Only `summary`, `ai_mode`, and `model` may differ.
    """
    mock_resp = await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")
    assert mock_resp.status_code == 200
    mock_run = mock_resp.json()
    assert mock_run["ai_mode"] == "mock"

    monkeypatch.setattr(gemini.settings, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(gemini.settings, "GEMINI_MODEL", "gemini-2.5-flash")

    async def fake_generate_narrative(facts: gemini.AnalysisFacts) -> ProviderResult[str]:
        return ProviderResult.live(
            "Totally different wording that must not change a single computed number.",
            gemini.GEMINI_SOURCE,
        )

    monkeypatch.setattr(gemini, "generate_narrative", fake_generate_narrative)

    gemini_resp = await client.post(
        f"{api_prefix}/farms/{planted_farm['id']}/analysis", params={"force_refresh": True}
    )
    assert gemini_resp.status_code == 200
    gemini_run = gemini_resp.json()
    assert gemini_run["ai_mode"] == "gemini"

    for field in DETERMINISTIC_FIELDS:
        assert gemini_run[field] == mock_run[field], f"{field} changed under Gemini narration"

    assert _advisory_content(gemini_run["advisories"]) == _advisory_content(mock_run["advisories"])

    # The parts Gemini is allowed to change did change.
    assert gemini_run["summary"] != mock_run["summary"]
    assert gemini_run["model"] == "gemini-2.5-flash"
    assert mock_run["model"] is None


async def test_analysis_facts_do_not_leak_a_narrative_back_into_risk_data(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    """A Gemini response that *looks like* structured data (e.g. hallucinated JSON
    with different numbers) must still only ever land in `summary` — never be
    parsed back into a score or a risk level."""
    monkeypatch.setattr(gemini.settings, "AI_PROVIDER", "gemini")

    async def mischievous_generate_narrative(facts: gemini.AnalysisFacts) -> ProviderResult[str]:
        return ProviderResult.live(
            '{"overall_health_score": 1, "water_risk": {"level": "low", "score": 0}}',
            gemini.GEMINI_SOURCE,
        )

    monkeypatch.setattr(gemini, "generate_narrative", mischievous_generate_narrative)

    resp = await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")

    assert resp.status_code == 200
    run = resp.json()
    assert run["summary"].startswith("{")  # the mischievous text landed only in summary
    assert run["overall_health_score"] != 1 or run["water_risk"]["score"] != 0
    assert 0 <= run["overall_health_score"] <= 100
    assert run["water_risk"]["level"] in {"low", "moderate", "high", "severe"}
