"""PostgreSQL-specific persistence behaviour.

The fast tier runs on SQLite, which is portable and needs nothing installed but is not
production. Four things it genuinely cannot answer live here:

* **foreign keys** — SQLite does not enforce them unless `PRAGMA foreign_keys=ON` is
  set per connection, so a broken reference passes there and fails in Postgres,
* **`ON DELETE CASCADE`** — for the same reason,
* **native types** — `uuid` and `numeric(9, 6)` are real column types in Postgres and
  emulated in SQLite, so precision and round-tripping differ,
* **`CHECK` constraints on enum columns** — the guarantee behind choosing `String` +
  `CHECK` over a native `ENUM`.

Every test is marked `postgres` and **skipped unless `TEST_DATABASE_URL` is set**, so
`uv run pytest` stays green on a machine with nothing provisioned. To run them:

    TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/terranex_test \\
      uv run pytest tests/db/test_postgres.py

The schema is dropped and recreated per test, so never point this at a database whose
contents matter.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db import farm_repo
from app.db.memory import FarmCropRecord, FarmRecord
from app.db.seed import crop_id_for, load_crop_catalog
from app.db.session import get_engine, session_scope
from app.models import CropORM, FarmCropORM, FarmORM, UserORM

pytestmark = pytest.mark.postgres


def _farm(**overrides) -> FarmRecord:
    now = datetime.now(UTC)
    return FarmRecord(
        id=overrides.pop("id", uuid.uuid4()),
        name=overrides.pop("name", "PG Field"),
        latitude=overrides.pop("latitude", 19.99727),
        longitude=overrides.pop("longitude", 73.79096),
        area_hectares=None,
        country_code=None,
        region=None,
        elevation_m=None,
        irrigation_type=overrides.pop("irrigation_type", "rainfed"),
        farming_practice=overrides.pop("farming_practice", "conventional"),
        notes=None,
        created_at=now,
        updated_at=now,
        **overrides,
    )


def _planting(farm_id: uuid.UUID, crop_id: uuid.UUID, **overrides) -> FarmCropRecord:
    now = datetime.now(UTC)
    return FarmCropRecord(
        id=uuid.uuid4(),
        farm_id=farm_id,
        crop_id=crop_id,
        planting_date=None,
        expected_harvest_date=None,
        growth_stage=overrides.pop("growth_stage", "not_planted"),
        area_hectares=None,
        is_primary=overrides.pop("is_primary", False),
        status=overrides.pop("status", "planned"),
        notes=None,
        created_at=now,
        updated_at=now,
    )


# --------------------------------------------------------------------------
# Native types
# --------------------------------------------------------------------------


def test_columns_use_native_postgres_types(postgres_db) -> None:
    columns = {c["name"]: c["type"] for c in inspect(get_engine()).get_columns("farms")}

    assert type(columns["id"]).__name__.upper().startswith("UUID")
    assert str(columns["latitude"]).upper().startswith("NUMERIC")


def test_coordinates_keep_six_decimal_places(postgres_db) -> None:
    """`numeric(9, 6)` is ~0.1 m. Losing precision here would move a farm."""
    record = _farm(latitude=-21.177400, longitude=-47.810300)
    farm_repo.insert_farm(record)

    stored = farm_repo.get_farm(record.id)

    assert stored is not None
    assert stored.latitude == pytest.approx(-21.1774, abs=1e-6)
    assert stored.longitude == pytest.approx(-47.8103, abs=1e-6)
    assert isinstance(stored.latitude, float), "Numeric must not surface as Decimal"


def test_uuids_round_trip_as_uuid_objects(postgres_db) -> None:
    record = _farm()
    farm_repo.insert_farm(record)

    with session_scope() as db:
        row = db.get(FarmORM, record.id)
        assert isinstance(row.id, uuid.UUID)


# --------------------------------------------------------------------------
# Referential integrity — the reason this tier exists
# --------------------------------------------------------------------------


def test_a_planting_cannot_reference_a_missing_farm(postgres_db) -> None:
    """SQLite lets this through unless foreign keys are switched on per connection."""
    with pytest.raises(IntegrityError):
        farm_repo.insert_planting(_planting(uuid.uuid4(), crop_id_for("maize")))


def test_a_planting_cannot_reference_a_missing_crop(postgres_db) -> None:
    record = _farm()
    farm_repo.insert_farm(record)

    with pytest.raises(IntegrityError):
        farm_repo.insert_planting(_planting(record.id, uuid.uuid4()))


def test_deleting_a_farm_row_cascades_to_its_plantings(postgres_db) -> None:
    """`ON DELETE CASCADE` on the hard delete path. The API only ever soft-deletes, so
    this covers an operator or a future purge job."""
    record = _farm()
    farm_repo.insert_farm(record)
    farm_repo.insert_planting(_planting(record.id, crop_id_for("maize")))

    with session_scope() as db:
        db.delete(db.get(FarmORM, record.id))

    with session_scope() as db:
        assert db.query(FarmCropORM).count() == 0


# --------------------------------------------------------------------------
# CHECK constraints
#
# The guarantee behind `String` + `CHECK` instead of a native `ENUM`.
# --------------------------------------------------------------------------


def test_an_invalid_enum_value_is_rejected(postgres_db) -> None:
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(
            FarmORM(
                id=uuid.uuid4(),
                name="Bad",
                latitude=0.0,
                longitude=0.0,
                irrigation_type="teleportation",
                farming_practice="conventional",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )


def test_an_out_of_range_coordinate_is_rejected(postgres_db) -> None:
    """Pydantic already rejects this at the edge; the database is the backstop for
    anything that reaches it another way."""
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(
            FarmORM(
                id=uuid.uuid4(),
                name="Off world",
                latitude=120.0,
                longitude=0.0,
                irrigation_type="rainfed",
                farming_practice="conventional",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )


def test_a_harvest_before_planting_is_rejected(postgres_db) -> None:
    from datetime import date

    record = _farm()
    farm_repo.insert_farm(record)

    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(
            FarmCropORM(
                id=uuid.uuid4(),
                farm_id=record.id,
                crop_id=crop_id_for("maize"),
                planting_date=date(2026, 9, 1),
                expected_harvest_date=date(2026, 4, 1),
                growth_stage="not_planted",
                is_primary=False,
                status="planned",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )


def test_crop_codes_are_unique(postgres_db) -> None:
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(
            CropORM(
                id=uuid.uuid4(),
                sort_order=999,
                code="maize",
                name="Duplicate maize",
                category="cereal",
                season="summer",
                preferred_textures=[],
                common_diseases=[],
            )
        )


# --------------------------------------------------------------------------
# Transactions and seeding
# --------------------------------------------------------------------------


def test_a_rollback_leaves_nothing_behind(postgres_db) -> None:
    farm_repo.insert_farm(_farm(name="Kept"))

    with pytest.raises(ValueError), session_scope() as db:
        db.add(
            FarmORM(
                id=uuid.uuid4(),
                name="Discarded",
                latitude=1.0,
                longitude=1.0,
                irrigation_type="rainfed",
                farming_practice="conventional",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        db.flush()
        raise ValueError("boom")

    with session_scope() as db:
        assert [row.name for row in db.query(FarmORM).all()] == ["Kept"]


def test_the_catalog_seeds_with_stable_ids(postgres_db) -> None:
    with session_scope() as db:
        rows = {row.code: row.id for row in db.query(CropORM).all()}

    assert len(rows) == len(load_crop_catalog())
    for crop in load_crop_catalog():
        assert rows[crop.code] == crop_id_for(crop.code)


def test_json_columns_round_trip(postgres_db) -> None:
    """`preferred_textures` and `common_diseases` are the columns a type mistake would
    silently empty."""
    with session_scope() as db:
        maize = db.query(CropORM).filter(CropORM.code == "maize").one()

    assert isinstance(maize.preferred_textures, list)
    assert maize.preferred_textures
    assert maize.common_diseases


def test_the_migration_left_a_stamped_version(postgres_db) -> None:
    with get_engine().connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()

    assert version


# --------------------------------------------------------------------------
# Phase 2b: ownership
#
# These are the reason this tier exists at all. SQLite runs with
# `PRAGMA foreign_keys = 0`, so it accepts a farm pointing at a user that was never
# created and never fires a cascade — both were measured to pass silently there. Farm
# ownership is the one place in this schema where a dangling reference is a security
# property, not just an integrity one, so it needs a database that enforces it.
# --------------------------------------------------------------------------


def _user(auth_id: str = "pg-owner", **overrides) -> UserORM:
    now = datetime.now(UTC)
    return UserORM(
        id=overrides.pop("id", uuid.uuid4()),
        auth_id=auth_id,
        email=overrides.pop("email", f"{auth_id}@example.test"),
        created_at=now,
        updated_at=now,
    )


def test_a_farm_cannot_reference_a_missing_user(postgres_db) -> None:
    """The ownership foreign key.

    A farm whose `user_id` names no row would be owned by nobody — invisible to every
    authenticated caller and, if that id were ever reused, silently transferred. SQLite
    accepts this insert; Postgres must not.
    """
    with pytest.raises(IntegrityError):
        farm_repo.insert_farm(_farm(user_id=uuid.uuid4()))


def test_deleting_a_user_deletes_their_farms(postgres_db) -> None:
    """`ON DELETE CASCADE` from `users` to `farms`.

    A deletion request has to remove the farms too; leaving them behind would orphan
    rows that no longer have a reachable owner.
    """
    owner = _user("cascade-farms")
    with session_scope() as db:
        db.add(owner)
    owner_id = owner.id

    farm_repo.insert_farm(_farm(user_id=owner_id, name="Doomed"))
    farm_repo.insert_farm(_farm(user_id=owner_id, name="Also doomed"))
    with session_scope() as db:
        assert db.query(FarmORM).filter(FarmORM.user_id == owner_id).count() == 2

    with session_scope() as db:
        db.delete(db.get(UserORM, owner_id))

    with session_scope() as db:
        assert db.query(FarmORM).filter(FarmORM.user_id == owner_id).count() == 0


def test_the_cascade_continues_to_plantings(postgres_db) -> None:
    """Two levels: users → farms → farm_crops.

    The second hop is a separate constraint. If only the first fired, deleting a user
    would leave plantings referencing farms that no longer exist.
    """
    owner = _user("cascade-plantings")
    with session_scope() as db:
        db.add(owner)
    owner_id = owner.id

    farm = _farm(user_id=owner_id)
    farm_repo.insert_farm(farm)
    farm_repo.insert_planting(_planting(farm.id, crop_id_for("maize")))
    farm_repo.insert_planting(_planting(farm.id, crop_id_for("wheat")))
    with session_scope() as db:
        assert db.query(FarmCropORM).filter(FarmCropORM.farm_id == farm.id).count() == 2

    with session_scope() as db:
        db.delete(db.get(UserORM, owner_id))

    with session_scope() as db:
        assert db.query(FarmORM).filter(FarmORM.id == farm.id).count() == 0
        assert db.query(FarmCropORM).filter(FarmCropORM.farm_id == farm.id).count() == 0
        # The catalog is reference data and must survive; only the owner's rows go.
        assert db.query(CropORM).count() == len(load_crop_catalog())


def test_ownership_columns_use_the_native_uuid_type(postgres_db) -> None:
    """`users.id` and `farms.user_id` must be real `uuid` columns.

    SQLite emulates `Uuid` as text, so a mismatch between the two sides of the foreign
    key — or a column that silently became `varchar` — is invisible there.
    """
    inspector = inspect(get_engine())
    users = {c["name"]: c["type"] for c in inspector.get_columns("users")}
    farms = {c["name"]: c["type"] for c in inspector.get_columns("farms")}

    assert type(users["id"]).__name__.upper().startswith("UUID")
    assert type(farms["user_id"]).__name__.upper().startswith("UUID")

    # And the foreign key is actually declared, not merely implied by the column name.
    foreign_keys = inspector.get_foreign_keys("farms")
    ownership = [fk for fk in foreign_keys if fk["constrained_columns"] == ["user_id"]]
    assert ownership, "farms.user_id has no foreign key to users"
    assert ownership[0]["referred_table"] == "users"


def test_auth_id_is_unique_in_postgres_too(postgres_db) -> None:
    """SQLite enforces this, so the fast tier covers it — but a split identity would
    divide one user's farms between two rows, and it is cheap to confirm here."""
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(_user("duplicate"))
        db.add(_user("duplicate"))
