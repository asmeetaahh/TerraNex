"""Alembic environment.

The connection string comes from `app.core.config.settings`, never from `alembic.ini`.
That file is committed; a Postgres URL carries a password. Keeping the URL in settings
means Alembic reads the same gitignored `.env` the application does, and a credential
cannot reach the repository through a migration config.

`render_as_batch` is on so migrations work on SQLite as well as Postgres. SQLite cannot
`ALTER TABLE ... DROP CONSTRAINT`; batch mode rebuilds the table instead. Combined with
the naming convention on `Base.metadata`, that is what makes `alembic downgrade`
testable in the fast, database-free tier rather than only against a real Postgres.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings

# Importing the package registers every model on Base.metadata. Import the package
# rather than the modules so a new model cannot be omitted from autogeneration.
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    if not settings.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Alembic needs a target database — export it or "
            "put it in backend/.env (which is gitignored)."
        )
    return settings.DATABASE_URL


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting, for review or manual application."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live connection."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
