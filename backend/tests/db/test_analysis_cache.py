"""Reuse of a stored analysis, and the four ways a stored answer can be wrong.

A cache hit here means: the same farm was asked the same question, of the same code,
recently enough for the answer to still describe the weather. Each of those four is a
separate way to be stale, and each has a test.

The one that motivated the design is provenance. Two analyses over byte-identical
measurements are still different payloads when one ran on live data and the other on a
simulated fallback — `sources[].mode` and `degraded_sources` differ. Serving the cached
simulated run after the provider recovered would report `mode: "simulated"` for data
that is now live, which is precisely the confusion `DataMode` exists to prevent. So
provenance is part of the hash, and recovery is a natural miss rather than a special
case.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
import respx
from httpx import AsyncClient

from app.core.config import settings
from app.core.deps import demo_user
from app.db import analysis_repo
from app.db.seed import demo_id, seed_crops, seed_demo_farms
from app.engine.version import ENGINE_VERSION
from app.rules.registry import RULESET_VERSION
from app.services import analysis_service


@pytest.fixture
def farm_id(sqlite_db) -> str:
    seed_crops()
    seed_demo_farms()
    return str(demo_id("farm", "nakuru-maize-field"))


async def analyse(client: AsyncClient, api_prefix: str, farm_id: str, **params) -> dict:
    query = "".join(f"?{k}={v}" for k, v in params.items())
    response = await client.post(f"{api_prefix}/farms/{farm_id}/analysis{query}")
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------
# Hit and miss
# --------------------------------------------------------------------------


async def test_an_identical_request_reuses_the_stored_run(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    first = await analyse(client, api_prefix, farm_id)
    second = await analyse(client, api_prefix, farm_id)

    assert second["id"] == first["id"]
    assert analysis_repo.count_runs(UUID(farm_id)) == 1, "a hit must not write a second row"


async def test_force_refresh_always_recomputes(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    first = await analyse(client, api_prefix, farm_id)
    second = await analyse(client, api_prefix, farm_id, force_refresh="true")

    assert second["id"] != first["id"]
    assert analysis_repo.count_runs(UUID(farm_id)) == 2


async def test_a_refreshed_run_scores_the_same_on_unchanged_inputs(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    """Determinism, seen from outside: a new run id, identical numbers."""
    first = await analyse(client, api_prefix, farm_id)
    second = await analyse(client, api_prefix, farm_id, force_refresh="true")

    assert second["overall_health_score"] == first["overall_health_score"]
    assert second["water_risk"]["score"] == first["water_risk"]["score"]


async def test_a_hit_returns_the_stored_payload_verbatim(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    first = await analyse(client, api_prefix, farm_id)
    second = await analyse(client, api_prefix, farm_id)

    assert second == first


# --------------------------------------------------------------------------
# The four ways a stored answer goes stale
# --------------------------------------------------------------------------


async def test_a_run_past_its_ttl_is_not_reused(
    farm_id, client: AsyncClient, api_prefix: str, monkeypatch
) -> None:
    """Weather moves. A month-old answer is not an answer to today's question."""
    first = await analyse(client, api_prefix, farm_id)

    monkeypatch.setattr(settings, "ANALYSIS_CACHE_TTL_S", 1)
    cutoff = analysis_repo.cache_cutoff(datetime.now(UTC) + timedelta(seconds=10), 1)
    stale = analysis_repo.find_cached_run(
        UUID(farm_id),
        _hash_of(first, farm_id),
        engine_version=ENGINE_VERSION,
        ruleset_version=RULESET_VERSION,
        not_before=cutoff,
    )

    assert stale is None, "a run older than the TTL must not be found"


async def test_a_ttl_of_zero_disables_expiry(farm_id, client: AsyncClient, api_prefix: str) -> None:
    """`cache_cutoff` returning None is what "never expire" looks like."""
    assert analysis_repo.cache_cutoff(datetime.now(UTC), 0) is None


