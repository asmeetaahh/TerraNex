"""Crop recommendations read the configured catalog, not the in-memory store.

The regression these cover: `_crop_recommendations` iterated `app.db.memory.store.crops`
directly, and `reference_service._ensure_catalog()` deliberately leaves that store empty
whenever a database is configured — the catalog lives in the `crops` table there. So
every database-backed deployment offered **zero** crop recommendations while
`GET /reference/crops` returned all twenty-six, and nothing failed loudly.

The fix routes the catalog through the same `database_enabled()` branch the reference
endpoint uses, and hands it to the engine on the context. These tests pin the property
that actually matters — the two endpoints agree about which crops exist.
"""

from httpx import AsyncClient

from app.db.seed import seed_crops, seed_demo_farms
from app.services.reference_service import catalog_crops


def test_the_catalog_reads_from_the_database(sqlite_db) -> None:
    seed_crops()

    crops = catalog_crops()

    assert len(crops) == 26
    assert {"maize", "wheat", "rice"} <= {crop.code for crop in crops}


def test_the_catalog_does_not_depend_on_the_memory_store(sqlite_db) -> None:
    """Asserted by emptying the store rather than by observing it empty.

    Whether the store happens to hold anything depends on what else ran in the process;
    what must be true regardless is that the catalog comes from the table. Clearing it
    first makes that unambiguous — before the fix this returned nothing.
    """
    from app.db.memory import store

    seed_crops()
    store.crops.clear()
    store.crops_by_code.clear()

    crops = catalog_crops()

    assert len(crops) == 26, "the catalog must come from the table, not the store"


async def test_recommendations_are_offered_on_the_database_path(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """The symptom. This returned `total: 0` while the reference catalog returned 26."""
    seed_crops()
    seed_demo_farms()

    farms = (await client.get(f"{api_prefix}/farms")).json()["items"]
    farm_id = farms[0]["id"]
    await client.post(f"{api_prefix}/farms/{farm_id}/analysis")

    body = (await client.get(f"{api_prefix}/farms/{farm_id}/recommendations/crops")).json()

    assert body["total"] > 0
    assert all(item["crop_code"] for item in body["items"])


async def test_both_endpoints_agree_the_catalog_exists(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """Cross-endpoint agreement is the property that would have caught this: a
    recommender offering nothing from a catalog of twenty-six is a contradiction."""
    seed_crops()
    seed_demo_farms()

    reference = (await client.get(f"{api_prefix}/reference/crops")).json()
    farms = (await client.get(f"{api_prefix}/farms")).json()["items"]
    farm_id = farms[0]["id"]
    await client.post(f"{api_prefix}/farms/{farm_id}/analysis")
    recommended = (await client.get(f"{api_prefix}/farms/{farm_id}/recommendations/crops")).json()

    assert reference["total"] == 26
    assert recommended["total"] > 0

    catalogued = {crop["code"] for crop in reference["items"]}
    offered = {item["crop_code"] for item in recommended["items"]}
    assert offered <= catalogued, "a crop was recommended that is not in the catalog"


async def test_a_recommended_crop_carries_its_persisted_details(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """The engine receives catalog rows through the context, so the name, category and
    water requirement it reports must be the persisted ones."""
    seed_crops()
    seed_demo_farms()

    farms = (await client.get(f"{api_prefix}/farms")).json()["items"]
    farm_id = farms[0]["id"]
    await client.post(f"{api_prefix}/farms/{farm_id}/analysis")

    reference = (await client.get(f"{api_prefix}/reference/crops")).json()["items"]
    by_code = {crop["code"]: crop for crop in reference}

    items = (await client.get(f"{api_prefix}/farms/{farm_id}/recommendations/crops")).json()[
        "items"
    ]

    for item in items:
        persisted = by_code[item["crop_code"]]
        assert item["crop_name"] == persisted["name"]
        assert item["category"] == persisted["category"]
        assert item["water_requirement_mm"] == persisted["water_need_mm_season"]
