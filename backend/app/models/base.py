"""SQLAlchemy declarative base and the conventions every model shares.

Two decisions here are load-bearing.

**A naming convention on the metadata.** Without one, Alembic autogenerates unnamed
constraints and indexes; SQLite then cannot drop them, so a downgrade fails and the
migration is one-way in exactly the environment where you most want to test it. Naming
them makes `alembic downgrade` work on every backend.

**Portable column types.** `sa.Uuid` renders as a native `uuid` on Postgres and as a
`CHAR(32)` on SQLite, and `sa.JSON` maps to each dialect's JSON type. That is what lets
the fast service tests run on SQLite while the production-faithful subset runs on
Postgres, from one set of models.

Enum-valued columns are `String` with a `CHECK`, not a native Postgres `ENUM`. Adding a
member to a native enum needs `ALTER TYPE` in its own migration, SQLite has no enum type
at all, and Pydantic already rejects bad values at the API edge. A `CHECK` gives the
database-level guarantee without either cost.
"""

from datetime import UTC, datetime

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Deterministic names for every constraint and index, so migrations are reversible.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every TerraNex model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    """Timezone-aware creation/update stamp.

    Set in Python rather than with a server default so the value is identical on
    Postgres and SQLite, and so tests can freeze it.
    """
    return datetime.now(UTC)


def enum_values(enum_cls: type) -> list[str]:
    """The permitted string values of a `StrEnum`, for a CHECK constraint."""
    return [member.value for member in enum_cls]
