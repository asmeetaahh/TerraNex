"""Database engine and session management.

**The database is optional.** When `DATABASE_URL` is unset the engine is never created
and :func:`database_enabled` returns False, which is what lets services keep using the
in-memory store. Three things depend on that: the test suite runs with no database and
no network, a developer can clone and boot the API with nothing provisioned, and the
migration from memory to Postgres can proceed one service at a time with the whole
suite green in between.

Sessions are synchronous. Every service function in this codebase is sync, and the
routes that call them are `async def` handlers that FastAPI already runs in a
threadpool where needed. Making the data layer async would change every service and
route signature — a far larger change than swapping the storage behind them, and one
that would touch `app/api/`, which this phase does not.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def database_enabled() -> bool:
    """Whether a database is configured.

    The single switch every caller reads. False means the in-memory store is still
    the source of truth.
    """
    return bool(settings.DATABASE_URL)


def get_engine() -> Engine:
    """The process-wide engine, created lazily on first use."""
    global _engine
    if _engine is None:
        if not settings.DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is not configured; call database_enabled() before get_engine()."
            )
        _engine = create_engine(
            settings.DATABASE_URL,
            # Verify a pooled connection before handing it out. Managed Postgres
            # closes idle connections, and without this the first request after a
            # quiet period fails rather than reconnecting.
            pool_pre_ping=True,
            future=True,
        )
        logger.info("database_engine_created", extra={"dialect": _engine.dialect.name})
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """A transactional scope: commit on success, roll back on any exception.

    Services use this rather than managing sessions themselves, so a failure halfway
    through a multi-statement operation cannot leave a half-written farm behind.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_engine() -> None:
    """Drop the engine and its pool. Called on shutdown, and by tests that repoint
    the database between cases."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
