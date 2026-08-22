"""Demo seeding, persisted.

`seed_crops` gained a database branch during the persistence migration;
`seed_demo_farms` did not. With `DATABASE_URL` set it kept writing to the in-memory
store, so startup logged `demo_farms_seeded: 3` while `GET /farms` — which reads the
`farms` table — returned nothing. The failure was silent in both directions: the log
claimed success, and the idempotency guard checked memory, so every boot re-seeded a
store nothing read.

`tests/api/test_demo_seed.py` covers the in-memory path and still does. These cover the
database one, and the property that matters most is `test_the_api_serves_the_seeded_farms`
— the symptom a user would actually have seen.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.deps import demo_user
from app.db.memory import store
from app.db.seed import DEMO_FARMS, demo_id, seed_demo_farms
from app.db.session import session_scope
from app.models import FarmCropORM, FarmORM, UserORM

EXPECTED_FARMS = 3
EXPECTED_PLANTINGS = sum(len(spec["crops"]) for spec in DEMO_FARMS)


# --------------------------------------------------------------------------
# The rows land in the database
# --------------------------------------------------------------------------


def test_seeding_writes_farms_to_the_database(sqlite_db) -> None:
    created = seed_demo_farms()

    assert created == EXPECTED_FARMS
    with session_scope() as db:
        assert db.query(FarmORM).count() == EXPECTED_FARMS


def test_seeding_writes_the_plantings_too(sqlite_db) -> None:
    """A farm without its crops is not worth showing, which is why the two are
    written in one transaction."""
    seed_demo_farms()

    with session_scope() as db:
        assert db.query(FarmCropORM).count() == EXPECTED_PLANTINGS
        for spec in DEMO_FARMS:
            farm_id = demo_id("farm", spec["slug"])
            plantings = db.scalars(select(FarmCropORM).where(FarmCropORM.farm_id == farm_id)).all()
            assert len(plantings) == len(spec["crops"])
            assert sum(1 for p in plantings if p.is_primary) == 1


def test_nothing_is_written_to_the_memory_store(sqlite_db) -> None:
    """The defect in one assertion: with a database configured, the store must stay
    empty. It used to receive all three farms."""
    seed_demo_farms()

    assert store.farms == {}
    assert store.farm_crops == {}


async def test_the_api_serves_the_seeded_farms(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """The symptom. `GET /farms` returned `total: 0` while startup logged three."""
    seed_demo_farms()

    body = (await client.get(f"{api_prefix}/farms")).json()

    assert body["total"] == EXPECTED_FARMS
    assert {farm["name"] for farm in body["items"]} == {spec["name"] for spec in DEMO_FARMS}


async def test_a_seeded_farm_carries_its_crops_through_the_api(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    seed_demo_farms()

    farms = (await client.get(f"{api_prefix}/farms")).json()["items"]
    first = next(f for f in farms if f["id"] == str(demo_id("farm", DEMO_FARMS[0]["slug"])))
    plantings = (await client.get(f"{api_prefix}/farms/{first['id']}/crops")).json()

    assert first["crop_count"] == len(DEMO_FARMS[0]["crops"])
    assert plantings["total"] == len(DEMO_FARMS[0]["crops"])


# --------------------------------------------------------------------------
# Deterministic ids
# --------------------------------------------------------------------------


def test_farm_ids_are_the_derived_ones(sqlite_db) -> None:
    """The same id in Postgres as in the store, on every machine and across restarts —
    so a frontend fixture referencing a demo farm keeps resolving."""
    seed_demo_farms()

    with session_scope() as db:
        stored = {row.id for row in db.scalars(select(FarmORM))}

    assert stored == {demo_id("farm", spec["slug"]) for spec in DEMO_FARMS}


def test_planting_ids_are_the_derived_ones(sqlite_db) -> None:
    seed_demo_farms()

    with session_scope() as db:
        stored = {row.id for row in db.scalars(select(FarmCropORM))}

    expected = {
        demo_id("planting", f"{spec['slug']}:{planting['crop_code']}")
        for spec in DEMO_FARMS
        for planting in spec["crops"]
    }
    assert stored == expected


def test_database_ids_match_the_memory_path(sqlite_db) -> None:
    """Both branches derive from `demo_id`, so switching storage must not move an id."""
    seed_demo_farms()
    with session_scope() as db:
        from_database = sorted(str(row.id) for row in db.scalars(select(FarmORM)))

    # An explicit target always takes the in-memory branch.
    seed_demo_farms(store)
    from_memory = sorted(str(farm_id) for farm_id in store.farms)

    assert from_database == from_memory


# --------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------


async def test_the_api_lists_the_farms_in_fixture_order(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """`live_farms` orders by `created_at, id`, and seeding stamped all three farms with
    a single timestamp — so the tiebreak fell to the uuid5 id and `GET /farms` returned
    them in an arbitrary order that happened to differ from the in-memory path."""
    seed_demo_farms()

    listed = (await client.get(f"{api_prefix}/farms")).json()["items"]

    assert [row["name"] for row in listed] == [spec["name"] for spec in DEMO_FARMS]


async def test_a_farms_plantings_keep_their_fixture_order(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """The planting query orders by `created_at, id` too, so a farm's plantings — all
    written in one batch — had the same degenerate tiebreak.

    Note this does not reproduce the defect: with today's fixture the uuid5 ids happen
    to sort into fixture order anyway, so it passed before the fix as well. It is here
    to hold the invariant once a demo crop is added or a slug changes.
    `test_the_seeded_timestamps_are_distinct` is what actually catches a regression.
    """
    seed_demo_farms()
    spec = next(s for s in DEMO_FARMS if len(s["crops"]) > 1)

    farm_id = demo_id("farm", spec["slug"])
    listed = (await client.get(f"{api_prefix}/farms/{farm_id}/crops")).json()["items"]

    assert [row["crop"]["code"] for row in listed] == [c["crop_code"] for c in spec["crops"]]


def test_the_seeded_timestamps_are_distinct(sqlite_db) -> None:
    """The property the ordering actually rests on, asserted directly.

    Both API-order tests above depend on the uuid5 tiebreak never being reached. This
    is the one that fails if a single shared `created_at` comes back — including for
    plantings, where the current fixture ids mask the symptom entirely.
    """
    seed_demo_farms()

    with session_scope() as db:
        farm_stamps = {row.id: row.created_at for row in db.scalars(select(FarmORM))}
        planting_stamps = [
            (row.farm_id, row.id, row.created_at) for row in db.scalars(select(FarmCropORM))
        ]

    # Keyed off the fixture rather than the query, which declares no order of its own.
    stamps = [farm_stamps[demo_id("farm", spec["slug"])] for spec in DEMO_FARMS]
    assert len(set(stamps)) == EXPECTED_FARMS
    assert stamps == sorted(stamps)

    # Plantings are only ever queried one farm at a time, so they need to be distinct
    # within a farm — not across the whole table.
    for spec in DEMO_FARMS:
        farm_id = demo_id("farm", spec["slug"])
        within = [stamp for owner, _planting_id, stamp in planting_stamps if owner == farm_id]
        ordered = [
            stamp
            for planting in spec["crops"]
            for owner, planting_id, stamp in planting_stamps
            if owner == farm_id
            and planting_id == demo_id("planting", f"{spec['slug']}:{planting['crop_code']}")
        ]
        assert len(set(within)) == len(spec["crops"]), f"{spec['slug']} reused a timestamp"
        assert ordered == sorted(ordered), f"{spec['slug']} plantings are not ascending"


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_a_second_seed_creates_nothing(sqlite_db) -> None:
    """Every boot re-seeds. The guard now queries the table rather than the store,
    which is what made the old one useless with a database configured."""
    assert seed_demo_farms() == EXPECTED_FARMS
    assert seed_demo_farms() == 0
    assert seed_demo_farms() == 0

    with session_scope() as db:
        assert db.query(FarmORM).count() == EXPECTED_FARMS
        assert db.query(FarmCropORM).count() == EXPECTED_PLANTINGS


def test_a_soft_deleted_demo_farm_is_not_resurrected(sqlite_db) -> None:
    """A user who deletes a demo farm must not find it back after a restart. The
    existence check looks at the row, deliberately ignoring `deleted_at`."""
    from datetime import UTC, datetime

    seed_demo_farms()
    target = demo_id("farm", DEMO_FARMS[0]["slug"])

    with session_scope() as db:
        db.get(FarmORM, target).deleted_at = datetime.now(UTC)

    assert seed_demo_farms() == 0

    with session_scope() as db:
        assert db.get(FarmORM, target).deleted_at is not None
        assert db.query(FarmORM).count() == EXPECTED_FARMS


async def test_a_deleted_demo_farm_stays_out_of_the_listing(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    seed_demo_farms()
    target = str(demo_id("farm", DEMO_FARMS[0]["slug"]))

    assert (await client.delete(f"{api_prefix}/farms/{target}")).status_code == 204
    seed_demo_farms()

    body = (await client.get(f"{api_prefix}/farms")).json()

    assert body["total"] == EXPECTED_FARMS - 1
    assert target not in {farm["id"] for farm in body["items"]}


# --------------------------------------------------------------------------
# Ownership
# --------------------------------------------------------------------------


def test_the_demo_owner_row_exists(sqlite_db) -> None:
    """`farms.user_id` is a foreign key, so the owner has to be written first."""
    seed_demo_farms()

    with session_scope() as db:
        owner = db.get(UserORM, demo_user().id)

    assert owner is not None
    assert owner.auth_id == demo_user().auth_id


def test_every_seeded_farm_is_owned_by_the_demo_user(sqlite_db) -> None:
    """Unowned demo farms would be invisible the moment ownership was enforced."""
    seed_demo_farms()

    with session_scope() as db:
        owners = {row.user_id for row in db.scalars(select(FarmORM))}

    assert owners == {demo_user().id}


# --------------------------------------------------------------------------
# PostgreSQL only
# --------------------------------------------------------------------------


@pytest.mark.postgres
def test_the_farms_keep_their_fixture_order_in_postgres(postgres_db) -> None:
    """The ordering fix leans on microsecond resolution. SQLite keeps timestamps as
    text and would preserve any precision; `timestamptz` is the one that could round
    the step away, which would silently restore the uuid5 tiebreak."""
    from app.db import farm_repo

    seed_demo_farms()

    assert [row.name for row in farm_repo.live_farms()] == [s["name"] for s in DEMO_FARMS]


@pytest.mark.postgres
def test_the_owner_foreign_key_is_satisfied_in_postgres(postgres_db) -> None:
    """SQLite runs with `PRAGMA foreign_keys = 0`, so it would accept these rows even
    if the owner had never been written. Postgres would reject them, which makes this
    the only place `ensure_user` being called first is actually proven."""
    created = seed_demo_farms()

    assert created == EXPECTED_FARMS
    with session_scope() as db:
        assert db.get(UserORM, demo_user().id) is not None
        assert db.query(FarmORM).count() == EXPECTED_FARMS
        assert db.query(FarmCropORM).count() == EXPECTED_PLANTINGS
