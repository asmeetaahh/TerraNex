"""Every timestamp on the wire is UTC with a trailing `Z`.

The contract says ISO-8601 UTC with `Z`. Persistence quietly broke that, because
`DateTime(timezone=True)` is a request rather than a guarantee and the two backends
honour it differently:

* **SQLite** has no timestamp type and returns a *naive* datetime, which serialises with
  no suffix at all. `new Date("2026-08-22T19:35:03.333914")` in a browser parses that as
  **local time**, so the value is wrong by the viewer's UTC offset — not merely
  formatted differently. Near midnight it shows the wrong calendar day.
* **Postgres** returns an aware datetime in the session time zone, serialising as
  `+05:30`. The instant is correct; the format still is not what the contract documents.
* The **in-memory** path never round-trips and keeps its original UTC.

The result was that the same farm reported `created_at` one way from `POST /farms`
(built in memory and returned directly) and another from `GET /farms/{id}` (read back
through the repository) — a divergence within a single storage backend.

`as_utc` normalises on read. These tests pin the wire format, which is the only thing a
client can actually observe, and they run against whichever backend the fixture selects
so no configuration is left unchecked.
"""

import io

import pytest
from httpx import AsyncClient

from tests.conftest import JPEG_BYTES

FARM = {
    "name": "Timestamp Farm",
    "latitude": 1.5,
    "longitude": 36.8,
    "area_hectares": 4.0,
    "irrigation_type": "rainfed",
    "farming_practice": "conventional",
}


def timestamps(body: dict) -> dict[str, str]:
    """Every `*_at` string in a payload, ignoring nulls."""
    return {k: v for k, v in body.items() if k.endswith("_at") and isinstance(v, str)}


def assert_utc(body: dict, where: str) -> dict[str, str]:
    found = timestamps(body)
    for field, value in found.items():
        assert value.endswith("Z"), f"{where}.{field} is not UTC-with-Z: {value}"
    return found


# --------------------------------------------------------------------------
# In-memory path (the suite default)
# --------------------------------------------------------------------------


async def test_memory_path_farm_timestamps_are_utc(client: AsyncClient, api_prefix: str) -> None:
    created = (await client.post(f"{api_prefix}/farms", json=FARM)).json()
    fetched = (await client.get(f"{api_prefix}/farms/{created['id']}")).json()

    assert assert_utc(created, "POST /farms")
    assert assert_utc(fetched, "GET /farms/{id}")


# --------------------------------------------------------------------------
# Database path
# --------------------------------------------------------------------------


