"""Farms and their plantings.

These mirror the `FarmRecord` and `FarmCropRecord` dataclasses in `app.db.memory`
field for field, so the migration is a change of storage and nothing else. No column
is added, renamed, or dropped relative to what the API already returns.

`user_id` is the ownership key. It is **nullable**: farms created before authentication
existed have no owner, and the in-memory development path has no `users` table at all.
A null owner is only ever readable by the demo user, so an unowned row cannot leak into
an authenticated account.

`crop_count` and `has_analysis` are deliberately absent. Both are derived at read time
today — a count of plantings and the existence of an analysis run — and storing them
would let them go stale. They stay derived, as a `COUNT` and an `EXISTS`.
"""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, enum_values, utcnow
from app.schemas.enums import CropStatus, FarmingPractice, GrowthStage, IrrigationType


def _one_of(column: str, enum_cls: type) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{v}'" for v in enum_values(enum_cls)))


class FarmORM(Base):
    """A registered farm. Soft-deleted: `deleted_at` is set rather than the row
    removed, so an analysis run that referenced it still resolves."""

    __tablename__ = "farms"
    __table_args__ = (
        CheckConstraint(_one_of("irrigation_type", IrrigationType), name="irrigation_type_valid"),
        CheckConstraint(
            _one_of("farming_practice", FarmingPractice), name="farming_practice_valid"
        ),
        CheckConstraint("latitude >= -90 AND latitude <= 90", name="latitude_range"),
        CheckConstraint("longitude >= -180 AND longitude <= 180", name="longitude_range"),
        CheckConstraint(
            "area_hectares IS NULL OR area_hectares > 0", name="area_hectares_positive"
        ),
        Index("ix_farms_deleted_at", "deleted_at"),
    )

    id: Mapped[Uuid] = mapped_column(Uuid, primary_key=True)

    # Set server-side from the verified token, never from the request body. Indexed
    # because every farm read is scoped by it.
    user_id: Mapped[Uuid | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # numeric(9, 6) is ~0.1 m of precision and is what the plan specifies. Coordinates
    # are the one field a farm cannot afford to have rounded — every provider call and
    # every downstream figure is derived from them.
    #
    # `asdecimal=False` matters: by default SQLAlchemy returns `Numeric` as `Decimal`,
    # which would reach the simulator's float arithmetic and the `float`-typed API
    # schema. The column keeps its precision; only the Python type is pinned. This is a
    # result-processing flag, so the emitted DDL is unchanged.
    latitude: Mapped[float] = mapped_column(Numeric(9, 6, asdecimal=False), nullable=False)
    longitude: Mapped[float] = mapped_column(Numeric(9, 6, asdecimal=False), nullable=False)

    area_hectares: Mapped[float | None] = mapped_column(Float)
    country_code: Mapped[str | None] = mapped_column(String(2))
    region: Mapped[str | None] = mapped_column(String(120))
    elevation_m: Mapped[float | None] = mapped_column(Float)

    irrigation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    farming_practice: Mapped[str] = mapped_column(String(20), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    plantings: Mapped[list["FarmCropORM"]] = relationship(
        back_populates="farm",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class FarmCropORM(Base):
    """This farm growing that crop on these dates."""

    __tablename__ = "farm_crops"
    __table_args__ = (
        CheckConstraint(_one_of("growth_stage", GrowthStage), name="growth_stage_valid"),
        CheckConstraint(_one_of("status", CropStatus), name="status_valid"),
        CheckConstraint(
            "area_hectares IS NULL OR area_hectares > 0", name="area_hectares_positive"
        ),
        CheckConstraint(
            "planting_date IS NULL OR expected_harvest_date IS NULL "
            "OR expected_harvest_date >= planting_date",
            name="harvest_after_planting",
        ),
        Index("ix_farm_crops_farm_id", "farm_id"),
    )

    id: Mapped[Uuid] = mapped_column(Uuid, primary_key=True)
    farm_id: Mapped[Uuid] = mapped_column(
        Uuid, ForeignKey("farms.id", ondelete="CASCADE"), nullable=False
    )
    crop_id: Mapped[Uuid] = mapped_column(Uuid, ForeignKey("crops.id"), nullable=False)

    planting_date: Mapped[date | None] = mapped_column(Date)
    expected_harvest_date: Mapped[date | None] = mapped_column(Date)
    growth_stage: Mapped[str] = mapped_column(String(20), nullable=False)
    area_hectares: Mapped[float | None] = mapped_column(Float)

    # At most one primary per farm. Enforced in the service layer rather than by a
    # partial unique index, because demoting the incumbent and promoting the new one
    # happen in one transaction and a database-level constraint would fire mid-swap.
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    farm: Mapped[FarmORM] = relationship(back_populates="plantings")
