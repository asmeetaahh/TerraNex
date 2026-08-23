"""Database-backed reads and writes for soil profiles.

Mirrors `analysis_repo` and `image_repo`: takes and returns plain values — a
`SoilObservation` plus its provenance — never ORM objects, so a detached instance can
never reach the service layer.

**Ownership is resolved by the caller.** A profile is addressed by farm, and every
caller has already passed `require_farm`, so there is no `user_id` column and no
per-user filter here. Unlike runs and images there is no read path that starts from the
profile's own id.

**One row per farm, upserted.** `upsert_profile` overwrites in place rather than
inserting, which is what keeps `get_profile` single-valued without an ordering clause.
"""

from dataclasses import asdict
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from app.db.session import session_scope
from app.models import SoilProfileORM, as_utc
from app.providers.base import SoilObservation
from app.schemas.common import DataMode
from app.schemas.enums import SoilTexture

#: Numeric columns copied straight from a `SoilObservation`.
#:
#: Listed rather than inferred so a new observation field cannot silently start being
#: persisted — but the omission is the dangerous direction, not the addition. Leaving
#: `water_holding_capacity_mm` out of the first draft of this tuple dropped it on every
#: round trip, which changed the FAO-56 water balance and, because the observation is
#: part of `inputs_hash`, made every analysis miss its own cache. `test_every_soil_field
#: _round_trips` exists so the next omission fails loudly instead.
MEASUREMENTS = (
    "ph",
    "organic_carbon_pct",
    "nitrogen_g_kg",
    "cec_cmol_kg",
    "bulk_density_kg_dm3",
    "sand_pct",
    "silt_pct",
    "clay_pct",
    "water_holding_capacity_mm",
)


class StoredProfile:
    """One farm's stored soil, with the provenance it was fetched under.

    Carries all four `DataSourceMeta` fields — `note` included. Persisting only three
    and rebuilding the fourth on read is what made a stored profile describe its own
    storage instead of its values.

    A small class rather than a tuple because the caller needs every part and positional
    unpacking of five values at a call site reads badly.
    """

    __slots__ = ("observation", "source", "mode", "fetched_at", "note")

    def __init__(
        self,
        observation: SoilObservation,
        source: str,
        mode: str,
        fetched_at: datetime,
        note: str | None = None,
    ) -> None:
        self.observation = observation
        self.source = source
        self.mode = mode
        self.fetched_at = fetched_at
        self.note = note

    def is_fresh(self, now: datetime, ttl_seconds: int) -> bool:
        """Whether this profile may still be served without asking the provider.

        A non-positive TTL disables expiry entirely, matching
        `analysis_repo.cache_cutoff`.
        """
        if ttl_seconds <= 0:
            return True
        return self.fetched_at >= now - timedelta(seconds=ttl_seconds)


def _as_observation(row: SoilProfileORM) -> SoilObservation:
    """Rebuild the observation exactly as the provider produced it.

    `texture_class` is coerced back to `SoilTexture` rather than left as the stored
    string: the live path yields the enum, and a stored profile that differed only by
    the *type* of this field would still be a different value to anything comparing
    observations — including `inputs_hash`.
    """
    return SoilObservation(
        **{name: getattr(row, name) for name in MEASUREMENTS},
        texture_class=SoilTexture(row.texture_class) if row.texture_class else None,
    )


def get_profile(farm_id: UUID) -> StoredProfile | None:
    """The stored profile for a farm, or None."""
    with session_scope() as db:
        row = db.scalars(select(SoilProfileORM).where(SoilProfileORM.farm_id == farm_id)).first()
        if row is None:
            return None
        return StoredProfile(
            observation=_as_observation(row),
            source=row.source,
            mode=row.mode,
            # Normalised on read: SQLite hands back a naive datetime, so comparing it
            # against an aware `now` for freshness would raise rather than expire.
            fetched_at=as_utc(row.fetched_at),
            note=row.note,
        )


def upsert_profile(
    farm_id: UUID,
    observation: SoilObservation,
    *,
    source: str,
    mode: DataMode | str,
    fetched_at: datetime,
    note: str | None = None,
    depth_cm: str = "0-30",
) -> None:
    """Store or replace a farm's soil profile.

    `fetched_at` is passed in rather than defaulted to now, because it is the moment the
    *provider* answered. Stamping the write time would make every profile permanently
    fresh and nothing would ever expire.

    `note` is stored verbatim for the same reason the other three provenance fields are:
    a served profile must qualify its values the way the provider qualified them.
    """
    values = {name: getattr(observation, name) for name in MEASUREMENTS}
    texture = observation.texture_class
    now = datetime.now(fetched_at.tzinfo) if fetched_at.tzinfo else datetime.now()

    with session_scope() as db:
        row = db.scalars(select(SoilProfileORM).where(SoilProfileORM.farm_id == farm_id)).first()

        if row is None:
            row = SoilProfileORM(id=uuid4(), farm_id=farm_id)
            db.add(row)

        for name, value in values.items():
            setattr(row, name, value)
        row.texture_class = str(texture) if texture is not None else None
        row.source = str(source)
        row.mode = str(mode)
        row.fetched_at = fetched_at
        row.note = note
        row.depth_cm = depth_cm
        row.raw = _raw_payload(observation)
        row.updated_at = now


def _raw_payload(observation: SoilObservation) -> dict:
    """The observation as stored for debugging and reprocessing.

    `asdict` keeps this in step with `SoilObservation` automatically, unlike the
    explicit `MEASUREMENTS` tuple above — which is deliberate. The columns are a
    contract that should not change silently; this blob is a record of what arrived.
    """
    payload = asdict(observation)
    texture = payload.get("texture_class")
    if texture is not None:
        payload["texture_class"] = str(texture)
    return payload


def delete_for_farm(farm_id: UUID) -> None:
    """Drop a farm's profile. Used when a farm moves far enough to invalidate it."""
    with session_scope() as db:
        row = db.scalars(select(SoilProfileORM).where(SoilProfileORM.farm_id == farm_id)).first()
        if row is not None:
            db.delete(row)


def count_profiles() -> int:
    with session_scope() as db:
        return len(db.scalars(select(SoilProfileORM.id)).all())
