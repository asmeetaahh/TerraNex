"""Persisted soil profiles.

Soil was cached in a process-local `TTLCache` with a thirty-day TTL — a durability the
implementation could not deliver, because the cache dies with the process. Every restart
therefore refetched every farm's soil from ISRIC, and if ISRIC happened to be
unreachable at that moment the whole estate degraded to *simulated* soil until it came
back.

That is why this table exists and `weather_snapshots` does not: soil is the one external
input that genuinely does not change on any timescale this product cares about, so
refetching it is pure cost. Weather has to be refetched because it changed.

**One row per farm.** `UNIQUE(farm_id)` with upsert on write: soil is a property of a
place, not a time series, and a history table would accumulate near-identical rows that
nothing reads.

**Every measurement is nullable.** SoilGrids returns nothing for open water, ice and
some unmapped terrain, and `SoilObservation` treats that as ordinary rather than as an
error. A `NOT NULL` here would force a fabricated number into a field nobody measured,
which would then drive real irrigation and fertiliser advice.

`fetched_at` is the moment the *provider* answered, never the moment the row was read.
Storing the read time would make the profile permanently fresh and it would never expire.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base, enum_values, utcnow
from app.schemas.enums import SoilTexture


def _one_of(column: str, enum_cls: type) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{v}'" for v in enum_values(enum_cls)))


class SoilProfileORM(Base):
    """The stored soil profile for one farm."""

    __tablename__ = "soil_profiles"
    __table_args__ = (
        CheckConstraint(
            f"texture_class IS NULL OR {_one_of('texture_class', SoilTexture)}",
            name="texture_class_valid",
        ),
        CheckConstraint("ph IS NULL OR (ph >= 0 AND ph <= 14)", name="ph_range"),
        CheckConstraint(
            "organic_carbon_pct IS NULL OR (organic_carbon_pct >= 0 AND organic_carbon_pct <= 100)",
            name="organic_carbon_range",
        ),
        CheckConstraint(
            "sand_pct IS NULL OR (sand_pct >= 0 AND sand_pct <= 100)", name="sand_range"
        ),
        CheckConstraint(
            "silt_pct IS NULL OR (silt_pct >= 0 AND silt_pct <= 100)", name="silt_range"
        ),
        CheckConstraint(
            "clay_pct IS NULL OR (clay_pct >= 0 AND clay_pct <= 100)", name="clay_range"
        ),
        CheckConstraint("nitrogen_g_kg IS NULL OR nitrogen_g_kg >= 0", name="nitrogen_positive"),
        CheckConstraint("cec_cmol_kg IS NULL OR cec_cmol_kg >= 0", name="cec_positive"),
        CheckConstraint(
            "bulk_density_kg_dm3 IS NULL OR bulk_density_kg_dm3 >= 0",
            name="bulk_density_positive",
        ),
        CheckConstraint(
            "water_holding_capacity_mm IS NULL OR water_holding_capacity_mm >= 0",
            name="water_holding_capacity_positive",
        ),
    )

    id: Mapped[Uuid] = mapped_column(Uuid, primary_key=True)

    # Unique, so the row *is* the farm's profile. Upsert rather than insert on refresh;
    # see the module docstring for why there is no history.
    farm_id: Mapped[Uuid] = mapped_column(
        Uuid, ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # ---- provenance ----
    #
    # Kept so a served profile can report the source that actually produced it rather
    # than whichever provider is configured now. `mode` records how the values were
    # obtained originally — a simulated fallback must never be resurrected as though it
    # were a measurement.
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    depth_cm: Mapped[str] = mapped_column(String(20), nullable=False, default="0-30")

    # ---- measurements, every one optional ----
    ph: Mapped[float | None] = mapped_column(Float)
    organic_carbon_pct: Mapped[float | None] = mapped_column(Float)
    nitrogen_g_kg: Mapped[float | None] = mapped_column(Float)
    cec_cmol_kg: Mapped[float | None] = mapped_column(Float)
    bulk_density_kg_dm3: Mapped[float | None] = mapped_column(Float)
    sand_pct: Mapped[float | None] = mapped_column(Float)
    silt_pct: Mapped[float | None] = mapped_column(Float)
    clay_pct: Mapped[float | None] = mapped_column(Float)
    texture_class: Mapped[str | None] = mapped_column(String(20))

    #: Plant-available water in the profile. Derived by the provider rather than
    #: measured, but it feeds the FAO-56 water balance directly, so dropping it on the
    #: way through storage silently changes every irrigation figure the farm reports.
    water_holding_capacity_mm: Mapped[float | None] = mapped_column(Float)

    #: The normalised observation as it reached the service layer, kept for debugging
    #: and reprocessing without a refetch.
    #:
    #: Not the provider's own HTTP body: that is consumed inside `app/providers/soil.py`
    #: and never leaves it, and `app/providers/` may not import `app.db`. Every field
    #: the rest of the system ever saw is here.
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
