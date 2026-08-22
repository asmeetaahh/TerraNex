"""Phase 5: persisted crop images.

Extends the chain — it does not replace anything.

Crop images were held in a process-local dict, so a restart emptied a farm's diagnosis
history while its farms and analysis runs survived. That inconsistency is the reason
this is the last in-memory island to go.

`analysis` holds the whole validated `CropImageAnalysis` as JSON, for the same reason
`analysis_runs.result` does: the payload grows when the vision model lands, and
normalising differentials and treatment options into child tables would cost a migration
per field while nothing queries into them.

**`sha256` is internal and load-bearing.** It seeds the deterministic diagnosis, so the
same photograph always yields the same result. It previously lived in a module-global
dict, which meant it did not survive a restart and re-analysing an image silently
re-seeded from the image's id — a different diagnosis for identical bytes. It is not
unique and not indexed: the same photograph may legitimately be uploaded twice, and
nothing looks an image up by its digest.

**`farm_crop_id` is ON DELETE SET NULL, not CASCADE.** A diagnosis is evidence, so
deleting the planting it was attached to costs the photograph its crop link rather than
its existence. `farm_id` and `user_id` cascade as elsewhere.

No object storage in this phase, so there is no column for the bytes and no URL: the
image is measured and its digest kept, and `CropImage.url` stays null.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8eba059f451'
down_revision: Union[str, Sequence[str], None] = '8a862fffc721'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('crop_images',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('farm_id', sa.Uuid(), nullable=False),
    sa.Column('farm_crop_id', sa.Uuid(), nullable=True),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('content_type', sa.String(length=100), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('analysis_status', sa.String(length=20), nullable=False),
    sa.Column('analysis', sa.JSON(), nullable=True),
    sa.Column('analysis_error', sa.Text(), nullable=True),
    sa.Column('model', sa.String(length=80), nullable=True),
    sa.Column('prompt_version', sa.String(length=40), nullable=True),
    sa.Column('ai_mode', sa.String(length=20), nullable=True),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('analyzed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("ai_mode IS NULL OR ai_mode IN ('gemini', 'mock', 'fallback')", name=op.f('ck_crop_images_ai_mode_valid')),
    sa.CheckConstraint("analysis_status IN ('pending', 'analyzing', 'complete', 'failed')", name=op.f('ck_crop_images_analysis_status_valid')),
    sa.CheckConstraint('height IS NULL OR height >= 0', name=op.f('ck_crop_images_height_positive')),
    sa.CheckConstraint('size_bytes >= 0', name=op.f('ck_crop_images_size_bytes_positive')),
    sa.CheckConstraint('width IS NULL OR width >= 0', name=op.f('ck_crop_images_width_positive')),
    sa.ForeignKeyConstraint(['farm_crop_id'], ['farm_crops.id'], name=op.f('fk_crop_images_farm_crop_id_farm_crops'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['farm_id'], ['farms.id'], name=op.f('fk_crop_images_farm_id_farms'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_crop_images_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_crop_images'))
    )
    with op.batch_alter_table('crop_images', schema=None) as batch_op:
        batch_op.create_index('ix_crop_images_farm_uploaded', ['farm_id', 'uploaded_at'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('crop_images', schema=None) as batch_op:
        batch_op.drop_index('ix_crop_images_farm_uploaded')

    op.drop_table('crop_images')
