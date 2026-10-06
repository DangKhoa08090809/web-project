"""Live mode control commands

Revision ID: 6c8f20d47319
Revises: 4c1f8b2a9d03
Create Date: 2026-08-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "6c8f20d47319"
down_revision = "4c1f8b2a9d03"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "device_control_runtimes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("current_boot_id", sa.String(length=100), nullable=False),
        sa.Column("last_control_poll_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_live_mode", sa.Boolean(), nullable=False),
        sa.Column("firmware_version", sa.String(length=100), nullable=True),
        sa.Column("uptime_ms", sa.BigInteger(), nullable=True),
        sa.Column("last_command_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], name=op.f("fk_device_control_runtimes_device_id_devices"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_control_runtimes")),
        sa.UniqueConstraint("device_id", name="uq_device_control_runtimes_device_id"),
    )
    with op.batch_alter_table("device_control_runtimes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_device_control_runtimes_device_id"), ["device_id"], unique=False)

    op.create_table(
        "device_control_commands",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
        sa.Column("command_id", sa.String(length=100), nullable=False),
        sa.Column("boot_id", sa.String(length=100), nullable=False),
        sa.Column("command_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_live_mode", sa.Boolean(), nullable=True),
        sa.Column("status_message", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "command_type IN ('ENABLE_LIVE_MODE', 'DISABLE_LIVE_MODE')",
            name=op.f("ck_device_control_commands_valid_device_control_command_type"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'applied', 'rejected', 'failed', 'superseded', 'stale')",
            name=op.f("ck_device_control_commands_valid_device_control_command_status"),
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], name=op.f("fk_device_control_commands_device_id_devices"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_device_control_commands_requested_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_control_commands")),
        sa.UniqueConstraint("command_id", name="uq_device_control_commands_command_id"),
    )
    with op.batch_alter_table("device_control_commands", schema=None) as batch_op:
        batch_op.create_index("ix_device_control_commands_device_boot_status", ["device_id", "boot_id", "status"], unique=False)
        batch_op.create_index("ix_device_control_commands_device_created", ["device_id", "created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_device_control_commands_device_id"), ["device_id"], unique=False)


def downgrade():
    with op.batch_alter_table("device_control_commands", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_device_control_commands_device_id"))
        batch_op.drop_index("ix_device_control_commands_device_created")
        batch_op.drop_index("ix_device_control_commands_device_boot_status")
    op.drop_table("device_control_commands")

    with op.batch_alter_table("device_control_runtimes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_device_control_runtimes_device_id"))
    op.drop_table("device_control_runtimes")
