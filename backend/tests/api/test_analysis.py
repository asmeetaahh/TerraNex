"""Analysis runs, the dashboard, projections and recommendations."""

import pytest
from httpx import AsyncClient

PROJECTION_ENDPOINTS = [
    "risks/weather",
    "risks/water",
    "risks/disease",
    "health",
    "advisories",
    "recommendations/crops",
    "recommendations/regenerative",
]


async def test_analysis_returns_every_section(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    resp = await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")

    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "complete"
    assert 0 <= run["overall_health_score"] <= 100
    assert run["overall_band"] in {"excellent", "good", "moderate", "poor", "critical"}
    assert run["summary"]
    for section in (
        "weather_risk",
        "water_risk",
        "disease_risk",
        "crop_health",
        "soil_assessment",
    ):
        assert run[section] is not None, f"{section} missing"
    assert run["advisories"]
    assert run["crop_recommendations"]
    assert run["regenerative_recommendations"]


async def test_analysis_declares_mock_ai_and_simulated_sources(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """No model is called in Phase 3, and the payload must say so."""
    run = (await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")).json()

    assert run["ai_mode"] == "mock"
    assert run["model"] is None
    assert run["prompt_version"]
    assert run["sources"]
    assert all(s["mode"] == "simulated" for s in run["sources"])
    assert all(s["source"] == "simulated" for s in run["sources"])


async def test_summary_discloses_that_figures_are_simulated(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    run = (await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")).json()
    assert "simulated" in run["summary"].lower()


async def test_factors_decompose_the_composite_score(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """An opaque score is far less useful than the reasons behind it."""
    run = (await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")).json()

    keys = {f["key"] for f in run["factors"]}
    assert {"weather_risk", "water_risk", "disease_risk", "soil_suitability"} <= keys
    for factor in run["factors"]:
        assert 0 <= factor["score"] <= 100
        assert 0 <= factor["weight"] <= 1
        assert factor["explanation"]


async def test_water_risk_balance_is_internally_consistent(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """`deficit_mm` must agree with `water_balance_mm`, or the panel contradicts itself."""
    run = (await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")).json()
    water = run["water_risk"]

    if water["water_balance_mm"] < 0:
        assert water["deficit_mm"] == pytest.approx(abs(water["water_balance_mm"]), rel=0.01)
    else:
        assert water["deficit_mm"] == 0


async def test_advisories_are_priority_ordered_with_rationale(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    run = (await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")).json()

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    ranks = [order[a["priority"]] for a in run["advisories"]]
    assert ranks == sorted(ranks)

    for advisory in run["advisories"]:
        assert advisory["title"] and advisory["body"]
        assert advisory["rationale"], "every advisory must cite what produced it"
        assert 0 <= advisory["confidence"] <= 1
        assert advisory["analysis_run_id"] == run["id"]


async def test_recommendations_are_ranked_and_scored(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    run = (await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")).json()

    crops = run["crop_recommendations"]
    assert [c["rank"] for c in crops] == list(range(1, len(crops) + 1))
    scores = [c["suitability_score"] for c in crops]
    assert scores == sorted(scores, reverse=True)

    regen = run["regenerative_recommendations"]
    assert [r["rank"] for r in regen] == list(range(1, len(regen) + 1))
    assert all(r["implementation_steps"] for r in regen)


# --------------------------------------------------------------------------
# Caching and determinism
# --------------------------------------------------------------------------


async def test_repeat_analysis_returns_the_same_stored_run(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """Without `force_refresh`, a repeat call must not recompute or duplicate."""
    first = (await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")).json()
    second = (await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")).json()

    assert first["id"] == second["id"]
    assert first == second

    history = await client.get(f"{api_prefix}/farms/{planted_farm['id']}/analysis")
    assert history.json()["total"] == 1


async def test_force_refresh_creates_a_new_run_with_identical_scores(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """A new run id, but deterministic content — same inputs, same numbers."""
    first = (await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")).json()
    second = (
        await client.post(
            f"{api_prefix}/farms/{planted_farm['id']}/analysis",
            params={"force_refresh": True},
        )
    ).json()

    assert first["id"] != second["id"]
    assert first["overall_health_score"] == second["overall_health_score"]
    assert first["water_risk"]["water_balance_mm"] == second["water_risk"]["water_balance_mm"]
    assert first["disease_risk"]["score"] == second["disease_risk"]["score"]
    assert (await client.get(f"{api_prefix}/farms/{planted_farm['id']}/analysis")).json()[
        "total"
    ] == 2


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


async def test_latest_returns_the_newest_run(
    client: AsyncClient, api_prefix: str, analyzed_farm: dict
) -> None:
    refreshed = (
        await client.post(
            f"{api_prefix}/farms/{analyzed_farm['id']}/analysis",
            params={"force_refresh": True},
        )
    ).json()

    latest = await client.get(f"{api_prefix}/farms/{analyzed_farm['id']}/analysis/latest")
    assert latest.json()["id"] == refreshed["id"]


async def test_run_permalink_resolves(
    client: AsyncClient, api_prefix: str, analyzed_farm: dict
) -> None:
    run = (await client.get(f"{api_prefix}/farms/{analyzed_farm['id']}/analysis/latest")).json()

    resp = await client.get(f"{api_prefix}/analysis/{run['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == run["id"]


async def test_history_returns_lightweight_summaries(
    client: AsyncClient, api_prefix: str, analyzed_farm: dict
) -> None:
    resp = await client.get(f"{api_prefix}/farms/{analyzed_farm['id']}/analysis")

    item = resp.json()["items"][0]
    assert {"id", "overall_health_score", "overall_band", "ai_mode"} <= set(item)
    assert "weather_risk" not in item, "history must stay lightweight"


@pytest.mark.parametrize("endpoint", PROJECTION_ENDPOINTS)
async def test_projection_without_analysis_is_no_analysis_yet(
    client: AsyncClient, api_prefix: str, planted_farm: dict, endpoint: str
) -> None:
    resp = await client.get(f"{api_prefix}/farms/{planted_farm['id']}/{endpoint}")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NO_ANALYSIS_YET"


@pytest.mark.parametrize("endpoint", PROJECTION_ENDPOINTS)
async def test_projection_matches_the_stored_run(
    client: AsyncClient, api_prefix: str, analyzed_farm: dict, endpoint: str
) -> None:
    """Projections must be reads of the stored run, never a recomputation."""
    run = (await client.get(f"{api_prefix}/farms/{analyzed_farm['id']}/analysis/latest")).json()
    resp = await client.get(f"{api_prefix}/farms/{analyzed_farm['id']}/{endpoint}")

    assert resp.status_code == 200
    body = resp.json()
    section = {
        "risks/weather": "weather_risk",
        "risks/water": "water_risk",
        "risks/disease": "disease_risk",
        "health": "crop_health",
        "advisories": "advisories",
        "recommendations/crops": "crop_recommendations",
        "recommendations/regenerative": "regenerative_recommendations",
    }[endpoint]

    if "items" in body:
        assert body["items"] == run[section][: len(body["items"])]
    else:
        assert body == run[section]


async def test_advisories_filter_by_priority(
    client: AsyncClient, api_prefix: str, analyzed_farm: dict
) -> None:
    everything = await client.get(f"{api_prefix}/farms/{analyzed_farm['id']}/advisories")
    priority = everything.json()["items"][0]["priority"]

    filtered = await client.get(
        f"{api_prefix}/farms/{analyzed_farm['id']}/advisories",
        params={"priority": priority},
    )

    assert filtered.status_code == 200
    assert all(a["priority"] == priority for a in filtered.json()["items"])


async def test_recommendation_limit_is_respected(
    client: AsyncClient, api_prefix: str, analyzed_farm: dict
) -> None:
    resp = await client.get(
        f"{api_prefix}/farms/{analyzed_farm['id']}/recommendations/crops",
        params={"limit": 2},
    )
    assert len(resp.json()["items"]) == 2


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


async def test_dashboard_without_analysis_is_an_empty_state_not_an_error(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    """The single most important dashboard behaviour: a new farm renders a
    'Run analysis' prompt rather than an error toast."""
    resp = await client.get(f"{api_prefix}/farms/{farm['id']}/dashboard")

    assert resp.status_code == 200
    body = resp.json()
    assert body["has_analysis"] is False
    assert body["analysis"] is None
    assert body["farm"]["id"] == farm["id"]
    # Weather is independent of analysis, so the panel still fills.
    assert body["current_weather"] is not None


async def test_dashboard_with_analysis_is_complete(
    client: AsyncClient, api_prefix: str, analyzed_farm: dict
) -> None:
    resp = await client.get(f"{api_prefix}/farms/{analyzed_farm['id']}/dashboard")

    body = resp.json()
    assert body["has_analysis"] is True
    assert body["analysis"]["overall_health_score"] >= 0
    assert len(body["crops"]) == 1
    assert body["data_freshness"]
    assert all(m["mode"] == "simulated" for m in body["data_freshness"])


async def test_farm_has_analysis_flag_flips(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    before = (await client.get(f"{api_prefix}/farms/{planted_farm['id']}")).json()
    assert before["has_analysis"] is False

    await client.post(f"{api_prefix}/farms/{planted_farm['id']}/analysis")

    after = (await client.get(f"{api_prefix}/farms/{planted_farm['id']}")).json()
    assert after["has_analysis"] is True


async def test_analysis_works_without_any_crop(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    """A farm registered with no crop must still analyse — the frontend should not
    have to gate the button."""
    resp = await client.post(f"{api_prefix}/farms/{farm['id']}/analysis")

    assert resp.status_code == 200
    run = resp.json()
    assert run["crop_health"]["growth_stage"] == "not_planted"
    assert run["crop_recommendations"]
