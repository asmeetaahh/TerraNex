"""Phase 5: persisted soil profiles.

Extends the chain — it does not replace anything.

Soil was cached in a process-local `TTLCache` with a thirty-day TTL, which the
implementation could not honour: the cache dies with the process. Every restart
refetched every farm's soil from ISRIC, and a provider outage at that moment degraded
the whole estate to *simulated* values until it recovered.

This is the one external input worth a table. Soil does not change on any timescale the
product cares about, so refetching it is pure cost; weather does change, which is why
there is deliberately no `weather_snapshots` table beside this one.

**`UNIQUE(farm_id)`, upserted.** Soil is a property of a place rather than a time
series, so one row per farm and no history — a history table would accumulate
near-identical rows nothing reads.

**Every measurement is nullable.** SoilGrids returns nothing for open water, ice and
some unmapped terrain, and that is ordinary rather than an error. A `NOT NULL` would
force a fabricated number into a field nobody measured, which would then drive real
irrigation and fertiliser advice.

**Every `SoilObservation` field has a column here, `water_holding_capacity_mm`
included.** It is derived rather than measured, but it feeds the FAO-56
plant-available-water term directly, so omitting it silently changed every irrigation
figure a farm reported after a restart — and moved `inputs_hash`, making each analysis
miss its own cache.

`source`, `mode` and `fetched_at` travel with the values so a served profile reports the
provenance it was *obtained* under. A profile stored under a configured simulator stays
`simulated` on the way out; persistence must not launder a simulation into a
measurement. `fetched_at` is when the provider answered, never when the row was read —
stamping the read time would make every profile permanently fresh.

`ON DELETE CASCADE`: the profile describes the farm's location, so with the farm gone it
describes nothing.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '03e69395803e'
down_revision: Union[str, Sequence[str], None] = 'e8eba059f451'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('soil_profiles',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('farm_id', sa.Uuid(), nullable=False),
    sa.Column('source', sa.String(length=64), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('depth_cm', sa.String(length=20), nullable=False),
    sa.Column('ph', sa.Float(), nullable=True),
    sa.Column('organic_carbon_pct', sa.Float(), nullable=True),
    sa.Column('nitrogen_g_kg', sa.Float(), nullable=True),
    sa.Column('cec_cmol_kg', sa.Float(), nullable=True),
    sa.Column('bulk_density_kg_dm3', sa.Float(), nullable=True),
    sa.Column('sand_pct', sa.Float(), nullable=True),
    sa.Column('silt_pct', sa.Float(), nullable=True),
    sa.Column('clay_pct', sa.Float(), nullable=True),
    sa.Column('texture_class', sa.String(length=20), nullable=True),
    sa.Column('water_holding_capacity_mm', sa.Float(), nullable=True),
    sa.Column('raw', sa.JSON(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("texture_class IS NULL OR texture_class IN ('sand', 'loamy_sand', 'sandy_loam', 'loam', 'silt_loam', 'silt', 'sandy_clay_loam', 'clay_loam', 'silty_clay_loam', 'sandy_clay', 'silty_clay', 'clay')", name=op.f('ck_soil_profiles_texture_class_valid')),
    sa.CheckConstraint('bulk_density_kg_dm3 IS NULL OR bulk_density_kg_dm3 >= 0', name=op.f('ck_soil_profiles_bulk_density_positive')),
    sa.CheckConstraint('cec_cmol_kg IS NULL OR cec_cmol_kg >= 0', name=op.f('ck_soil_profiles_cec_positive')),
    sa.CheckConstraint('clay_pct IS NULL OR (clay_pct >= 0 AND clay_pct <= 100)', name=op.f('ck_soil_profiles_clay_range')),
    sa.CheckConstraint('nitrogen_g_kg IS NULL OR nitrogen_g_kg >= 0', name=op.f('ck_soil_profiles_nitrogen_positive')),
    sa.CheckConstraint('organic_carbon_pct IS NULL OR (organic_carbon_pct >= 0 AND organic_carbon_pct <= 100)', name=op.f('ck_soil_profiles_organic_carbon_range')),
    sa.CheckConstraint('ph IS NULL OR (ph >= 0 AND ph <= 14)', name=op.f('ck_soil_profiles_ph_range')),
    sa.CheckConstraint('sand_pct IS NULL OR (sand_pct >= 0 AND sand_pct <= 100)', name=op.f('ck_soil_profiles_sand_range')),
    sa.CheckConstraint('silt_pct IS NULL OR (silt_pct >= 0 AND silt_pct <= 100)', name=op.f('ck_soil_profiles_silt_range')),
    sa.CheckConstraint('water_holding_capacity_mm IS NULL OR water_holding_capacity_mm >= 0', name=op.f('ck_soil_profiles_water_holding_capacity_positive')),
    sa.ForeignKeyConstraint(['farm_id'], ['farms.id'], name=op.f('fk_soil_profiles_farm_id_farms'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_soil_profiles')),
    sa.UniqueConstraint('farm_id', name=op.f('uq_soil_profiles_farm_id'))
    )


def downgrade() -> None:
    op.drop_table('soil_profiles')
