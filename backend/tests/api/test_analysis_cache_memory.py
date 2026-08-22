"""Cache semantics on the offline path.

`DATABASE_URL` unset is the default developer and test configuration, so this is the
behaviour most of the suite runs against and the behaviour a teammate sees locally. It
must match the persisted path clause for clause, or the two supported configurations
disagree about what an analysis is.

The case that matters is `test_changed_inputs_produce_a_new_run`. Before the store
learned to index runs by their inputs, `run_analysis` returned the most recent run for
the farm regardless of what had changed — so planting a different crop and re-analysing
returned the old crop's score, and only `force_refresh=true` escaped it. That is a wrong
answer, not a stale one.

No database is involved anywhere in this file; `sqlite_db` is deliberately absent.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.db.memory import RunMetadata, store
from app.services import analysis_service


@pytest.fixture(autouse=True)
def _offline() -> None:
    """Every test here asserts the no-database path, so prove we are on it."""
    assert analysis_service._persisted() is False


async def analyse(client: AsyncClient, api_prefix: str, farm_id: str, **params) -> dict:
    query = "".join(f"?{k}={v}" for k, v in params.items())
    response = await client.post(f"{api_prefix}/farms/{farm_id}/analysis{query}")
    assert response.status_code == 200, response.text
    return response.json()


async def plant(client: AsyncClient, api_prefix: str, farm_id: str, code: str) -> None:
    """Replace the farm's primary crop — a change that moves the analysis."""
    crops = (await client.get(f"{api_prefix}/reference/crops")).json()["items"]
    crop = next(c for c in crops if c["code"] == code)
    response = await client.post(
        f"{api_prefix}/farms/{farm_id}/crops",
        json={"crop_id": crop["id"], "growth_stage": "flowering", "is_primary": True},
    )
    assert response.status_code == 201, response.text


# --------------------------------------------------------------------------
# Hit and miss
# --------------------------------------------------------------------------


