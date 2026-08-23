"""Crop image bytes for diagnosis.

Upload and diagnosis are separate requests. The uploaded bytes were validated, measured
and then discarded, so by the time `POST /crop-images/{id}/analyze` ran the photograph
no longer existed anywhere and no model could be shown it. This column is where the
pixels survive between the two.

Stored downscaled — at most 1568 px on the long edge, re-encoded as JPEG. That is the
resolution ceiling a vision model works to, so nothing diagnostic is lost, and it keeps
a row in the hundreds of kilobytes rather than the ten megabytes `MAX_UPLOAD_MB` allows.
`size_bytes`, `width` and `height` still describe the original upload: they report what
the user sent, not what was kept.

Nullable on purpose. Rows written before this column have no pixels and must stay
readable; they simply cannot be re-diagnosed from the image. Back-filling would mean
inventing one, and there is nothing to invent it from.

`LargeBinary` renders as BYTEA on PostgreSQL and BLOB on SQLite, so both supported
backends store it natively. Nothing serves these bytes to a client — there is still no
object storage, and `CropImage.url` stays null.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3ec4eabebe67'
down_revision: Union[str, Sequence[str], None] = '9ae697e5d621'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('crop_images', schema=None) as batch_op:
        batch_op.add_column(sa.Column('image_bytes', sa.LargeBinary(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('crop_images', schema=None) as batch_op:
        batch_op.drop_column('image_bytes')
