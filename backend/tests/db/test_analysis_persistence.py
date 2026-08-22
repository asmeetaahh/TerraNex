"""Analysis runs, persisted.

Runs lived in a process-local dict, so a restart emptied every dashboard, risk panel
and advisory list — silently, because nothing errored and the panels simply went blank.
These cover the round trip and the property that actually matters:
`test_a_run_survives_a_restart`.

The payload is stored as JSON and rebuilt through `AnalysisRun.model_validate`, so the
fidelity tests below are not ceremony: a section lost in serialisation would surface as
an empty dashboard card rather than an exception.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.core.deps import demo_user
from app.db import analysis_repo
from app.db.memory import store
from app.db.seed import demo_id, seed_crops, seed_demo_farms
from app.engine.version import ENGINE_VERSION
from app.rules.registry import RULESET_VERSION


async def analyse(client: AsyncClient, api_prefix: str, farm_id: str) -> dict:
    response = await client.post(f"{api_prefix}/farms/{farm_id}/analysis")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def farm_id(sqlite_db) -> str:
    seed_crops()
    seed_demo_farms()
    return str(demo_id("farm", "nakuru-maize-field"))


# --------------------------------------------------------------------------
# The row lands in the database
# --------------------------------------------------------------------------


async def test_an_analysis_writes_a_row(farm_id, client: AsyncClient, api_prefix: str) -> None:
    await analyse(client, api_prefix, farm_id)

    from uuid import UUID

    assert analysis_repo.count_runs(UUID(farm_id)) == 1


async def test_nothing_is_written_to_the_memory_store(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    """The defect in one assertion: with a database configured the store must stay
    empty, or a restart still loses everything."""
    await analyse(client, api_prefix, farm_id)

    assert store.analysis_runs == {}


async def test_a_run_survives_a_restart(farm_id, client: AsyncClient, api_prefix: str) -> None:
    """The symptom. Clearing the store is what a fresh process looks like."""
    created = await analyse(client, api_prefix, farm_id)

    store.reset()

    latest = (await client.get(f"{api_prefix}/farms/{farm_id}/analysis/latest")).json()

    assert latest["id"] == created["id"]
    assert latest["overall_health_score"] == created["overall_health_score"]


async def test_the_dashboard_still_reports_an_analysis_after_a_restart(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    await analyse(client, api_prefix, farm_id)
    store.reset()

    dashboard = (await client.get(f"{api_prefix}/farms/{farm_id}/dashboard")).json()

    assert dashboard["has_analysis"] is True
    assert dashboard["analysis"] is not None


async def test_the_farm_reports_has_analysis_after_a_restart(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    """`has_analysis` read only the store, so a persisted farm rendered as one that had
    never been analysed."""
    await analyse(client, api_prefix, farm_id)
    store.reset()

    farm = (await client.get(f"{api_prefix}/farms/{farm_id}")).json()

    assert farm["has_analysis"] is True


# --------------------------------------------------------------------------
# Payload fidelity
# --------------------------------------------------------------------------


async def test_the_whole_payload_round_trips(farm_id, client: AsyncClient, api_prefix: str) -> None:
    created = await analyse(client, api_prefix, farm_id)
    store.reset()

    restored = (await client.get(f"{api_prefix}/farms/{farm_id}/analysis/latest")).json()

    assert restored == created


@pytest.mark.parametrize(
    "section",
    [
        "weather_risk",
        "water_risk",
        "disease_risk",
        "crop_health",
        "soil_assessment",
        "advisories",
        "crop_recommendations",
        "regenerative_recommendations",
        "sources",
        "factors",
    ],
)
async def test_every_nested_section_survives_storage(
    farm_id, client: AsyncClient, api_prefix: str, section: str
) -> None:
    """A section lost in serialisation shows up as an empty dashboard card, not an
    error, which is why each one is named."""
    created = await analyse(client, api_prefix, farm_id)
    store.reset()

    restored = (await client.get(f"{api_prefix}/farms/{farm_id}/analysis/latest")).json()

    assert restored[section] == created[section]
    assert restored[section], f"{section} came back empty"


async def test_provenance_survives_storage(farm_id, client: AsyncClient, api_prefix: str) -> None:
    """`meta.mode` is the whole honesty mechanism; a stored run must not lose it."""
    created = await analyse(client, api_prefix, farm_id)
    store.reset()

    restored = (await client.get(f"{api_prefix}/farms/{farm_id}/analysis/latest")).json()

    assert [s["mode"] for s in restored["sources"]] == [s["mode"] for s in created["sources"]]
    assert restored["degraded_sources"] == created["degraded_sources"]
    assert restored["ai_mode"] == created["ai_mode"]


# --------------------------------------------------------------------------
# History and retrieval
# --------------------------------------------------------------------------


async def test_history_lists_stored_runs_newest_first(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    first = await analyse(client, api_prefix, farm_id)
    second = (await client.post(f"{api_prefix}/farms/{farm_id}/analysis?force_refresh=true")).json()

    store.reset()
    history = (await client.get(f"{api_prefix}/farms/{farm_id}/analysis")).json()

    assert history["total"] == 2
    assert [item["id"] for item in history["items"]] == [second["id"], first["id"]]


async def test_a_run_resolves_by_its_own_id_after_a_restart(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    created = await analyse(client, api_prefix, farm_id)
    store.reset()

    restored = (await client.get(f"{api_prefix}/analysis/{created['id']}")).json()

    assert restored["id"] == created["id"]


async def test_an_unknown_run_id_is_not_found(
    farm_id, client: AsyncClient, api_prefix: str
) -> None:
    response = await client.get(f"{api_prefix}/analysis/{uuid4()}")

    assert response.status_code == 404


# --------------------------------------------------------------------------
# Reproducibility columns
# --------------------------------------------------------------------------


async def test_a_stored_run_records_the_code_that_produced_it(sqlite_db) -> None:
    """Version columns are what let a cached run be rejected after an engine or
    ruleset change, so they must actually be written."""
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import AnalysisRunORM

    seed_crops()
    seed_demo_farms()

    from app.services import analysis_service

    farm = demo_id("farm", "nakuru-maize-field")
    await analysis_service.run_analysis(farm, user=demo_user())

    with session_scope() as db:
        row = db.scalars(select(AnalysisRunORM)).first()

    assert row is not None
    assert row.engine_version == ENGINE_VERSION
    assert row.ruleset_version == RULESET_VERSION
    assert len(row.inputs_hash) == 64
    assert row.user_id == demo_user().id


def test_the_cutoff_helper_respects_a_disabled_ttl() -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)

    assert analysis_repo.cache_cutoff(now, 0) is None
    assert analysis_repo.cache_cutoff(now, -1) is None
    assert analysis_repo.cache_cutoff(now, 3600) == now - timedelta(seconds=3600)


# --------------------------------------------------------------------------
# PostgreSQL only
# --------------------------------------------------------------------------


@pytest.mark.postgres
async def test_the_farm_foreign_key_cascades_in_postgres(postgres_db) -> None:
    """SQLite runs with foreign keys off, so this is the only place the cascade — and
    therefore the guarantee that deleting a farm does not orphan its runs — is proven.
    """
    from uuid import UUID

    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import AnalysisRunORM, FarmORM
    from app.services import analysis_service

    seed_crops()
    seed_demo_farms()
    farm = demo_id("farm", "nakuru-maize-field")

    await analysis_service.run_analysis(farm, user=demo_user())
    assert analysis_repo.count_runs(UUID(str(farm))) == 1

    with session_scope() as db:
        db.delete(db.get(FarmORM, farm))

    with session_scope() as db:
        assert db.scalars(select(AnalysisRunORM)).all() == []
