"""Demo-farm seeding, gated on the existing SEED_DEMO_DATA setting.

Why this exists: a fresh process correctly serves an empty farm list, which leaves
every downstream panel with nothing to render. Seeding gives a new instance something
real to show, without touching the API contract.

The suite's autouse `clean_store` fixture empties the store before every test, so each
test seeds explicitly and starts from nothing.
"""

from httpx import AsyncClient

from app.db.memory import store
from app.db.seed import DEMO_FARMS, demo_id, seed_crops, seed_demo_farms

EXPECTED_SLUGS = {"nashik-vineyard-block", "nakuru-maize-field", "ames-soybean-quarter"}


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------


def test_seeding_creates_the_expected_farms() -> None:
    created = seed_demo_farms()

    assert created == len(DEMO_FARMS) == 3
    assert len(store.live_farms()) == 3
    assert {spec["slug"] for spec in DEMO_FARMS} == EXPECTED_SLUGS


def test_every_demo_farm_has_crops() -> None:
    seed_demo_farms()

    for spec in DEMO_FARMS:
        farm_id = demo_id("farm", spec["slug"])
        plantings = store.crops_for_farm(farm_id)
        assert plantings, f"{spec['slug']} has no plantings"
        assert len(plantings) == len(spec["crops"])


def test_each_farm_has_exactly_one_primary_crop() -> None:
    """`is_primary` selects the crop analysis centres on, so it must be unique."""
    seed_demo_farms()

    for spec in DEMO_FARMS:
        primaries = [c for c in store.crops_for_farm(demo_id("farm", spec["slug"])) if c.is_primary]
        assert len(primaries) == 1, f"{spec['slug']} has {len(primaries)} primary crops"


def test_demo_crops_exist_in_the_catalog() -> None:
    """A demo referencing a crop outside the catalog would silently seed a farm
    with no usable planting."""
    seed_crops()
    codes = set(store.crops_by_code)

    for spec in DEMO_FARMS:
        for planting in spec["crops"]:
            assert planting["crop_code"] in codes, f"unknown crop {planting['crop_code']}"


def test_harvest_dates_follow_planting_dates() -> None:
    seed_demo_farms()

    for planting in store.farm_crops.values():
        assert planting.expected_harvest_date > planting.planting_date


# --------------------------------------------------------------------------
# Determinism and idempotency
# --------------------------------------------------------------------------


def test_ids_are_derived_not_generated() -> None:
    """Ids come from the slug, so a demo farm keeps the same id across restarts and
    machines — which is what makes re-seeding a no-op."""
    seed_demo_farms()
    first = sorted(str(f.id) for f in store.live_farms())

    store.reset()
    seed_demo_farms()
    second = sorted(str(f.id) for f in store.live_farms())

    assert first == second


def test_repeated_seeding_does_not_duplicate() -> None:
    """Startup must be safe to repeat — reloads, workers, or a manual re-seed."""
    assert seed_demo_farms() == 3

    for _ in range(4):
        assert seed_demo_farms() == 0, "a repeat seed created farms"

    assert len(store.live_farms()) == 3
    assert len(store.farm_crops) == sum(len(s["crops"]) for s in DEMO_FARMS)


def test_reseeding_does_not_resurrect_a_deleted_farm() -> None:
    """A user who deletes a demo farm should not have it reappear on the next boot."""
    seed_demo_farms()
    target = demo_id("farm", "nakuru-maize-field")
    from datetime import UTC, datetime

    store.farms[target].deleted_at = datetime.now(UTC)

    assert seed_demo_farms() == 0
    assert store.get_farm(target) is None
    assert len(store.live_farms()) == 2


def test_coordinates_are_real_and_in_range() -> None:
    """Demo coordinates are real gazetteer locations, never generated."""
    seed_demo_farms()

    known = {
        "Nashik Block A": (19.9975, 73.7898),
        "Nakuru Highland Field": (-0.3031, 36.0800),
        "Ames North Quarter": (42.0308, -93.6319),
    }
    for farm in store.live_farms():
        assert -90 <= farm.latitude <= 90
        assert -180 <= farm.longitude <= 180
        assert (farm.latitude, farm.longitude) == known[farm.name]
        assert farm.country_code in {"IN", "KE", "US"}


# --------------------------------------------------------------------------
# The setting actually gates it
# --------------------------------------------------------------------------


