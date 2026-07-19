"""Provisioning and sync acknowledgements

Revision ID: 9d2a8f7c4e11
Revises: 30587f147105
Create Date: 2026-07-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "9d2a8f7c4e11"
down_revision = "30587f147105"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.add_column(sa.Column("firmware_version", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("hardware_version", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("paired_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "device_pairing_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], name=op.f("fk_device_pairing_codes_device_id_devices"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_device_pairing_codes_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_pairing_codes")),
        sa.UniqueConstraint("code_hash", name="uq_device_pairing_codes_code_hash"),
    )
    with op.batch_alter_table("device_pairing_codes", schema=None) as batch_op:
        batch_op.create_index("ix_device_pairing_codes_user_expires", ["user_id", "expires_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_device_pairing_codes_user_id"), ["user_id"], unique=False)

    with op.batch_alter_table("ride_sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("first_seq", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("last_seq", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("sync_status", sa.String(length=20), server_default="syncing", nullable=False))
        batch_op.add_column(
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False)
        )

    with op.batch_alter_table("telemetry_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("device_time_ms", sa.BigInteger(), nullable=True))
        batch_op.add_column(
            sa.Column("server_received_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False)
        )
        batch_op.alter_column("timestamp", existing_type=sa.DateTime(timezone=True), nullable=True)

    op.create_table(
        "sync_batches",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("ride_session_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.String(length=100), nullable=True),
        sa.Column("first_seq", sa.BigInteger(), nullable=True),
        sa.Column("last_seq", sa.BigInteger(), nullable=True),
        sa.Column("received_count", sa.Integer(), nullable=False),
        sa.Column("inserted_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], name=op.f("fk_sync_batches_device_id_devices"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["ride_session_id"], ["ride_sessions.id"], name=op.f("fk_sync_batches_ride_session_id_ride_sessions"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_batches")),
    )
    with op.batch_alter_table("sync_batches", schema=None) as batch_op:
        batch_op.create_index("ix_sync_batches_device_received", ["device_id", "received_at"], unique=False)
        batch_op.create_index("ix_sync_batches_session_received", ["ride_session_id", "received_at"], unique=False)


def downgrade():
    with op.batch_alter_table("sync_batches", schema=None) as batch_op:
        batch_op.drop_index("ix_sync_batches_session_received")
        batch_op.drop_index("ix_sync_batches_device_received")
    op.drop_table("sync_batches")

    with op.batch_alter_table("telemetry_records", schema=None) as batch_op:
        batch_op.alter_column("timestamp", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.drop_column("server_received_at")
        batch_op.drop_column("device_time_ms")

    with op.batch_alter_table("ride_sessions", schema=None) as batch_op:
        batch_op.drop_column("updated_at")
        batch_op.drop_column("sync_status")
        batch_op.drop_column("last_seq")
        batch_op.drop_column("first_seq")

    with op.batch_alter_table("device_pairing_codes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_device_pairing_codes_user_id"))
        batch_op.drop_index("ix_device_pairing_codes_user_expires")
    op.drop_table("device_pairing_codes")

    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.drop_column("paired_at")
        batch_op.drop_column("hardware_version")
        batch_op.drop_column("firmware_version")
