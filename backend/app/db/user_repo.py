"""Local user rows, keyed on the Supabase subject.

The one operation that matters is :func:`resolve_user_id`: given a verified `sub`,
return the local id that farms are owned by, creating the row on first sight. Supabase
owns the account; this owns the mapping.

Without a database configured there is nothing to write, so the id is derived from the
subject instead. That keeps ownership meaningful on the in-memory path — two different
tokens still get two different owners — without inventing a table the store does not
have.
"""

from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select

from app.db.session import database_enabled, session_scope
from app.models import UserORM

# **The single namespace for every local user id**, demo and authenticated alike.
# Derives a stable id from a Supabase subject when no database is available: identical
# inputs, identical id, on every machine and across restarts — the same reasoning as
# the crop and demo-farm ids in `app.db.seed`.
#
# It lives here rather than beside the demo user in `app.core.deps` because this module
# owns the subject-to-local-id mapping. It was briefly defined in both places with the
# same literal; they agreed, but only by coincidence, and editing one would have forked
# the demo user from its own `users` row with no error to show for it.
USER_NAMESPACE = uuid5(NAMESPACE_URL, "https://terranex.app/users")


def local_id_for(auth_id: str) -> UUID:
    """The derived local id for a Supabase subject."""
    return uuid5(USER_NAMESPACE, auth_id)


def resolve_user_id(*, auth_id: str, email: str | None = None) -> UUID:
    """The local `users.id` for a verified subject, creating the row if needed.

    Idempotent: a returning user resolves to the row they already have, and their email
    is refreshed if Supabase reports a new one.
    """
    if not database_enabled():
        return local_id_for(auth_id)

    with session_scope() as db:
        row = db.scalar(select(UserORM).where(UserORM.auth_id == auth_id))
        if row is None:
            row = UserORM(id=uuid4(), auth_id=auth_id, email=email)
            db.add(row)
            db.flush()
        elif email and row.email != email:
            row.email = email
        return row.id


def ensure_user(*, user_id: UUID, auth_id: str, email: str | None = None) -> UUID:
    """Make sure a row exists with this exact id.

    Used for the demo user, whose id is fixed by derivation rather than assigned by the
    database — a farm cannot reference an owner that does not exist once `farms.user_id`
    is a foreign key.
    """
    if not database_enabled():
        return user_id

    with session_scope() as db:
        row = db.get(UserORM, user_id)
        if row is None:
            existing = db.scalar(select(UserORM).where(UserORM.auth_id == auth_id))
            if existing is not None:
                return existing.id
            db.add(UserORM(id=user_id, auth_id=auth_id, email=email))
        return user_id