async def test_identical_inputs_hit_the_cache(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    first = await analyse(client, api_prefix, planted_farm["id"])
    second = await analyse(client, api_prefix, planted_farm["id"])

    assert second["id"] == first["id"]
    assert second == first
    assert len(store.analysis_runs) == 1, "a hit must not store a second run"


async def test_changed_inputs_produce_a_new_run(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """The regression. Planting a different crop changes the analysis, so the stored
    answer no longer answers the question being asked."""
    first = await analyse(client, api_prefix, planted_farm["id"])

    await plant(client, api_prefix, planted_farm["id"], "sorghum")
    second = await analyse(client, api_prefix, planted_farm["id"])

    assert second["id"] != first["id"], "a changed crop must not return the old run"
    assert len(store.analysis_runs) == 2


async def test_the_new_run_reflects_the_change(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """Not just a different id — a different answer. A new id carrying the old numbers
    would satisfy the test above while still being the stale result."""
    first = await analyse(client, api_prefix, planted_farm["id"])

    await plant(client, api_prefix, planted_farm["id"], "sorghum")
    second = await analyse(client, api_prefix, planted_farm["id"])

    assert second["crop_recommendations"][0] != first["crop_recommendations"][0] or (
        second["overall_health_score"] != first["overall_health_score"]
    )


async def test_the_cache_hits_again_once_inputs_settle(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """A miss must not disable the cache — the next identical request hits."""
    await analyse(client, api_prefix, planted_farm["id"])
    await plant(client, api_prefix, planted_farm["id"], "sorghum")

    second = await analyse(client, api_prefix, planted_farm["id"])
    third = await analyse(client, api_prefix, planted_farm["id"])

    assert third["id"] == second["id"]


# --------------------------------------------------------------------------
# force_refresh
# --------------------------------------------------------------------------


async def test_force_refresh_always_produces_a_new_run(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    first = await analyse(client, api_prefix, planted_farm["id"])
    second = await analyse(client, api_prefix, planted_farm["id"], force_refresh="true")

    assert second["id"] != first["id"]
    assert len(store.analysis_runs) == 2


async def test_force_refresh_keeps_the_scores_identical(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """Determinism seen from outside: a new run id over unchanged inputs, same numbers."""
    first = await analyse(client, api_prefix, planted_farm["id"])
    second = await analyse(client, api_prefix, planted_farm["id"], force_refresh="true")

    assert second["overall_health_score"] == first["overall_health_score"]
    assert second["water_risk"]["score"] == first["water_risk"]["score"]


async def test_a_forced_run_becomes_the_one_the_cache_serves(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    await analyse(client, api_prefix, planted_farm["id"])
    forced = await analyse(client, api_prefix, planted_farm["id"], force_refresh="true")

    assert (await analyse(client, api_prefix, planted_farm["id"]))["id"] == forced["id"]


# --------------------------------------------------------------------------
# TTL
# --------------------------------------------------------------------------


async def test_a_run_past_its_ttl_is_not_reused(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    """Weather moves. Aged by rewriting the stored run's timestamp, which is what a
    process that has been up for a while looks like."""
    first = await analyse(client, api_prefix, planted_farm["id"])
    monkeypatch.setattr(settings, "ANALYSIS_CACHE_TTL_S", 60)

    stored = store.analysis_runs[UUID(first["id"])]
    store.analysis_runs[stored.id] = stored.model_copy(
        update={"created_at": datetime.now(UTC) - timedelta(hours=2)}
    )

    second = await analyse(client, api_prefix, planted_farm["id"])

    assert second["id"] != first["id"], "a run older than the TTL must not be reused"


async def test_a_run_inside_its_ttl_is_reused(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "ANALYSIS_CACHE_TTL_S", 3600)

    first = await analyse(client, api_prefix, planted_farm["id"])
    second = await analyse(client, api_prefix, planted_farm["id"])

    assert second["id"] == first["id"]


async def test_a_ttl_of_zero_disables_expiry(
    client: AsyncClient, api_prefix: str, planted_farm: dict, monkeypatch
) -> None:
    """Zero means never expire, so even a very old run is still served."""
    monkeypatch.setattr(settings, "ANALYSIS_CACHE_TTL_S", 0)

    first = await analyse(client, api_prefix, planted_farm["id"])
    stored = store.analysis_runs[UUID(first["id"])]
    store.analysis_runs[stored.id] = stored.model_copy(
        update={"created_at": datetime.now(UTC) - timedelta(days=30)}
    )

    second = await analyse(client, api_prefix, planted_farm["id"])

    assert second["id"] == first["id"]


# --------------------------------------------------------------------------
# Hash, provenance and versions
# --------------------------------------------------------------------------


async def test_a_stored_run_records_its_inputs_hash(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """Without the metadata beside it, a run cannot be found by inputs at all."""
    run = await analyse(client, api_prefix, planted_farm["id"])
    metadata = store.run_metadata[UUID(run["id"])]

    assert len(metadata.inputs_hash) == 64
    assert metadata.engine_version
    assert metadata.ruleset_version


async def test_a_different_hash_is_a_miss(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """Asserted on the store directly, so the miss is attributable to the hash alone
    rather than to anything else that changed."""
    run = await analyse(client, api_prefix, planted_farm["id"])
    metadata = store.run_metadata[UUID(run["id"])]

    assert (
        store.find_cached_run(
            UUID(planted_farm["id"]),
            "0" * 64,
            engine_version=metadata.engine_version,
            ruleset_version=metadata.ruleset_version,
        )
        is None
    )


async def test_a_provenance_change_is_a_miss(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """Provenance lives inside the hash, so a run computed from simulated data is not
    served once the same measurements arrive live."""
    from dataclasses import replace

    from app.core.deps import demo_user
    from app.engine.context import inputs_hash
    from app.schemas.enums import GrowthStage
    from app.services.analysis_context import build_context
    from app.services.environment_service import gather_environment
    from app.services.farm_service import require_farm

    run = await analyse(client, api_prefix, planted_farm["id"])
    metadata = store.run_metadata[UUID(run["id"])]

    record = require_farm(UUID(planted_farm["id"]), demo_user())
    env = await gather_environment(record)
    context = build_context(record, env, None, GrowthStage.not_planted)
    live = replace(context, provenance_key=("open-meteo:live", "soilgrids:live"))

    assert (
        store.find_cached_run(
            UUID(planted_farm["id"]),
            inputs_hash(live),
            engine_version=metadata.engine_version,
            ruleset_version=metadata.ruleset_version,
        )
        is None
    )


async def test_an_engine_version_change_is_a_miss(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    run = await analyse(client, api_prefix, planted_farm["id"])
    metadata = store.run_metadata[UUID(run["id"])]

    assert (
        store.find_cached_run(
            UUID(planted_farm["id"]),
            metadata.inputs_hash,
            engine_version="9.9.9-different",
            ruleset_version=metadata.ruleset_version,
        )
        is None
    )


async def test_a_ruleset_change_is_a_miss(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    run = await analyse(client, api_prefix, planted_farm["id"])
    metadata = store.run_metadata[UUID(run["id"])]

    assert (
        store.find_cached_run(
            UUID(planted_farm["id"]),
            metadata.inputs_hash,
            engine_version=metadata.engine_version,
            ruleset_version="edited-ruleset",
        )
        is None
    )


async def test_a_run_without_metadata_is_not_reused(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """A run stored before metadata was recorded cannot be identified by inputs, so it
    is skipped rather than assumed to match."""
    run = await analyse(client, api_prefix, planted_farm["id"])
    metadata = store.run_metadata.pop(UUID(run["id"]))

    assert (
        store.find_cached_run(
            UUID(planted_farm["id"]),
            metadata.inputs_hash,
            engine_version=metadata.engine_version,
            ruleset_version=metadata.ruleset_version,
        )
        is None
    )


# --------------------------------------------------------------------------
# Farm isolation
# --------------------------------------------------------------------------


async def test_two_farms_do_not_share_a_run(
    client: AsyncClient, api_prefix: str, maize_crop: dict
) -> None:
    """`inputs_hash` excludes the farm so a result stays reproducible, but a run carries
    `farm_id` on itself and on every advisory — so another farm's run is wrong data."""
    made = []
    for name in ("Field A", "Field B"):
        farm = (
            await client.post(
                f"{api_prefix}/farms",
                json={"name": name, "latitude": 19.997, "longitude": 73.791},
            )
        ).json()
        made.append(farm["id"])

    first = await analyse(client, api_prefix, made[0])
    second = await analyse(client, api_prefix, made[1])

    assert first["id"] != second["id"]
    assert first["farm_id"] == made[0]
    assert second["farm_id"] == made[1]


async def test_a_lookup_for_another_farm_finds_nothing(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """Even with the identical hash, a different farm gets nothing."""
    run = await analyse(client, api_prefix, planted_farm["id"])
    metadata = store.run_metadata[UUID(run["id"])]

    assert (
        store.find_cached_run(
            uuid4(),
            metadata.inputs_hash,
            engine_version=metadata.engine_version,
            ruleset_version=metadata.ruleset_version,
        )
        is None
    )


# --------------------------------------------------------------------------
# Store housekeeping
# --------------------------------------------------------------------------


def test_reset_clears_the_metadata_too() -> None:
    """Leaving metadata behind would let a reset store answer for runs it no longer
    holds."""
    store.run_metadata[uuid4()] = RunMetadata("h", "e", "r")

    store.reset()

    assert store.run_metadata == {}
