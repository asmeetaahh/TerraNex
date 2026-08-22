"""The crop reference catalog.

Read-only global data, seeded from `app/db/fixtures/crops.json`. Nothing client-facing
writes it, which is why there are no timestamps: an entry is created by seeding and
changed only by editing the fixture.

`id` is **not** generated here. It is `uuid5(CROP_NAMESPACE, code)`, derived in
`app.db.seed`, so the same crop has the same id on every machine, across restarts, and
before and after this table existed. A frontend fixture that hardcodes a `crop_id`
keeps working — which is the whole reason the derivation was chosen in Phase 3.
"""

from sqlalchemy import CheckConstraint, Float, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base, enum_values
from app.schemas.enums import CropCategory, DroughtTolerance, Season


class CropORM(Base):
    """A crop in the reference catalog, with the agronomic constants the risk engine
    reads. Mirrors `app.schemas.crop.Crop` field for field."""

    __tablename__ = "crops"
    __table_args__ = (
        CheckConstraint(
            "category IN ({})".format(", ".join(f"'{v}'" for v in enum_values(CropCategory))),
            name="category_valid",
        ),
        CheckConstraint(
            "season IN ({})".format(", ".join(f"'{v}'" for v in enum_values(Season))),
            name="season_valid",
        ),
        CheckConstraint(
            "drought_tolerance IS NULL OR drought_tolerance IN ({})".format(
                ", ".join(f"'{v}'" for v in enum_values(DroughtTolerance))
            ),
            name="drought_tolerance_valid",
        ),
        CheckConstraint("ph_min IS NULL OR (ph_min >= 0 AND ph_min <= 14)", name="ph_min_range"),
        CheckConstraint("ph_max IS NULL OR (ph_max >= 0 AND ph_max <= 14)", name="ph_max_range"),
    )

    id: Mapped[Uuid] = mapped_column(Uuid, primary_key=True)

    # Position in the fixture, so `GET /reference/crops` returns the catalog in the
    # order it was curated — staples first — rather than alphabetically. The in-memory
    # store got this free from dict insertion order; a table has no inherent order, and
    # the frontend renders this list straight into a dropdown, so losing it would be a
    # visible change nobody asked for.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    scientific_name: Mapped[str | None] = mapped_column(String(120))

    category: Mapped[str] = mapped_column(String(30), nullable=False)
    season: Mapped[str] = mapped_column(String(30), nullable=False)

    base_temp_c: Mapped[float | None] = mapped_column(Float)
    optimal_temp_min_c: Mapped[float | None] = mapped_column(Float)
    optimal_temp_max_c: Mapped[float | None] = mapped_column(Float)
    gdd_to_maturity: Mapped[float | None] = mapped_column(Float)
    water_need_mm_season: Mapped[float | None] = mapped_column(Float)
    ph_min: Mapped[float | None] = mapped_column(Float)
    ph_max: Mapped[float | None] = mapped_column(Float)
    drought_tolerance: Mapped[str | None] = mapped_column(String(20))

    # Short lists that nothing queries into, so a portable JSON column rather than a
    # Postgres-only JSONB or a join table.
    preferred_textures: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    common_diseases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
