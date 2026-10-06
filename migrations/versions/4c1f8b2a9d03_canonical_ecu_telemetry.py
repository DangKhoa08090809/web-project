"""Canonical ECU telemetry ownership

Revision ID: 4c1f8b2a9d03
Revises: b8f6d2c91a3a
Create Date: 2026-08-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "4c1f8b2a9d03"
down_revision = "b8f6d2c91a3a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ecu_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_key", sa.String(length=100), nullable=False),
        sa.Column("manufacturer", sa.String(length=100), nullable=False),
        sa.Column("protocol_family", sa.String(length=100), nullable=False),
        sa.Column("frame_length", sa.Integer(), nullable=False),
        sa.Column("active_decoder_version_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ecu_profiles")),
        sa.UniqueConstraint("profile_key", name="uq_ecu_profiles_profile_key"),
    )
    with op.batch_alter_table("ecu_profiles", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ecu_profiles_profile_key"), ["profile_key"], unique=False)

    op.create_table(
        "decoder_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ecu_profile_id", sa.Integer(), nullable=False),
        sa.Column("decoder_id", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("decoder_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ecu_profile_id"], ["ecu_profiles.id"], name=op.f("fk_decoder_versions_ecu_profile_id_ecu_profiles"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decoder_versions")),
        sa.UniqueConstraint("ecu_profile_id", "decoder_id", "version", name="uq_decoder_versions_profile_decoder_version"),
    )
    with op.batch_alter_table("decoder_versions", schema=None) as batch_op:
        batch_op.create_index("ix_decoder_versions_profile_version", ["ecu_profile_id", "version"], unique=False)

    with op.batch_alter_table("ride_sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("vehicle_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("sample_interval_ms", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("sampling_rate_hz", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("decoder_version_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("raw_frame_count", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("valid_frame_count", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("invalid_frame_count", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("checksum_error_count", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("capture_status", sa.String(length=20), server_default="capturing", nullable=False))
        batch_op.add_column(sa.Column("analysis_status", sa.String(length=20), server_default="not_requested", nullable=False))
        batch_op.add_column(sa.Column("analysis_run_id", sa.String(length=100), nullable=True))
        batch_op.create_foreign_key("fk_ride_sessions_vehicle_id_vehicles", "vehicles", ["vehicle_id"], ["id"], ondelete="SET NULL")
        batch_op.create_foreign_key(
            "fk_ride_sessions_decoder_version_id_decoder_versions",
            "decoder_versions",
            ["decoder_version_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("telemetry_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("timestamp_ms", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("tps_voltage", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("tps_raw_candidate", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("battery_voltage", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("iat_c", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("ect_c_candidate", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("map_raw", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("candidate_signals", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("quality_flags", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("frame_valid", sa.Boolean(), server_default=sa.true(), nullable=False))
        batch_op.add_column(sa.Column("checksum_valid", sa.Boolean(), server_default=sa.true(), nullable=False))
        batch_op.add_column(sa.Column("decoder_valid", sa.Boolean(), server_default=sa.true(), nullable=False))

    op.create_table(
        "raw_artifacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ride_session_id", sa.Integer(), nullable=False),
        sa.Column("artifact_type", sa.String(length=40), nullable=False),
        sa.Column("storage_path", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ride_session_id"], ["ride_sessions.id"], name=op.f("fk_raw_artifacts_ride_session_id_ride_sessions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raw_artifacts")),
    )
    with op.batch_alter_table("raw_artifacts", schema=None) as batch_op:
        batch_op.create_index("ix_raw_artifacts_session", ["ride_session_id"], unique=False)

    op.create_table(
        "analysis_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ride_session_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=100), nullable=False),
        sa.Column("model_version", sa.String(length=100), nullable=True),
        sa.Column("feature_schema_version", sa.String(length=100), nullable=True),
        sa.Column("overall_status", sa.String(length=100), nullable=True),
        sa.Column("health_score", sa.Float(), nullable=True),
        sa.Column("anomaly_ratio", sa.Float(), nullable=True),
        sa.Column("anomaly_window_count", sa.Integer(), nullable=True),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ride_session_id"], ["ride_sessions.id"], name=op.f("fk_analysis_results_ride_session_id_ride_sessions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_results")),
        sa.UniqueConstraint("analysis_run_id", name="uq_analysis_results_analysis_run_id"),
    )
    with op.batch_alter_table("analysis_results", schema=None) as batch_op:
        batch_op.create_index("ix_analysis_results_session_analyzed", ["ride_session_id", "analyzed_at"], unique=False)


def downgrade():
    with op.batch_alter_table("analysis_results", schema=None) as batch_op:
        batch_op.drop_index("ix_analysis_results_session_analyzed")
    op.drop_table("analysis_results")

    with op.batch_alter_table("raw_artifacts", schema=None) as batch_op:
        batch_op.drop_index("ix_raw_artifacts_session")
    op.drop_table("raw_artifacts")

    with op.batch_alter_table("telemetry_records", schema=None) as batch_op:
        batch_op.drop_column("decoder_valid")
        batch_op.drop_column("checksum_valid")
        batch_op.drop_column("frame_valid")
        batch_op.drop_column("quality_flags")
        batch_op.drop_column("candidate_signals")
        batch_op.drop_column("map_raw")
        batch_op.drop_column("ect_c_candidate")
        batch_op.drop_column("iat_c")
        batch_op.drop_column("battery_voltage")
        batch_op.drop_column("tps_raw_candidate")
        batch_op.drop_column("tps_voltage")
        batch_op.drop_column("timestamp_ms")

    with op.batch_alter_table("ride_sessions", schema=None) as batch_op:
        batch_op.drop_constraint("fk_ride_sessions_decoder_version_id_decoder_versions", type_="foreignkey")
        batch_op.drop_constraint("fk_ride_sessions_vehicle_id_vehicles", type_="foreignkey")
        batch_op.drop_column("analysis_run_id")
        batch_op.drop_column("analysis_status")
        batch_op.drop_column("capture_status")
        batch_op.drop_column("checksum_error_count")
        batch_op.drop_column("invalid_frame_count")
        batch_op.drop_column("valid_frame_count")
        batch_op.drop_column("raw_frame_count")
        batch_op.drop_column("decoder_version_id")
        batch_op.drop_column("sampling_rate_hz")
        batch_op.drop_column("sample_interval_ms")
        batch_op.drop_column("vehicle_id")

    with op.batch_alter_table("decoder_versions", schema=None) as batch_op:
        batch_op.drop_index("ix_decoder_versions_profile_version")
    op.drop_table("decoder_versions")

    with op.batch_alter_table("ecu_profiles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ecu_profiles_profile_key"))
    op.drop_table("ecu_profiles")
