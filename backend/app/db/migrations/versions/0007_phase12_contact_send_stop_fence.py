"""Add Phase 12 contact send-stop generation fencing.

Revision ID: 0007_phase12_contact_send_stop_fence
Revises: 0006_phase12_governed_dispatch_lock
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_phase12_contact_send_stop_fence"
down_revision = "0006_phase12_governed_dispatch_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    contact_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("contacts")}
    if "send_stop_generation" not in contact_columns:
        op.add_column(
            "contacts",
            sa.Column("send_stop_generation", sa.Integer(), nullable=False, server_default="0"),
        )

    attempt_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("send_attempts")}
    if "stop_generation" not in attempt_columns:
        op.add_column(
            "send_attempts",
            sa.Column("stop_generation", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    attempt_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("send_attempts")}
    if "stop_generation" in attempt_columns:
        op.drop_column("send_attempts", "stop_generation")

    contact_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("contacts")}
    if "send_stop_generation" in contact_columns:
        op.drop_column("contacts", "send_stop_generation")
