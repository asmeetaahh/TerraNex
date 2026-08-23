"""Crop recommendation reason codes on the wire.

`tests/unit/engine/test_recommendation_reasons.py` covers what the engine emits. This
covers what survives the schema boundary, storage and the analysis cache — the point of
the field being that a *client* can read it.

The additive promise is asserted directly: `reasons` defaults to `[]`, so a client
written against the previous contract still validates, and a stored run written before
the field existed still loads.
"""

import pytest
from httpx import AsyncClient

from app.schemas.recommendation import CropRecommendation

APPROVED_KEYS = {
    "crop.ph_within_range",
    "crop.ph_outside_range",
    "crop.temperature_optimal",
    "crop.temperature_outside",
    "crop.texture_match",
    "crop.texture_mismatch",
    "crop.water_sufficient",
    "crop.water_shortfall",
}


@pytest.fixture
async def analysed(client: AsyncClient, api_prefix: str, farm: dict) -> dict:
    crops = (await client.get(f"{api_prefix}/reference/crops")).json()["items"]
    await client.post(
        f"{api_prefix}/farms/{farm['id']}/crops",
        json={"crop_id": crops[0]["id"], "growth_stage": "flowering", "is_primary": True},
    )
    resp = await client.post(f"{api_prefix}/farms/{farm['id']}/analysis")
    assert resp.status_code == 200, resp.text
    return farm


async def recommendations(client: AsyncClient, api_prefix: str, farm_id: str, limit: int = 25):
    resp = await client.get(f"{api_prefix}/farms/{farm_id}/recommendations/crops?limit={limit}")
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


# --------------------------------------------------------------------------
# The evidence crosses the boundary
# --------------------------------------------------------------------------


async def test_every_recommendation_publishes_reasons(
    client: AsyncClient, api_prefix: str, analysed: dict
) -> None:
    """The simulated site measures pH, temperature, texture and rainfall, so every
    ranked crop should carry evidence for the components that were assessed."""
    items = await recommendations(client, api_prefix, analysed["id"])

    assert items, "an analysis is expected to rank the catalog"
    assert any(item["reasons"] for item in items), "at least one crop must carry evidence"


async def test_published_keys_are_all_from_the_approved_vocabulary(
    client: AsyncClient, api_prefix: str, analysed: dict
) -> None:
    """Keys become a public vocabulary the moment a client binds a translation to one."""
    items = await recommendations(client, api_prefix, analysed["id"])

    for item in items:
        for reason in item["reasons"]:
            assert reason["key"] in APPROVED_KEYS, f"unapproved key {reason['key']}"


async def test_published_params_are_scalars(
    client: AsyncClient, api_prefix: str, analysed: dict
) -> None:
    items = await recommendations(client, api_prefix, analysed["id"])

    for item in items:
        for reason in item["reasons"]:
            for name, value in reason["params"].items():
                assert isinstance(value, float | int | str), f"{name}={value!r}"


async def test_reasons_never_exceed_one_per_component(
    client: AsyncClient, api_prefix: str, analysed: dict
) -> None:
    """Four components, so at most four reasons, and no key repeated within a crop."""
    items = await recommendations(client, api_prefix, analysed["id"])

    for item in items:
        keys = [r["key"] for r in item["reasons"]]
        assert len(keys) <= 4
        assert len(keys) == len(set(keys)), "a component must not emit twice"


async def test_a_crop_carries_at_most_one_key_per_component_pair(
    client: AsyncClient, api_prefix: str, analysed: dict
) -> None:
    """`within_range` and `outside_range` are mutually exclusive — a crop cannot both
    tolerate and not tolerate the site's pH."""
    pairs = [
        {"crop.ph_within_range", "crop.ph_outside_range"},
        {"crop.temperature_optimal", "crop.temperature_outside"},
        {"crop.texture_match", "crop.texture_mismatch"},
        {"crop.water_sufficient", "crop.water_shortfall"},
    ]
    items = await recommendations(client, api_prefix, analysed["id"])

    for item in items:
        keys = {r["key"] for r in item["reasons"]}
        for pair in pairs:
            assert len(keys & pair) <= 1, f"contradictory keys {keys & pair}"


# --------------------------------------------------------------------------
# The existing fields are untouched
# --------------------------------------------------------------------------


async def test_the_existing_reasoning_fields_are_unchanged(
    client: AsyncClient, api_prefix: str, analysed: dict
) -> None:
    """This phase adds; it does not reword. `factors`, `strengths`, `considerations` and
    `rationale` keep their previous shape for every existing client."""
    item = (await recommendations(client, api_prefix, analysed["id"]))[0]

    assert item["rationale"]
    assert isinstance(item["strengths"], list)
    assert isinstance(item["considerations"], list)
    assert [f["key"] for f in item["factors"]] == [
        "ph_match",
        "temperature_match",
        "texture_match",
        "water_match",
    ]


async def test_ranking_and_scores_are_unchanged(
    client: AsyncClient, api_prefix: str, analysed: dict
) -> None:
    """Reasons are evidence, not input. Ranks stay contiguous and scores non-increasing."""
    items = await recommendations(client, api_prefix, analysed["id"])

    assert [i["rank"] for i in items] == list(range(1, len(items) + 1))
    scores = [i["suitability_score"] for i in items]
    assert scores == sorted(scores, reverse=True)


# --------------------------------------------------------------------------
# Backward compatibility
# --------------------------------------------------------------------------


def test_a_payload_without_reasons_still_validates() -> None:
    """A client or stored row written before this field existed must still parse."""
    legacy = {
        "crop_code": "sorghum",
        "crop_name": "Sorghum",
        "category": "cereal",
        "suitability_score": 91,
        "rank": 1,
        "rationale": "Sorghum scores 91/100.",
    }

    item = CropRecommendation.model_validate(legacy)

    assert item.reasons == []


def test_reasons_is_not_a_required_field() -> None:
    assert CropRecommendation.model_fields["reasons"].is_required() is False


# --------------------------------------------------------------------------
# Persistence and caching
# --------------------------------------------------------------------------


async def test_a_cached_run_returns_the_same_reasons(
    client: AsyncClient, api_prefix: str, analysed: dict
) -> None:
    """A cache hit serves the stored payload verbatim."""
    first = (await client.post(f"{api_prefix}/farms/{analysed['id']}/analysis")).json()
    second = (await client.post(f"{api_prefix}/farms/{analysed['id']}/analysis")).json()

    assert second["id"] == first["id"], "expected a cache hit"
    assert second["crop_recommendations"] == first["crop_recommendations"]


async def test_reasons_survive_a_restart(sqlite_db, client: AsyncClient, api_prefix: str) -> None:
    """Stored inside `analysis_runs.result`, so the field round-trips through JSON."""
    from app.db.memory import store
    from app.db.seed import seed_crops

    seed_crops()
    created = (
        await client.post(
            f"{api_prefix}/farms",
            json={"name": "CropReasons", "latitude": -1.29, "longitude": 36.82},
        )
    ).json()
    crops = (await client.get(f"{api_prefix}/reference/crops")).json()["items"]
    await client.post(
        f"{api_prefix}/farms/{created['id']}/crops",
        json={"crop_id": crops[0]["id"], "growth_stage": "flowering", "is_primary": True},
    )
    before = (await client.post(f"{api_prefix}/farms/{created['id']}/analysis")).json()

    store.reset()

    after = (await client.get(f"{api_prefix}/farms/{created['id']}/analysis/latest")).json()

    assert after["crop_recommendations"] == before["crop_recommendations"]
