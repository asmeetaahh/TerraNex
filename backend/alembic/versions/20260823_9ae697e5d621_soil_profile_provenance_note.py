"""Soil profile provenance note.

`DataSourceMeta` carries four provenance fields; `soil_profiles` persisted three of them
and rebuilt the fourth on read. The rebuilt note described *storage* — "re-fetched only
after its retention window" — rather than the values, so the same farm reported a
different qualifier depending on whether a database was configured.

For SoilGrids it dropped "A model, not a laboratory result", the one caveat that stops a
250 m modelled prediction being read as a soil test. That is the reason this column
exists: the note qualifies the values, so it has to travel with them.

Nullable, because a provider may legitimately return no qualifier — and because
back-filling a note onto rows written before this column would mean inventing one, which
is the defect it fixes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9ae697e5d621'
down_revision: Union[str, Sequence[str], None] = '03e69395803e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('soil_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('note', sa.Text(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('soil_profiles', schema=None) as batch_op:
        batch_op.drop_column('note')

