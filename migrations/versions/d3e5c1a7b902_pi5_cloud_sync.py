"""Pi5 cloud sync canonical v2 fields

Revision ID: d3e5c1a7b902
Revises: 6c8f20d47319
Create Date: 2026-09-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "d3e5c1a7b902"
down_revision = "6c8f20d47319"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ride_sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("telemetry_schema_version", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("source_type", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("capture_started_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("capture_ended_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("duration_ms", sa.Float(), nullable=True))

    with op.batch_alter_table("telemetry_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tps_raw", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("ect_c", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("record_fingerprint", sa.String(length=64), nullable=True))

    with op.batch_alter_table("analysis_results", schema=None) as batch_op:
        batch_op.add_column(sa.Column("telemetry_schema_version", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("ecu_profile_id", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("decoder_id", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("decoder_version", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("signal_columns", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("result_fingerprint", sa.String(length=64), nullable=True))


def downgrade():
    with op.batch_alter_table("analysis_results", schema=None) as batch_op:
        batch_op.drop_column("result_fingerprint")
        batch_op.drop_column("signal_columns")
        batch_op.drop_column("decoder_version")
        batch_op.drop_column("decoder_id")
        batch_op.drop_column("ecu_profile_id")
        batch_op.drop_column("telemetry_schema_version")

    with op.batch_alter_table("telemetry_records", schema=None) as batch_op:
        batch_op.drop_column("record_fingerprint")
        batch_op.drop_column("ect_c")
        batch_op.drop_column("tps_raw")

    with op.batch_alter_table("ride_sessions", schema=None) as batch_op:
        batch_op.drop_column("duration_ms")
        batch_op.drop_column("capture_ended_at")
        batch_op.drop_column("capture_started_at")
        batch_op.drop_column("source_type")
        batch_op.drop_column("telemetry_schema_version")
