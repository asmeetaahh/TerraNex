"""Reference data: crop catalog and simulated geocoding."""

from httpx import AsyncClient


async def test_catalog_returns_seeded_crops(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.get(f"{api_prefix}/reference/crops", params={"page_size": 200})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 20
    codes = {c["code"] for c in body["items"]}
    assert {"maize", "wheat", "rice", "sorghum", "potato"} <= codes


async def test_catalog_entries_carry_agronomic_constants(
    client: AsyncClient, api_prefix: str, maize_crop: dict
) -> None:
    """These constants feed the water balance and growth-stage maths, so they must
    actually be populated rather than null."""
    assert maize_crop["base_temp_c"] == 10.0
    assert maize_crop["optimal_temp_max_c"] == 32.0
    assert maize_crop["gdd_to_maturity"] == 1400
    assert maize_crop["water_need_mm_season"] == 600
    assert maize_crop["ph_min"] < maize_crop["ph_max"]
    assert maize_crop["preferred_textures"]
    assert maize_crop["common_diseases"]


async def test_crop_ids_are_stable_across_calls(client: AsyncClient, api_prefix: str) -> None:
    """Ids are derived from the crop code, so a frontend may safely cache them."""
    first = await client.get(f"{api_prefix}/reference/crops", params={"page_size": 200})
    second = await client.get(f"{api_prefix}/reference/crops", params={"page_size": 200})

    ids = {c["code"]: c["id"] for c in first.json()["items"]}
    assert all(ids[c["code"]] == c["id"] for c in second.json()["items"])


async def test_catalog_filters_by_category_and_season(client: AsyncClient, api_prefix: str) -> None:
    cereals = await client.get(
        f"{api_prefix}/reference/crops", params={"category": "cereal", "page_size": 200}
    )
    assert cereals.json()["total"] >= 5
    assert all(c["category"] == "cereal" for c in cereals.json()["items"])

    winter = await client.get(
        f"{api_prefix}/reference/crops", params={"season": "winter", "page_size": 200}
    )
    assert all(c["season"] == "winter" for c in winter.json()["items"])


async def test_catalog_rejects_an_unknown_category(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.get(f"{api_prefix}/reference/crops", params={"category": "spaceship"})
    assert resp.status_code == 422


async def test_geocoding_matches_the_gazetteer(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.get(f"{api_prefix}/reference/locations", params={"q": "Nairobi"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"]
    top = body["items"][0]
    assert top["name"] == "Nairobi"
    assert top["country_code"] == "KE"
    assert -2 < top["latitude"] < 0
    assert top["display_name"]


async def test_geocoding_is_marked_simulated(client: AsyncClient, api_prefix: str) -> None:
    """Even a gazetteer hit is not a live geocoder result."""
    resp = await client.get(f"{api_prefix}/reference/locations", params={"q": "Nairobi"})

    meta = resp.json()["meta"]
    assert meta["mode"] == "simulated"
    assert meta["source"] == "simulated"


async def test_unknown_place_returns_no_fabricated_coordinates(
    client: AsyncClient, api_prefix: str
) -> None:
    """The defect this phase closes.

    An unresolvable place must yield nothing. A synthesised coordinate is
    indistinguishable from a real one downstream, so every soil, weather and risk
    figure computed from it would be silently wrong for a place the user believes
    they selected.
    """
    resp = await client.get(f"{api_prefix}/reference/locations", params={"q": "Zzyzx Hollow"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_no_synthetic_location_marker_anywhere(client: AsyncClient, api_prefix: str) -> None:
    """The old fallback labelled invented points '(synthetic location)'. Nothing may
    reintroduce that, under any query."""
    for query in ["Zzyzx Hollow", "Qqqqqq", "Atlantis", "asdfghjkl"]:
        resp = await client.get(f"{api_prefix}/reference/locations", params={"q": query})
        assert resp.status_code == 200
        payload = resp.text.lower()
        assert "synthetic" not in payload
        assert resp.json()["items"] == []


async def test_geocoding_is_deterministic(client: AsyncClient, api_prefix: str) -> None:
    first = await client.get(f"{api_prefix}/reference/locations", params={"q": "Zzyzx Hollow"})
    second = await client.get(f"{api_prefix}/reference/locations", params={"q": "Zzyzx Hollow"})

    assert first.json()["items"] == second.json()["items"]


async def test_geocoding_is_case_insensitive(client: AsyncClient, api_prefix: str) -> None:
    lower = await client.get(f"{api_prefix}/reference/locations", params={"q": "nairobi"})
    upper = await client.get(f"{api_prefix}/reference/locations", params={"q": "NAIROBI"})

    assert lower.json()["items"] == upper.json()["items"]


async def test_geocoding_respects_limit(client: AsyncClient, api_prefix: str) -> None:
    resp = await client.get(f"{api_prefix}/reference/locations", params={"q": "a", "limit": 2})
    # 'a' is below min_length, so this is a validation error rather than a result.
    assert resp.status_code == 422
