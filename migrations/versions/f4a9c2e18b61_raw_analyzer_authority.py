"""Raw analyzer authority metadata

Revision ID: f4a9c2e18b61
Revises: d3e5c1a7b902
Create Date: 2026-09-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "f4a9c2e18b61"
down_revision = "d3e5c1a7b902"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ride_sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("raw_representation", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("transport_profile_id", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("ecu_profile_id", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("decoder_id", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("decoder_version", sa.String(length=40), nullable=True))

    with op.batch_alter_table("telemetry_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("raw_hex", sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column("raw_length", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("raw_representation", sa.String(length=40), nullable=True))


def downgrade():
    with op.batch_alter_table("telemetry_records", schema=None) as batch_op:
        batch_op.drop_column("raw_representation")
        batch_op.drop_column("raw_length")
        batch_op.drop_column("raw_hex")

    with op.batch_alter_table("ride_sessions", schema=None) as batch_op:
        batch_op.drop_column("decoder_version")
        batch_op.drop_column("decoder_id")
        batch_op.drop_column("ecu_profile_id")
        batch_op.drop_column("transport_profile_id")
        batch_op.drop_column("raw_representation")
