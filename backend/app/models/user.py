"""Local user records.

Supabase owns authentication; this table owns *ownership*. A row is created the first
time a verified token arrives, keyed on the token's `sub` claim, so a farm can point at
a stable local id rather than at whatever Supabase happens to call the account.

No password, no token, no secret is stored here. The only credential-adjacent value is
`auth_id`, which is a public subject identifier.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class UserORM(Base):
    """A local identity, mapped to a Supabase account by `auth_id`."""

    __tablename__ = "users"

    id: Mapped[Uuid] = mapped_column(Uuid, primary_key=True)

    # The `sub` claim of the verified token. Unique because it is the join key between
    # Supabase's account and this row; two locals rows for one account would split a
    # user's farms in half.
    auth_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)

    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(120))
    locale: Mapped[str | None] = mapped_column(String(10))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
