"""Soil profiles, persisted.

Soil was cached in a process-local `TTLCache` with a thirty-day TTL — a durability the
implementation could not deliver, because the cache dies with the process. Every restart
refetched every farm's soil from ISRIC, and a provider outage at that moment degraded
the whole estate to *simulated* values until it recovered.

**The load-bearing assertions here count HTTP calls, not response fields.** The point of
the table is that a stored profile *prevents an outbound request*; a test that only
checked the response body would pass just as happily against the old cache-only code,
because the values are identical either way. `respx` is set to assert on call counts so
the saving is proven rather than assumed.

The four states that matter, each with its own test:

* fresh stored profile          → served, **no** provider call, `mode: cached`
* expired stored profile        → refetched
* provider down, fresh profile  → served from storage (the provider is never asked)
* provider down, no profile     → simulated fallback, exactly as before
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
import respx

from app.core.config import settings
from app.core.deps import demo_user
from app.db import soil_repo
from app.db.memory import store
from app.db.seed import demo_id, seed_crops, seed_demo_farms
from app.providers.cache import clear_all_caches
from app.providers.soil import SOILGRIDS_SOURCE, SOILGRIDS_URL
from app.schemas.common import DataMode
from app.services import environment_service
from app.services.farm_service import require_farm


def layer(name: str, mean: object, d_factor: float | None = None) -> dict:
    body: dict = {"name": name, "depths": [{"label": "0-30cm", "values": {"mean": mean}}]}
    if d_factor is not None:
        body["unit_measures"] = {"d_factor": d_factor}
    return body


#: A realistic loam: pH 6.4, 1.8% carbon, 40/40/20 sand/silt/clay.
LOAM = {
    "properties": {
        "layers": [
            layer("phh2o", 64, 10),
            layer("soc", 180, 100),
            layer("nitrogen", 150, 100),
            layer("cec", 152, 10),
            layer("bdod", 132, 100),
            layer("sand", 400, 10),
            layer("silt", 400, 10),
            layer("clay", 200, 10),
        ]
    }
}

#: A visibly different soil, so a refetch can be told from a stored answer by value.
SANDY = {
    "properties": {
        "layers": [
            layer("phh2o", 78, 10),
            layer("sand", 850, 10),
            layer("silt", 100, 10),
            layer("clay", 50, 10),
        ]
    }
}


@pytest.fixture(autouse=True)
def _soilgrids(monkeypatch, sqlite_db):
    """Opt into the real provider; conftest pins the suite to `simulated`.

    The TTL cache is cleared around every test, because a warm cache would answer before
    the database is ever consulted and silently make every assertion below vacuous.
    """
    monkeypatch.setattr(settings, "SOIL_PROVIDER", "soilgrids")
    clear_all_caches()
    seed_crops()
    seed_demo_farms()
    yield
    clear_all_caches()


@pytest.fixture
def farm():
    return require_farm(demo_id("farm", "nakuru-maize-field"), demo_user())


async def gather(farm):
    """One soil fetch through the real service path."""
    failures: list[str] = []
    return await environment_service._gather_soil(
        farm, farm.latitude, farm.longitude, failures
    ), failures


# --------------------------------------------------------------------------
# The row lands, and then prevents a call
# --------------------------------------------------------------------------


@respx.mock
async def test_a_first_fetch_writes_a_profile(farm) -> None:
    route = respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM))

    (observation, meta), failures = await gather(farm)

    assert route.call_count == 1
    assert failures == []
    assert meta.mode is DataMode.live
    assert observation.ph == pytest.approx(6.4)
    assert soil_repo.count_profiles() == 1


@respx.mock
async def test_a_stored_profile_prevents_the_outbound_request(farm) -> None:
    """The whole point of the step, in one assertion: `call_count == 1` after two
    gathers across a restart."""
    route = respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM))

    await gather(farm)
    clear_all_caches()  # a restart: the TTL cache is gone, the table is not
    store.reset()

    (observation, meta), failures = await gather(farm)

    assert route.call_count == 1, "a stored profile must not be refetched"
    assert observation.ph == pytest.approx(6.4)
    assert meta.mode is DataMode.cached
    assert failures == []


@respx.mock
async def test_a_stored_profile_reports_its_original_fetch_time(farm) -> None:
    """`fetched_at` is the age of the measurement, not of the read. Reporting the read
    time would make every profile permanently fresh and nothing would ever expire."""
    respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM))

    (_, first_meta), _ = await gather(farm)
    clear_all_caches()

    (_, second_meta), _ = await gather(farm)

    assert second_meta.fetched_at == first_meta.fetched_at


@respx.mock
async def test_the_profile_survives_a_restart(farm) -> None:
    respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM))
    await gather(farm)

    store.reset()

    stored = soil_repo.get_profile(farm.id)
    assert stored is not None
    assert stored.observation.ph == pytest.approx(6.4)
    assert stored.source == SOILGRIDS_SOURCE


# --------------------------------------------------------------------------
# Expiry
# --------------------------------------------------------------------------


@respx.mock
async def test_an_expired_profile_is_refetched(farm) -> None:
    """Past `CACHE_TTL_SOIL_S` the stored answer is no longer served."""
    route = respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM))
    await gather(farm)

    stale = datetime.now(UTC) - timedelta(seconds=settings.CACHE_TTL_SOIL_S + 60)
    stored = soil_repo.get_profile(farm.id)
    soil_repo.upsert_profile(
        farm.id,
        stored.observation,
        source=stored.source,
        mode=stored.mode,
        fetched_at=stale,
    )
    clear_all_caches()

    route.mock(return_value=httpx.Response(200, json=SANDY))
    (observation, _), _ = await gather(farm)

    assert route.call_count == 2
    assert observation.sand_pct == pytest.approx(85.0), "the refetched values must win"


@respx.mock
async def test_a_refetch_replaces_the_row_rather_than_adding_one(farm) -> None:
    """`UNIQUE(farm_id)` with upsert: one row per farm, no history."""
    route = respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM))
    await gather(farm)

    stale = datetime.now(UTC) - timedelta(seconds=settings.CACHE_TTL_SOIL_S + 60)
    stored = soil_repo.get_profile(farm.id)
    soil_repo.upsert_profile(
        farm.id, stored.observation, source=stored.source, mode=stored.mode, fetched_at=stale
    )
    clear_all_caches()
    route.mock(return_value=httpx.Response(200, json=SANDY))
    await gather(farm)

    assert soil_repo.count_profiles() == 1


def test_the_freshness_window_honours_a_disabled_ttl() -> None:
    now = datetime(2026, 8, 23, 12, tzinfo=UTC)
    profile = soil_repo.StoredProfile(
        observation=None, source="s", mode="live", fetched_at=now - timedelta(days=3650)
    )

    assert profile.is_fresh(now, 0) is True, "a non-positive TTL disables expiry"
    assert profile.is_fresh(now, -1) is True
    assert profile.is_fresh(now, 60) is False


# --------------------------------------------------------------------------
# Degradation — the behaviour that must NOT change
# --------------------------------------------------------------------------


@respx.mock
async def test_a_provider_failure_with_a_stored_profile_serves_the_profile(farm) -> None:
    """The outage this step exists to survive. Before it, a restart during an ISRIC
    outage meant simulated soil for every farm."""
    route = respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM))
    await gather(farm)

    clear_all_caches()
    store.reset()
    route.mock(return_value=httpx.Response(503))

    (observation, meta), failures = await gather(farm)

    assert observation.ph == pytest.approx(6.4), "real measurements, not a simulation"
    assert meta.mode is DataMode.cached
    assert failures == [], "a fresh profile means the provider is never asked"


@respx.mock
async def test_an_expired_profile_survives_a_provider_failure(farm) -> None:
    """Stale measurements still beat invented ones — but the failure is recorded, so the
    run still reports degraded provenance."""
    route = respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM))
    await gather(farm)

    stale = datetime.now(UTC) - timedelta(seconds=settings.CACHE_TTL_SOIL_S + 60)
    stored = soil_repo.get_profile(farm.id)
    soil_repo.upsert_profile(
        farm.id, stored.observation, source=stored.source, mode=stored.mode, fetched_at=stale
    )
    clear_all_caches()
    route.mock(return_value=httpx.Response(503))

    (observation, meta), failures = await gather(farm)

    assert observation.ph == pytest.approx(6.4)
    assert meta.mode is DataMode.cached
    assert SOILGRIDS_SOURCE in failures, "the outage must still be reported"
    assert "unavailable" in (meta.note or "")


@respx.mock
async def test_a_provider_failure_with_no_profile_still_simulates(farm) -> None:
    """Unchanged pre-existing behaviour: nothing stored and nothing reachable means the
    declared simulator, labelled `simulated` and recorded as a failure."""
    respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(503))

    (observation, meta), failures = await gather(farm)

    assert meta.mode is DataMode.simulated
    assert SOILGRIDS_SOURCE in failures
    assert observation is not None
    assert soil_repo.count_profiles() == 0, "a failure must never be stored as a profile"


@respx.mock
async def test_a_failed_fetch_is_not_persisted(farm) -> None:
    """A 503 must not write a row, or the outage would be remembered for thirty days."""
    respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(503))

    await gather(farm)

    assert soil_repo.count_profiles() == 0


# --------------------------------------------------------------------------
# Fidelity
# --------------------------------------------------------------------------


@respx.mock
async def test_uncovered_properties_stay_null(farm) -> None:
    """SoilGrids returns nothing for open water and unmapped terrain. A `None` must
    survive storage as `None` rather than becoming a fabricated number."""
    respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=SANDY))

    await gather(farm)

    stored = soil_repo.get_profile(farm.id)
    assert stored.observation.sand_pct == pytest.approx(85.0)
    assert stored.observation.nitrogen_g_kg is None
    assert stored.observation.cec_cmol_kg is None


def test_every_soil_field_round_trips(sqlite_db) -> None:
    """The structural guard, and the reason it exists.

    `water_holding_capacity_mm` was left out of `MEASUREMENTS` in the first draft of the
    repository. Nothing failed: the column simply was not written, the field came back
    `None`, and because it feeds the FAO-56 plant-available-water term every irrigation
    figure quietly changed after a restart. It also made the observation differ from the
    live one, so `inputs_hash` moved and *every* analysis missed its own cache.

    Enumerating the dataclass rather than a hand-written list is the point — a field
    added to `SoilObservation` and forgotten here fails immediately.
    """
    import dataclasses

    from app.core.deps import demo_user
    from app.providers.base import SoilObservation
    from app.schemas.enums import SoilTexture
    from app.services.farm_service import require_farm

    seed_crops()
    seed_demo_farms()
    record = require_farm(demo_id("farm", "nakuru-maize-field"), demo_user())

    # A distinct, non-default value in every field, so a dropped one cannot coincide
    # with what storage would have returned anyway.
    populated = SoilObservation(
        **{
            field.name: round(3.0 + index / 10, 3)
            for index, field in enumerate(dataclasses.fields(SoilObservation))
            if field.name != "texture_class"
        },
        texture_class=SoilTexture.clay,
    )

    soil_repo.upsert_profile(
        record.id,
        populated,
        source=SOILGRIDS_SOURCE,
        mode=DataMode.live,
        fetched_at=datetime.now(UTC),
    )

    restored = soil_repo.get_profile(record.id).observation

    assert restored == populated, "a soil field was lost in storage"
    for field in dataclasses.fields(SoilObservation):
        value = getattr(restored, field.name)
        assert value is not None, f"{field.name} was dropped on the round trip"


def test_the_stored_texture_is_an_enum_not_a_string(sqlite_db) -> None:
    """The live path yields `SoilTexture`; a stored profile must too. A value equal only
    as a string still compares unequal wherever the enum is expected."""
    from app.core.deps import demo_user
    from app.providers.base import SoilObservation
    from app.schemas.enums import SoilTexture
    from app.services.farm_service import require_farm

    seed_crops()
    seed_demo_farms()
    record = require_farm(demo_id("farm", "nakuru-maize-field"), demo_user())

    soil_repo.upsert_profile(
        record.id,
        SoilObservation(texture_class=SoilTexture.silty_clay, ph=6.1),
        source=SOILGRIDS_SOURCE,
        mode=DataMode.live,
        fetched_at=datetime.now(UTC),
    )

    restored = soil_repo.get_profile(record.id).observation

    assert restored.texture_class is SoilTexture.silty_clay


@respx.mock
async def test_analyses_over_a_stored_profile_share_one_run(farm, client, api_prefix) -> None:
    """Two analyses that both read soil from storage must hit the same cached run.

    Not the *first* analysis: it fetches soil live, and `ProviderCache.get_or_fetch` has
    always re-stamped a subsequent hit as `cached`, so the first run's provenance
    legitimately differs from every later one. That flip predates this step — it happens
    with no database at all — and Step 2 deliberately made provenance part of
    `inputs_hash`, so it is correct that live and cached runs are not interchangeable.

    What must hold is that storage adds no *further* divergence. Once the profile is
    being served from the table, the observation and its provenance are fixed, so every
    analysis from that point on asks the same question. When
    `water_holding_capacity_mm` was being dropped they did not, and no two analyses ever
    agreed.
    """
    from uuid import UUID

    from app.db import analysis_repo

    respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM))

    await client.post(f"{api_prefix}/farms/{farm.id}/analysis")  # live soil
    clear_all_caches()
    second = (await client.post(f"{api_prefix}/farms/{farm.id}/analysis")).json()
    clear_all_caches()
    third = (await client.post(f"{api_prefix}/farms/{farm.id}/analysis")).json()

    assert third["id"] == second["id"], "a stored profile must not move the digest"
    assert analysis_repo.count_runs(UUID(str(farm.id))) == 2


@respx.mock
async def test_the_raw_payload_is_kept(farm) -> None:
    """Stored for debugging and reprocessing without a refetch."""
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import SoilProfileORM

    respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM))
    await gather(farm)

    with session_scope() as db:
        row = db.scalars(select(SoilProfileORM)).first()

    assert row is not None
    assert row.raw is not None
    assert row.raw["ph"] == pytest.approx(6.4)
    assert row.depth_cm == "0-30"


@respx.mock
async def test_a_stored_simulation_is_never_relabelled_as_a_measurement(farm) -> None:
    """Persistence must not launder a simulated value into a `cached` measurement."""
    settings.SOIL_PROVIDER = "simulated"
    try:
        await gather(farm)
        clear_all_caches()
        (_, meta), _ = await gather(farm)
    finally:
        settings.SOIL_PROVIDER = "soilgrids"

    assert meta.mode is DataMode.simulated, "a simulation stays a simulation"


# --------------------------------------------------------------------------
# The endpoint, end to end
# --------------------------------------------------------------------------


@respx.mock
async def test_the_soil_endpoint_serves_a_stored_profile_after_a_restart(
    farm, client, api_prefix
) -> None:
    route = respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM))

    first = (await client.get(f"{api_prefix}/farms/{farm.id}/soil")).json()
    clear_all_caches()
    store.reset()
    second = (await client.get(f"{api_prefix}/farms/{farm.id}/soil")).json()

    assert route.call_count == 1
    assert second["ph"] == first["ph"]
    assert second["sand_pct"] == first["sand_pct"]


# --------------------------------------------------------------------------
# PostgreSQL only
# --------------------------------------------------------------------------


@pytest.mark.postgres
@respx.mock
async def test_the_farm_cascade_removes_the_profile_in_postgres(postgres_db) -> None:
    """SQLite runs with foreign keys off, so this is the only place the cascade is
    proven."""
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import FarmORM, SoilProfileORM

    settings.SOIL_PROVIDER = "soilgrids"
    clear_all_caches()
    seed_crops()
    seed_demo_farms()
    try:
        respx.get(SOILGRIDS_URL).mock(return_value=httpx.Response(200, json=LOAM))
        record = require_farm(demo_id("farm", "nakuru-maize-field"), demo_user())
        await gather(record)
        assert soil_repo.count_profiles() == 1

        with session_scope() as db:
            db.delete(db.get(FarmORM, UUID(str(record.id))))

        with session_scope() as db:
            assert db.scalars(select(SoilProfileORM)).all() == []
    finally:
        clear_all_caches()
