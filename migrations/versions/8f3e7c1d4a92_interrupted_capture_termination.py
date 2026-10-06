"""Persist reported RideSession capture termination metadata.

Revision ID: 8f3e7c1d4a92
Revises: f4a9c2e18b61
Create Date: 2026-10-02 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "8f3e7c1d4a92"
down_revision = "f4a9c2e18b61"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable is intentional: existing sessions have no confirmed termination report.
    with op.batch_alter_table("ride_sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("termination_reason", sa.String(length=80), nullable=True))


def downgrade():
    with op.batch_alter_table("ride_sessions", schema=None) as batch_op:
        batch_op.drop_column("termination_reason")
