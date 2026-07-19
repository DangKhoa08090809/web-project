"""Session notes

Revision ID: b8f6d2c91a3a
Revises: 9d2a8f7c4e11
Create Date: 2026-07-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "b8f6d2c91a3a"
down_revision = "9d2a8f7c4e11"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ride_sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("notes", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("ride_sessions", schema=None) as batch_op:
        batch_op.drop_column("notes")
