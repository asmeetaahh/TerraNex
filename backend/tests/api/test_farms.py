"""Farm and planting CRUD."""

from httpx import AsyncClient

from tests.conftest import DEMO_FARM


async def test_create_farm_returns_full_resource(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.post(f"{api_prefix}/farms", json=DEMO_FARM)

    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "North Field"
    assert body["id"]
    assert body["created_at"] and body["updated_at"]
    # Derived fields are present from the start so the UI never sees undefined.
    assert body["crop_count"] == 0
    assert body["has_analysis"] is False
    # Defaults from the schema, not silently omitted.
    assert body["irrigation_type"] == "rainfed"
    assert body["farming_practice"] == "conventional"


async def test_country_code_is_normalised(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.post(f"{api_prefix}/farms", json={**DEMO_FARM, "country_code": "ke"})
    assert resp.json()["country_code"] == "KE"


async def test_list_farms_uses_the_collection_envelope(
    client: AsyncClient, api_prefix: str
) -> None:
    for index in range(3):
        await client.post(f"{api_prefix}/farms", json={**DEMO_FARM, "name": f"Field {index}"})

    resp = await client.get(f"{api_prefix}/farms")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "total", "page", "page_size", "has_next"}
    assert body["total"] == 3
    assert body["has_next"] is False


async def test_list_farms_paginates(client: AsyncClient, api_prefix: str) -> None:
    for index in range(5):
        await client.post(f"{api_prefix}/farms", json={**DEMO_FARM, "name": f"Field {index}"})

    first = await client.get(f"{api_prefix}/farms", params={"page": 1, "page_size": 2})
    second = await client.get(f"{api_prefix}/farms", params={"page": 2, "page_size": 2})

    assert first.json()["total"] == 5
    assert len(first.json()["items"]) == 2
    assert first.json()["has_next"] is True
    assert second.json()["page"] == 2
    # Pages must not overlap.
    assert {i["id"] for i in first.json()["items"]}.isdisjoint(
        {i["id"] for i in second.json()["items"]}
    )


async def test_get_farm_round_trips(client: AsyncClient, api_prefix: str, farm: dict) -> None:
    resp = await client.get(f"{api_prefix}/farms/{farm['id']}")

    assert resp.status_code == 200
    assert resp.json()["id"] == farm["id"]
    assert resp.json()["name"] == farm["name"]


async def test_patch_updates_only_supplied_fields(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    resp = await client.patch(f"{api_prefix}/farms/{farm['id']}", json={"name": "South Field"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "South Field"
    # Untouched fields survive.
    assert body["latitude"] == farm["latitude"]
    assert body["area_hectares"] == farm["area_hectares"]


async def test_delete_is_soft_and_hides_the_farm(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    resp = await client.delete(f"{api_prefix}/farms/{farm['id']}")
    assert resp.status_code == 204
    assert not resp.content

    assert (await client.get(f"{api_prefix}/farms/{farm['id']}")).status_code == 404
    assert (await client.get(f"{api_prefix}/farms")).json()["total"] == 0


# --------------------------------------------------------------------------
# Plantings
# --------------------------------------------------------------------------


async def test_add_crop_embeds_the_catalog_entry(
    client: AsyncClient, api_prefix: str, farm: dict, maize_crop: dict
) -> None:
    """`FarmCrop` embeds `crop` so a card renders without a second request."""
    resp = await client.post(
        f"{api_prefix}/farms/{farm['id']}/crops",
        json={"crop_id": maize_crop["id"], "growth_stage": "vegetative"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["crop"]["code"] == "maize"
    assert body["crop"]["gdd_to_maturity"] == 1400
    assert body["farm_id"] == farm["id"]
    assert body["growth_stage"] == "vegetative"


async def test_crop_count_reflects_plantings(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    resp = await client.get(f"{api_prefix}/farms/{planted_farm['id']}")
    assert resp.json()["crop_count"] == 1


async def test_only_one_primary_crop_per_farm(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """`is_primary` selects the crop the analysis centres on, so it must be unique."""
    catalog = await client.get(f"{api_prefix}/reference/crops", params={"page_size": 200})
    beans = next(c for c in catalog.json()["items"] if c["code"] == "common_bean")

    await client.post(
        f"{api_prefix}/farms/{planted_farm['id']}/crops",
        json={"crop_id": beans["id"], "is_primary": True},
    )

    listing = await client.get(f"{api_prefix}/farms/{planted_farm['id']}/crops")
    primaries = [c for c in listing.json()["items"] if c["is_primary"]]
    assert len(primaries) == 1
    assert primaries[0]["crop"]["code"] == "common_bean"


async def test_update_crop_advances_growth_stage(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    listing = await client.get(f"{api_prefix}/farms/{planted_farm['id']}/crops")
    planting_id = listing.json()["items"][0]["id"]

    resp = await client.patch(
        f"{api_prefix}/farms/{planted_farm['id']}/crops/{planting_id}",
        json={"growth_stage": "maturity", "status": "growing"},
    )

    assert resp.status_code == 200
    assert resp.json()["growth_stage"] == "maturity"


async def test_delete_crop_removes_it(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    listing = await client.get(f"{api_prefix}/farms/{planted_farm['id']}/crops")
    planting_id = listing.json()["items"][0]["id"]

    resp = await client.delete(f"{api_prefix}/farms/{planted_farm['id']}/crops/{planting_id}")

    assert resp.status_code == 204
    assert (await client.get(f"{api_prefix}/farms/{planted_farm['id']}/crops")).json()["total"] == 0


async def test_planting_from_another_farm_is_not_reachable(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """A planting id must not be usable through a different farm's path."""
    other = await client.post(f"{api_prefix}/farms", json={**DEMO_FARM, "name": "Other"})
    listing = await client.get(f"{api_prefix}/farms/{planted_farm['id']}/crops")
    planting_id = listing.json()["items"][0]["id"]

    resp = await client.patch(
        f"{api_prefix}/farms/{other.json()['id']}/crops/{planting_id}",
        json={"growth_stage": "maturity"},
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CROP_NOT_FOUND"