async def test_lifespan_seeds_when_enabled(monkeypatch) -> None:
    """Exercises the real application lifespan, not a re-implementation of its guard."""
    from app.core.config import settings
    from app.main import app as fastapi_app
    from app.main import lifespan

    monkeypatch.setattr(settings, "SEED_DEMO_DATA", True)
    store.reset()

    async with lifespan(fastapi_app):
        assert len(store.live_farms()) == 3


async def test_lifespan_seeds_nothing_when_disabled(monkeypatch) -> None:
    """SEED_DEMO_DATA=false must leave a production instance empty."""
    from app.core.config import settings
    from app.main import app as fastapi_app
    from app.main import lifespan

    monkeypatch.setattr(settings, "SEED_DEMO_DATA", False)
    store.reset()

    async with lifespan(fastapi_app):
        assert store.live_farms() == []
        # The crop catalog is reference data and is seeded regardless.
        assert store.crops


async def test_repeated_lifespan_does_not_duplicate(monkeypatch) -> None:
    """Two startups in one process — a reload, or a worker respawn."""
    from app.core.config import settings
    from app.main import app as fastapi_app
    from app.main import lifespan

    monkeypatch.setattr(settings, "SEED_DEMO_DATA", True)
    store.reset()

    async with lifespan(fastapi_app):
        pass
    async with lifespan(fastapi_app):
        assert len(store.live_farms()) == 3
        assert len(store.farm_crops) == sum(len(s["crops"]) for s in DEMO_FARMS)


# --------------------------------------------------------------------------
# Through the API — unchanged contract, real data
# --------------------------------------------------------------------------


async def test_farms_endpoint_returns_seeded_farms(client: AsyncClient, api_prefix: str) -> None:
    """The gap this closes: `GET /farms` served an empty list on a fresh instance."""
    before = await client.get(f"{api_prefix}/farms")
    assert before.json()["total"] == 0

    seed_demo_farms()

    after = await client.get(f"{api_prefix}/farms")
    assert after.status_code == 200
    body = after.json()
    assert body["total"] == 3
    assert {f["name"] for f in body["items"]} == {
        "Nashik Block A",
        "Nakuru Highland Field",
        "Ames North Quarter",
    }
    # Derived fields are populated, so the frontend's selector has real content.
    assert all(f["crop_count"] > 0 for f in body["items"])
    assert all(f["has_analysis"] is False for f in body["items"])


async def test_seeded_farm_supports_the_whole_dashboard_flow(
    client: AsyncClient, api_prefix: str
) -> None:
    """A seeded farm must work end to end, not merely appear in a list."""
    seed_demo_farms()
    farm_id = str(demo_id("farm", "nakuru-maize-field"))

    dashboard = await client.get(f"{api_prefix}/farms/{farm_id}/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["has_analysis"] is False
    assert len(dashboard.json()["crops"]) == 2

    run = await client.post(f"{api_prefix}/farms/{farm_id}/analysis")
    assert run.status_code == 200
    assert run.json()["crop_health"]["growth_stage"] == "flowering"

    after = (await client.get(f"{api_prefix}/farms/{farm_id}/dashboard")).json()
    assert after["has_analysis"] is True
    assert after["analysis"]["advisories"]


async def test_seeded_farms_expose_environment_endpoints(
    client: AsyncClient, api_prefix: str
) -> None:
    """Real coordinates mean the environment endpoints resolve for every demo farm."""
    seed_demo_farms()

    for spec in DEMO_FARMS:
        farm_id = str(demo_id("farm", spec["slug"]))
        for endpoint in ("weather", "soil", "vegetation"):
            resp = await client.get(f"{api_prefix}/farms/{farm_id}/{endpoint}")
            assert resp.status_code == 200, f"{spec['slug']}/{endpoint}"
            # Provider defaults remain the deterministic simulator.
            assert resp.json()["meta"]["mode"] == "simulated"


async def test_seeded_farms_are_ordinary_farms(client: AsyncClient, api_prefix: str) -> None:
    """Demo farms are not special-cased anywhere — they update and delete normally."""
    seed_demo_farms()
    farm_id = str(demo_id("farm", "ames-soybean-quarter"))

    patched = await client.patch(f"{api_prefix}/farms/{farm_id}", json={"name": "Renamed"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renamed"

    deleted = await client.delete(f"{api_prefix}/farms/{farm_id}")
    assert deleted.status_code == 204
    assert (await client.get(f"{api_prefix}/farms")).json()["total"] == 2
