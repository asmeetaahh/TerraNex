"""`limit` conformance, and what `total` is allowed to mean.

The contract advertises `limit` from 1 to 25 on both recommendation endpoints. The
analysis had committed to five: it ranked the catalog, kept the top five, and stored
those. Because the endpoints are projections of the stored run, five was a ceiling the
API could never see past — a client asking for ten received five, with nothing in the
payload to say why.

`total` compounded it. Slicing before paginating meant `total` counted the returned page,
so it equalled `page_size` at every limit and `has_next` was always false. A client had
no way to learn there was more ranking behind the window it asked for.

The fix stores the whole deterministic ranking and lets `paginate` do the slicing.
Ordering is untouched: the engine sorts every candidate before it slices, so a larger
limit reveals more of the same sequence rather than a different one — which is what
`test_the_ordering_is_a_prefix_at_every_limit` pins.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

CROPS = "crops"
REGEN = "regenerative"


@pytest.fixture
async def analysed_farm(client: AsyncClient, api_prefix: str, farm: dict) -> dict:
    """A farm with one completed analysis, so the projections have something to read."""
    crops = (await client.get(f"{api_prefix}/reference/crops")).json()["items"]
    await client.post(
        f"{api_prefix}/farms/{farm['id']}/crops",
        json={"crop_id": crops[0]["id"], "growth_stage": "flowering", "is_primary": True},
    )
    resp = await client.post(f"{api_prefix}/farms/{farm['id']}/analysis")
    assert resp.status_code == 200, resp.text
    return farm


async def fetch(client: AsyncClient, api_prefix: str, farm_id: str, kind: str, limit=None):
    query = f"?limit={limit}" if limit is not None else ""
    resp = await client.get(f"{api_prefix}/farms/{farm_id}/recommendations/{kind}{query}")
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------
# The stored ranking
# --------------------------------------------------------------------------


async def test_the_analysis_stores_the_whole_ranking(
    client: AsyncClient, api_prefix: str, analysed_farm: dict
) -> None:
    """Not the top five. Everything the endpoints could ever be asked for has to already
    be in the stored run, because they never recompute."""
    run = (await client.get(f"{api_prefix}/farms/{analysed_farm['id']}/analysis/latest")).json()
    catalog = (await client.get(f"{api_prefix}/reference/crops")).json()["total"]

    assert len(run["crop_recommendations"]) == catalog, "every rankable crop is stored"
    assert len(run["regenerative_recommendations"]) > 5, "more practices than the old ceiling"


@pytest.mark.parametrize("kind", [CROPS, REGEN])
async def test_the_ranking_survives_a_restart(
    sqlite_db, client: AsyncClient, api_prefix: str, kind: str
) -> None:
    """The full ranking has to round-trip through storage, not just the first page.

    `sqlite_db` comes first so the farm is created while a database is configured —
    building it on the memory path and then switching backends would 404, which is a
    fixture-ordering mistake rather than a persistence finding.
    """
    from app.db.memory import store
    from app.db.seed import seed_crops

    seed_crops()
    created = (
        await client.post(
            f"{api_prefix}/farms",
            json={"name": "Ranking", "latitude": -1.29, "longitude": 36.82},
        )
    ).json()
    crops = (await client.get(f"{api_prefix}/reference/crops")).json()["items"]
    await client.post(
        f"{api_prefix}/farms/{created['id']}/crops",
        json={"crop_id": crops[0]["id"], "growth_stage": "flowering", "is_primary": True},
    )
    await client.post(f"{api_prefix}/farms/{created['id']}/analysis")

    key = "crop_code" if kind == CROPS else "practice_code"
    before = await fetch(client, api_prefix, created["id"], kind, 25)

    store.reset()  # a restart: process-local state gone, the database is not

    after = await fetch(client, api_prefix, created["id"], kind, 25)

    assert after["total"] == before["total"]
    assert after["total"] > 5, "the whole ranking persisted, not the old ceiling"
    assert [i[key] for i in after["items"]] == [i[key] for i in before["items"]]


# --------------------------------------------------------------------------
# limit conformance
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [CROPS, REGEN])
async def test_a_small_limit_returns_that_many_but_total_reports_the_ranking(
    client: AsyncClient, api_prefix: str, analysed_farm: dict, kind: str
) -> None:
    """The regression. `total` must describe the ranking, not the page."""
    body = await fetch(client, api_prefix, analysed_farm["id"], kind, 3)

    assert len(body["items"]) == 3
    assert body["page_size"] == 3
    assert body["total"] > 3, "total counts the whole ranking, not the returned page"
    assert body["has_next"] is True


async def test_ten_can_actually_be_returned(
    client: AsyncClient, api_prefix: str, analysed_farm: dict
) -> None:
    """The headline defect: the contract permits 25, the analysis stored 5, and a request
    for 10 quietly received 5."""
    body = await fetch(client, api_prefix, analysed_farm["id"], CROPS, 10)

    assert len(body["items"]) == 10


async def test_the_contract_maximum_returns_everything_available(
    client: AsyncClient, api_prefix: str, analysed_farm: dict
) -> None:
    body = await fetch(client, api_prefix, analysed_farm["id"], CROPS, 25)

    assert len(body["items"]) == min(25, body["total"])


@pytest.mark.parametrize("kind", [CROPS, REGEN])
async def test_a_limit_beyond_the_ranking_returns_what_exists(
    client: AsyncClient, api_prefix: str, analysed_farm: dict, kind: str
) -> None:
    """Asking for more than exists is not an error, and must not pad the list."""
    body = await fetch(client, api_prefix, analysed_farm["id"], kind, 25)

    assert len(body["items"]) == min(25, body["total"])
    assert body["has_next"] is (body["total"] > 25)


@pytest.mark.parametrize("kind", [CROPS, REGEN])
async def test_the_default_limit_is_unchanged(
    client: AsyncClient, api_prefix: str, analysed_farm: dict, kind: str
) -> None:
    """The published default is 5. Storing more must not change what an unparameterised
    request returns — only what a larger one can reach."""
    body = await fetch(client, api_prefix, analysed_farm["id"], kind)

    assert len(body["items"]) == 5
    assert body["page_size"] == 5


# --------------------------------------------------------------------------
# Determinism and ordering
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [CROPS, REGEN])
async def test_the_ordering_is_a_prefix_at_every_limit(
    client: AsyncClient, api_prefix: str, analysed_farm: dict, kind: str
) -> None:
    """A larger limit reveals more of the same sequence, never a different one.

    The engine ranks every candidate and slices last, so this is a property of the sort
    rather than of the limit — which is exactly why storing the full ranking could not
    change any score or rank.
    """
    key = "crop_code" if kind == CROPS else "practice_code"
    full = await fetch(client, api_prefix, analysed_farm["id"], kind, 25)
    codes = [item[key] for item in full["items"]]

    for limit in (1, 3, 5, 7):
        page = await fetch(client, api_prefix, analysed_farm["id"], kind, limit)
        assert [item[key] for item in page["items"]] == codes[:limit]


@pytest.mark.parametrize("kind", [CROPS, REGEN])
async def test_ranks_are_contiguous_from_one(
    client: AsyncClient, api_prefix: str, analysed_farm: dict, kind: str
) -> None:
    body = await fetch(client, api_prefix, analysed_farm["id"], kind, 25)

    assert [item["rank"] for item in body["items"]] == list(range(1, len(body["items"]) + 1))


async def test_crop_scores_never_increase_down_the_ranking(
    client: AsyncClient, api_prefix: str, analysed_farm: dict
) -> None:
    body = await fetch(client, api_prefix, analysed_farm["id"], CROPS, 25)
    scores = [item["suitability_score"] for item in body["items"]]

    assert scores == sorted(scores, reverse=True)


async def test_practice_relevance_never_increases_down_the_ranking(
    client: AsyncClient, api_prefix: str, analysed_farm: dict
) -> None:
    body = await fetch(client, api_prefix, analysed_farm["id"], REGEN, 25)
    scores = [item["relevance_score"] for item in body["items"]]

    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("kind", [CROPS, REGEN])
async def test_repeated_requests_are_identical(
    client: AsyncClient, api_prefix: str, analysed_farm: dict, kind: str
) -> None:
    """Determinism is the property the whole engine is built on."""
    first = await fetch(client, api_prefix, analysed_farm["id"], kind, 25)
    second = await fetch(client, api_prefix, analysed_farm["id"], kind, 25)

    assert first == second


# --------------------------------------------------------------------------
# expected_yield_note
# --------------------------------------------------------------------------


async def test_expected_yield_note_is_null_because_the_catalog_has_no_yield_data(
    client: AsyncClient, api_prefix: str, analysed_farm: dict
) -> None:
    """Null on purpose, and this test is the record of why.

    The crop catalog carries fifteen agronomic fields and not one is a yield — no t/ha,
    no yield potential, no historical record. Anything written here would have to be
    derived from `gdd_to_maturity` or `water_need_mm_season`, neither of which determines
    yield: that depends on variety, management, fertility and season, none of which
    TerraNex holds.

    A plausible-looking figure is worse than an absent one, because a farmer would plan
    against it. If this test ever fails, the catalog gained real yield data — check that
    it did, rather than making the assertion pass.
    """
    body = await fetch(client, api_prefix, analysed_farm["id"], CROPS, 25)

    assert all(item["expected_yield_note"] is None for item in body["items"])


def test_the_catalog_fixture_carries_no_yield_field() -> None:
    """The premise of the test above, asserted at its source."""
    import json
    from pathlib import Path

    import app

    fixture = Path(app.__file__).parent / "db" / "fixtures" / "crops.json"
    rows = json.loads(fixture.read_text())
    rows = rows if isinstance(rows, list) else rows.get("crops", rows)

    keys = set(rows[0])
    assert keys, "the fixture is expected to define crop fields"
    assert not [k for k in keys if "yield" in k.lower()], (
        "the catalog gained a yield field — expected_yield_note may now be populatable "
        "from real data"
    )