async def test_an_engine_version_change_invalidates_history(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    """A stored answer from different code is not this code's answer."""
    first = await analyse(client, api_prefix, farm_id)

    found = analysis_repo.find_cached_run(
        UUID(farm_id),
        _hash_of(first, farm_id),
        engine_version="9.9.9-different",
        ruleset_version=RULESET_VERSION,
    )

    assert found is None


async def test_a_ruleset_change_invalidates_history(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    """`RULESET_VERSION` is a content hash of the YAML, so a threshold cannot be edited
    without the runs it produced becoming unreusable."""
    first = await analyse(client, api_prefix, farm_id)

    found = analysis_repo.find_cached_run(
        UUID(farm_id),
        _hash_of(first, farm_id),
        engine_version=ENGINE_VERSION,
        ruleset_version="edited-ruleset",
    )

    assert found is None


async def test_changed_weather_produces_a_new_run(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    """The ordinary miss: different observations, different question."""
    first = await analyse(client, api_prefix, farm_id)

    found = analysis_repo.find_cached_run(
        UUID(farm_id),
        "0" * 64,
        engine_version=ENGINE_VERSION,
        ruleset_version=RULESET_VERSION,
    )

    assert found is None
    assert first["id"]


# --------------------------------------------------------------------------
# Provenance — the reason provenance is in the hash
# --------------------------------------------------------------------------


def _hash_of(run: dict, farm_id: str) -> str:
    """The inputs_hash the stored row carries, read back from the table."""
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import AnalysisRunORM

    with session_scope() as db:
        row = db.scalars(select(AnalysisRunORM).where(AnalysisRunORM.id == UUID(run["id"]))).first()
    assert row is not None
    return row.inputs_hash


async def test_a_provenance_change_produces_a_different_hash(farm_id) -> None:
    """Directly asserted on the hash, because this is the property the whole design
    rests on: identical measurements, different source mode, different question."""
    from dataclasses import replace

    from app.engine.context import inputs_hash
    from app.schemas.enums import GrowthStage
    from app.services.analysis_context import build_context
    from app.services.environment_service import gather_environment
    from app.services.farm_service import require_farm

    record = require_farm(UUID(farm_id), demo_user())
    env = await gather_environment(record)
    context = build_context(record, env, None, GrowthStage.not_planted)

    live = replace(context, provenance_key=("open-meteo:live", "soilgrids:live"))
    simulated = replace(context, provenance_key=("simulated:simulated",))

    assert inputs_hash(live) != inputs_hash(simulated)


async def test_a_degraded_run_is_not_served_after_recovery(
    farm_id, client: AsyncClient, api_prefix: str, monkeypatch
) -> None:
    """The scenario in full: soil provider down, then recovered.

    An integration test, so the miss is over-determined — the recovered run differs from
    the outage run in both its soil values and its provenance.
    `test_a_provenance_change_produces_a_different_hash` is the isolated proof that
    provenance alone is enough.
    """
    from app.providers.soil import SOILGRIDS_URL

    monkeypatch.setattr(settings, "SOIL_PROVIDER", "soilgrids")

    with respx.mock:
        respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(503))
        degraded = await analyse(client, api_prefix, farm_id)

    assert degraded["degraded_sources"], "the outage must be recorded"

    from app.providers.cache import get_cache

    get_cache("soil", settings.CACHE_TTL_SOIL_S).clear()

    with respx.mock:
        respx.get(SOILGRIDS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "properties": {
                        "layers": [
                            {
                                "name": "phh2o",
                                "unit_measures": {"d_factor": 10},
                                "depths": [{"label": "0-30cm", "values": {"mean": 64}}],
                            }
                        ]
                    }
                },
            )
        )
        recovered = await analyse(client, api_prefix, farm_id)

    assert recovered["id"] != degraded["id"], "a recovered provider must not serve the outage run"
    assert analysis_repo.count_runs(UUID(farm_id)) == 2


