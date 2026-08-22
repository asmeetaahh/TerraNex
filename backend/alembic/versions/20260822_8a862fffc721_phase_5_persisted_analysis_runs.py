"""Phase 5: persisted analysis runs.

Extends the Phase 2b chain — it does not replace it.

Analysis runs were held in a process-local dict, so a restart emptied every dashboard,
risk panel and advisory list until someone re-ran the analysis. Nothing errored; the
panels simply went blank.

`result` holds the whole validated `AnalysisRun` as JSON. Four fields are promoted to
columns because they are the only ones queried: `created_at` to find the latest,
`overall_health_score` and `overall_band` for sorting and trend charts, and
`inputs_hash` for cache lookup. Normalising the nested advisories and recommendations
into child tables would cost a migration every time a section gains a field and buy
nothing, since nothing queries into them.

`user_id` is denormalised from the farm and **nullable on purpose**, for the same reason
`farms.user_id` is: runs computed before ownership existed, and the in-memory
development path, have no owner. The cascade chain keeps it consistent — deleting a user
deletes their farms, which deletes their runs.

The `(farm_id, inputs_hash)` index is deliberately **not unique**. Two concurrent
requests may compute and insert the same hash; runs are immutable, so the duplicate is
harmless, whereas a unique constraint would turn a benign race into a 500.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a862fffc721'
down_revision: Union[str, Sequence[str], None] = '539ec596be65'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('analysis_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('farm_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('overall_health_score', sa.Integer(), nullable=False),
    sa.Column('overall_band', sa.String(length=20), nullable=False),
    sa.Column('ai_mode', sa.String(length=20), nullable=False),
    sa.Column('inputs_hash', sa.String(length=64), nullable=False),
    sa.Column('engine_version', sa.String(length=20), nullable=False),
    sa.Column('ruleset_version', sa.String(length=64), nullable=False),
    sa.Column('prompt_version', sa.String(length=40), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=True),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('degraded_sources', sa.JSON(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.CheckConstraint("ai_mode IN ('gemini', 'mock', 'fallback')", name=op.f('ck_analysis_runs_ai_mode_valid')),
    sa.CheckConstraint("overall_band IN ('excellent', 'good', 'moderate', 'poor', 'critical')", name=op.f('ck_analysis_runs_overall_band_valid')),
    sa.CheckConstraint("status IN ('complete', 'partial', 'failed')", name=op.f('ck_analysis_runs_status_valid')),
    sa.CheckConstraint('duration_ms >= 0', name=op.f('ck_analysis_runs_duration_ms_positive')),
    sa.CheckConstraint('overall_health_score >= 0 AND overall_health_score <= 100', name=op.f('ck_analysis_runs_overall_health_score_range')),
    sa.ForeignKeyConstraint(['farm_id'], ['farms.id'], name=op.f('fk_analysis_runs_farm_id_farms'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_analysis_runs_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_analysis_runs'))
    )
    with op.batch_alter_table('analysis_runs', schema=None) as batch_op:
        batch_op.create_index('ix_analysis_runs_farm_created', ['farm_id', 'created_at'], unique=False)
        batch_op.create_index('ix_analysis_runs_farm_hash', ['farm_id', 'inputs_hash'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('analysis_runs', schema=None) as batch_op:
        batch_op.drop_index('ix_analysis_runs_farm_hash')
        batch_op.drop_index('ix_analysis_runs_farm_created')

    op.drop_table('analysis_runs')
