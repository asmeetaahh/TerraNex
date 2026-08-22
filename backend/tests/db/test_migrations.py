"""Alembic migrations.

The migration is the schema's only source of truth in a deployed environment —
`create_all` is never run there — so it needs the same standard of proof as the code.
Three properties matter:

* **upgrade** builds every table the models declare,
* **downgrade** removes them again, which is what makes a bad deploy recoverable,
* the migration and the models **do not drift**, which is what stops a column existing
  in one and not the other.

These run on a throwaway SQLite file, so they need no database and no network. The
Postgres-specific behaviour they cannot cover lives in `test_postgres.py`.
"""

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from alembic import command
from app.models import Base

BACKEND_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {"crops", "farms", "farm_crops"}


@pytest.fixture
def alembic_config(tmp_path, monkeypatch) -> Config:
    """Alembic pointed at a fresh SQLite file.

    `env.py` reads the URL from settings rather than `alembic.ini`, so redirecting the
    setting is all it takes — and it proves that indirection works.
    """
    from app.core.config import settings

    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setattr(settings, "DATABASE_URL", database_url)

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture
def database_url(alembic_config) -> str:
    from app.core.config import settings

    return settings.DATABASE_URL


def test_upgrade_creates_every_table(alembic_config, database_url) -> None:
    command.upgrade(alembic_config, "head")

    tables = set(inspect(create_engine(database_url)).get_table_names())

    assert tables >= EXPECTED_TABLES, f"missing {EXPECTED_TABLES - tables}"
    assert "alembic_version" in tables


def test_downgrade_removes_every_table(alembic_config, database_url) -> None:
    """A migration you cannot reverse is a deploy you cannot roll back."""
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")

    tables = set(inspect(create_engine(database_url)).get_table_names())

    assert not (EXPECTED_TABLES & tables), f"left behind {EXPECTED_TABLES & tables}"


def test_upgrade_downgrade_upgrade_round_trips(alembic_config, database_url) -> None:
    """The cycle a rollback-and-retry actually performs."""
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    tables = set(inspect(create_engine(database_url)).get_table_names())

    assert tables >= EXPECTED_TABLES


def test_migration_matches_the_models(alembic_config, database_url) -> None:
    """No drift.

    After upgrading, comparing the live schema against `Base.metadata` must find
    nothing. A column added to a model without a migration would show up here rather
    than as a runtime error against a deployed database.
    """
    command.upgrade(alembic_config, "head")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        differences = compare_metadata(context, Base.metadata)

    assert differences == [], f"models and migration have drifted: {differences}"


def test_the_committed_config_carries_no_credentials() -> None:
    """`alembic.ini` is committed and a connection string carries a password, so the
    URL must come from settings. This fails if someone pastes one back in."""
    text = (BACKEND_ROOT / "alembic.ini").read_text(encoding="utf-8")

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        assert not stripped.startswith("sqlalchemy.url"), (
            "alembic.ini sets sqlalchemy.url; the URL must come from settings so a "
            "credential cannot be committed"
        )