# --------------------------------------------------------------------------
# Ownership
# --------------------------------------------------------------------------


async def test_the_cache_is_scoped_to_one_farm(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """`inputs_hash` deliberately excludes the farm so a result is reproducible, but a
    run carries `farm_id` on itself and on every advisory — so another farm's run is the
    wrong data however identical the inputs were.
    """
    seed_crops()
    seed_demo_farms()

    farms = (await client.get(f"{api_prefix}/farms")).json()["items"]
    assert len(farms) >= 2

    first = await analyse(client, api_prefix, farms[0]["id"])
    second = await analyse(client, api_prefix, farms[1]["id"])

    assert second["id"] != first["id"]
    assert second["farm_id"] == farms[1]["id"]
    assert first["farm_id"] == farms[0]["id"]


async def test_a_lookup_scoped_to_another_owner_finds_nothing(farm_id, client, api_prefix) -> None:
    from uuid import uuid4

    first = await analyse(client, api_prefix, farm_id)

    found = analysis_repo.find_cached_run(
        UUID(farm_id),
        _hash_of(first, farm_id),
        engine_version=ENGINE_VERSION,
        ruleset_version=RULESET_VERSION,
        user_id=uuid4(),
    )

    assert found is None


# --------------------------------------------------------------------------
# Global locations
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "latitude", "longitude"),
    [
        ("ribeirao_preto", -21.1775, -47.8103),
        ("krasnodar", 45.0453, 38.9818),
        ("murmansk", 68.9678, 33.0992),
        ("nashik", 19.997, 73.791),
        ("zhengzhou", 34.7578, 113.6486),
        ("bloemfontein", -29.1211, 26.2140),
        ("aswan", 24.0908, 32.8994),
        ("bahir_dar", 11.5936, 37.3908),
        ("shiraz", 29.6103, 52.5311),
        ("al_ain", 24.1917, 55.7606),
        ("medan", 3.5833, 98.6667),
        ("riyadh", 24.6877, 46.7219),
    ],
)
async def test_analysis_persists_and_caches_at_every_brics_location(
    sqlite_db, client: AsyncClient, api_prefix: str, name: str, latitude: float, longitude: float
) -> None:
    """Persistence and caching must behave identically anywhere on Earth."""
    seed_crops()

    created = (
        await client.post(
            f"{api_prefix}/farms",
            json={"name": "Bengaluru Farm", "latitude": latitude, "longitude": longitude},
        )
    ).json()

    first = await analyse(client, api_prefix, created["id"])
    second = await analyse(client, api_prefix, created["id"])

    assert second["id"] == first["id"], f"{name} did not hit the cache"
    assert analysis_repo.count_runs(UUID(created["id"])) == 1


async def test_two_distant_farms_do_not_share_a_run(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    seed_crops()

    north = (
        await client.post(
            f"{api_prefix}/farms", json={"name": "A", "latitude": 68.9678, "longitude": 33.0992}
        )
    ).json()
    south = (
        await client.post(
            f"{api_prefix}/farms", json={"name": "B", "latitude": -29.1211, "longitude": 26.2140}
        )
    ).json()

    first = await analyse(client, api_prefix, north["id"])
    second = await analyse(client, api_prefix, south["id"])

    assert first["id"] != second["id"]


# --------------------------------------------------------------------------
# The in-memory path keeps its old behaviour
# --------------------------------------------------------------------------


async def test_without_a_database_the_previous_behaviour_is_unchanged(
    client: AsyncClient, api_prefix: str
) -> None:
    """No database means nothing durable to look in, so the store keeps returning the
    most recent run for the farm exactly as it always did."""
    seed_crops()
    seed_demo_farms()
    farm = str(demo_id("farm", "nakuru-maize-field"))

    first = await analyse(client, api_prefix, farm)
    second = await analyse(client, api_prefix, farm)

    assert second["id"] == first["id"]
    assert analysis_service._persisted() is False
