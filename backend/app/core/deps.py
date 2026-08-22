"""Shared FastAPI dependencies.

**Identity is resolved here and nowhere else.** A route asks for `CurrentUser` and gets
one; it never reads a header, never decodes a token, and never learns whether auth is
switched on.

Two modes, and the switch between them is `ENABLE_AUTH`:

* **on** — the `Authorization: Bearer <token>` header is required. The token is verified
  against Supabase (see `app.core.security`) and the local `users` row for its `sub` is
  created if this is the account's first request. A missing or unverifiable token is an
  `UnauthorizedError`, which the existing handler renders as the standard envelope.
* **off** — every request is attributed to a fixed demo user. This is the frontend's
  unblock switch and what keeps the offline test suite working; it predates this module
  and is deliberately preserved.

**The dependency is a plain function taking `Request`, not `fastapi.security.HTTPBearer`.**
That is deliberate and load-bearing: a security scheme would add `securitySchemes` and a
per-path `security` block to the generated OpenAPI, and `contracts/openapi.json` is
frozen and consumed by the frontend's generated types. Verified empirically — the
parameter-form dependency leaves the document byte-identical. The cost is that Swagger
shows no "Authorize" button; see `docs/ARCHITECTURE.md`.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import bearer_token, verify_token
from app.db.session import session_scope
from app.db.user_repo import local_id_for

# The subject the demo user is derived from. Its local id comes from `local_id_for`,
# the same function every authenticated subject goes through, so the demo user cannot
# drift from the `users` row it points at — the namespace is defined once, in
# `app.db.user_repo`.
DEMO_AUTH_ID = "demo-user"


@dataclass(frozen=True)
class CurrentUser:
    """The identity a request acts as.

    `id` is the local `users.id` that farms are owned by. `auth_id` is the Supabase
    subject, kept for logging and for resolving the row again.
    """

    id: UUID
    auth_id: str
    email: str | None
    is_demo: bool = False


def demo_user() -> CurrentUser:
    """The identity every request uses while `ENABLE_AUTH` is false."""
    return CurrentUser(
        id=local_id_for(DEMO_AUTH_ID),
        auth_id=DEMO_AUTH_ID,
        email=settings.DEMO_USER_EMAIL,
        is_demo=True,
    )


async def get_current_user(request: Request) -> CurrentUser:
    """The authenticated user, or the demo user when auth is disabled.

    Raises `UnauthorizedError` when auth is enabled and the token is missing, malformed,
    expired, or fails verification.
    """
    if not settings.ENABLE_AUTH:
        return demo_user()

    claims = verify_token(bearer_token(request.headers.get("authorization")))

    from app.db import user_repo

    local_id = user_repo.resolve_user_id(auth_id=claims.subject, email=claims.email)
    return CurrentUser(id=local_id, auth_id=claims.subject, email=claims.email)


def get_db() -> Iterator[Session]:
    """A request-scoped transactional session.

    Yields a session that commits when the request succeeds and rolls back if it
    raises, so a handler cannot half-write a change.
    """
    with session_scope() as session:
        yield session