async def test_a_farm_reports_the_same_timestamp_however_it_is_read(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """The regression, stated exactly.

    `POST` returns a record built in memory; `GET` and the list endpoint return one read
    back from the database. All three describe the same row and must agree — before
    `as_utc` they did not, and only the create response carried the `Z`.
    """
    created = (await client.post(f"{api_prefix}/farms", json=FARM)).json()
    fetched = (await client.get(f"{api_prefix}/farms/{created['id']}")).json()
    listed = next(
        f
        for f in (await client.get(f"{api_prefix}/farms")).json()["items"]
        if f["id"] == created["id"]
    )

    assert created["created_at"] == fetched["created_at"] == listed["created_at"]
    assert created["updated_at"] == fetched["updated_at"] == listed["updated_at"]


@pytest.mark.parametrize("endpoint", ["detail", "list"])
async def test_database_farm_timestamps_are_utc(
    sqlite_db, client: AsyncClient, api_prefix: str, endpoint: str
) -> None:
    created = (await client.post(f"{api_prefix}/farms", json=FARM)).json()

    if endpoint == "detail":
        body = (await client.get(f"{api_prefix}/farms/{created['id']}")).json()
    else:
        body = next(
            f
            for f in (await client.get(f"{api_prefix}/farms")).json()["items"]
            if f["id"] == created["id"]
        )

    found = assert_utc(body, f"farm {endpoint}")
    assert "created_at" in found and "updated_at" in found


async def test_database_planting_timestamps_are_utc(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    from app.db.seed import seed_crops

    seed_crops()
    farm = (await client.post(f"{api_prefix}/farms", json=FARM)).json()
    crop = (await client.get(f"{api_prefix}/reference/crops")).json()["items"][0]
    await client.post(
        f"{api_prefix}/farms/{farm['id']}/crops",
        json={"crop_id": crop["id"], "growth_stage": "flowering", "is_primary": True},
    )

    listed = (await client.get(f"{api_prefix}/farms/{farm['id']}/crops")).json()["items"][0]

    found = assert_utc(listed, "planting")
    assert "created_at" in found and "updated_at" in found


async def test_database_analysis_and_image_timestamps_are_utc(
    sqlite_db, client: AsyncClient, api_prefix: str
) -> None:
    """Runs and images were already correct — runs read their timestamp out of the
    stored JSON, and images gained `as_utc` when they were persisted. Pinned here so a
    future change cannot regress them along the path the others took."""
    from app.db.seed import demo_id, seed_crops, seed_demo_farms

    seed_crops()
    seed_demo_farms()
    farm_id = str(demo_id("farm", "nakuru-maize-field"))

    run = (await client.post(f"{api_prefix}/farms/{farm_id}/analysis")).json()
    image = (
        await client.post(
            f"{api_prefix}/farms/{farm_id}/crop-images",
            files={"file": ("leaf.jpg", io.BytesIO(JPEG_BYTES), "image/jpeg")},
        )
    ).json()

    from app.db.memory import store

    store.reset()

    restored_run = (await client.get(f"{api_prefix}/farms/{farm_id}/analysis/latest")).json()
    restored_image = (await client.get(f"{api_prefix}/crop-images/{image['id']}")).json()

    assert_utc(restored_run, "analysis run")
    assert_utc(restored_image, "crop image")
    assert restored_run["created_at"] == run["created_at"]
    assert restored_image["uploaded_at"] == image["uploaded_at"]


# --------------------------------------------------------------------------
# The helper itself
# --------------------------------------------------------------------------


def test_as_utc_converts_rather_than_relabelling() -> None:
    """An aware non-UTC timestamp — what Postgres returns — must be *converted*, so the
    instant is preserved. Stamping UTC onto it would shift the value by the offset."""
    from datetime import UTC, datetime, timedelta, timezone

    from app.models import as_utc

    ist = timezone(timedelta(hours=5, minutes=30))
    aware = datetime(2026, 8, 23, 1, 6, 22, tzinfo=ist)

    converted = as_utc(aware)

    assert converted.tzinfo is UTC
    assert converted == aware, "the instant must not move"
    assert converted.hour == 19 and converted.day == 22


def test_as_utc_stamps_a_naive_timestamp() -> None:
    """What SQLite returns. Values are written as UTC, so attaching UTC restores what
    was stored rather than guessing."""
    from datetime import UTC, datetime

    from app.models import as_utc

    naive = datetime(2026, 8, 22, 19, 35, 3)

    assert as_utc(naive) == datetime(2026, 8, 22, 19, 35, 3, tzinfo=UTC)
    assert as_utc(None) is None


@pytest.mark.postgres
async def test_postgres_farm_timestamps_are_utc(
    postgres_db, client: AsyncClient, api_prefix: str
) -> None:
    """Postgres returns an aware datetime in the session time zone, which serialised as
    `+05:30` rather than `Z` before `as_utc`. SQLite cannot catch this — its problem is
    the opposite one — so it needs its own tier."""
    created = (await client.post(f"{api_prefix}/farms", json=FARM)).json()
    fetched = (await client.get(f"{api_prefix}/farms/{created['id']}")).json()

    assert_utc(fetched, "postgres farm")
    assert created["created_at"] == fetched["created_at"]
