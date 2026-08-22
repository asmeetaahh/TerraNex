"""The crop catalog, served from the database.

The catalog is reference data the whole product hangs off: a `crop_id` identifies a
planting, drives the risk engine's agronomic constants, and appears in frontend
fixtures. Moving it from a dict to a table must therefore change **nothing** an API
consumer can observe.

Three properties carry that:

* ids stay `uuid5(code)`, so a hardcoded `crop_id` keeps resolving,
* order stays the fixture's curated order, because the frontend renders it into a
  dropdown and alphabetical would put alfalfa where maize was,
* every field survives the round trip, including the JSON list columns.
"""

import pytest

from app.db.seed import crop_id_for, load_crop_catalog, seed_crops
from app.db.session import database_enabled, session_scope
from app.models import CropORM
from app.schemas.enums import CropCategory, Season
from app.services import reference_service

CATALOG_SIZE = 26


def test_the_fixture_seeds_a_database(sqlite_db) -> None:
    assert database_enabled()

    with session_scope() as db:
        assert db.query(CropORM).count() == CATALOG_SIZE


def test_seeding_is_idempotent(sqlite_db) -> None:
    """Every boot re-seeds. A second run must update, never duplicate."""
    seed_crops()
    seed_crops()

    with session_scope() as db:
        assert db.query(CropORM).count() == CATALOG_SIZE


def test_crop_ids_are_the_same_uuid5_as_before(sqlite_db) -> None:
    """The guarantee the derivation existed for: a `crop_id` a frontend hardcoded in
    Phase 3 must still resolve now the catalog lives in Postgres."""
    with session_scope() as db:
        rows = {row.code: row.id for row in db.query(CropORM).all()}

    for crop in load_crop_catalog():
        assert rows[crop.code] == crop_id_for(crop.code)
        assert rows[crop.code] == crop.id


def test_catalog_order_matches_the_fixture(sqlite_db) -> None:
    """Curated order, not alphabetical — staples first."""
    expected = [crop.code for crop in load_crop_catalog()]

    served = reference_service.list_crops(category=None, season=None, page=1, page_size=200)

    assert [crop.code for crop in served.items] == expected
    assert served.items[0].code == "maize"


def test_every_field_survives_the_round_trip(sqlite_db) -> None:
    """Including the JSON list columns, which are the ones a column-type mistake
    would silently empty."""
    fixture = {crop.code: crop for crop in load_crop_catalog()}

    served = reference_service.list_crops(category=None, season=None, page=1, page_size=200)

    for crop in served.items:
        assert crop.model_dump() == fixture[crop.code].model_dump()

    maize = next(c for c in served.items if c.code == "maize")
    assert maize.preferred_textures
    assert maize.common_diseases


def test_get_crop_resolves_by_id(sqlite_db) -> None:
    found = reference_service.get_crop(crop_id_for("maize"))

    assert found is not None
    assert found.code == "maize"


def test_get_crop_returns_none_for_an_unknown_id(sqlite_db) -> None:
    from uuid import uuid4

    assert reference_service.get_crop(uuid4()) is None


@pytest.mark.parametrize(
    ("category", "season"),
    [(CropCategory.cereal, None), (None, Season.year_round), (CropCategory.legume, None)],
)
def test_filters_are_applied_in_sql(sqlite_db, category, season) -> None:
    """Filtering moved from a Python comprehension into a WHERE clause; the result
    must be identical to filtering the fixture."""
    expected = [
        crop.code
        for crop in load_crop_catalog()
        if (category is None or crop.category == category)
        and (season is None or crop.season == season)
    ]

    served = reference_service.list_crops(category=category, season=season, page=1, page_size=200)

    assert [crop.code for crop in served.items] == expected


def test_pagination_still_reports_the_full_total(sqlite_db) -> None:
    page = reference_service.list_crops(category=None, season=None, page=1, page_size=5)

    assert len(page.items) == 5
    assert page.total == CATALOG_SIZE
    assert page.has_next is True


def test_the_catalog_survives_a_restart(sqlite_db) -> None:
    """The point of the phase. Dispose the engine and reconnect — the rows are still
    there, with the same ids, without re-seeding."""
    from app.db import session as session_module

    before = reference_service.list_crops(category=None, season=None, page=1, page_size=200)

    session_module.dispose_engine()

    after = reference_service.list_crops(category=None, season=None, page=1, page_size=200)

    assert [c.id for c in after.items] == [c.id for c in before.items]
    assert after.total == CATALOG_SIZE
