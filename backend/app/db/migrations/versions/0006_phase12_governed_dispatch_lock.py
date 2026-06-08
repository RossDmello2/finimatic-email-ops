"""Add Phase 12 governed provider dispatch locking.

Revision ID: 0006_phase12_governed_dispatch_lock
Revises: 0005_phase12_queue_claim_fencing
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_phase12_governed_dispatch_lock"
down_revision = "0005_phase12_queue_claim_fencing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("send_attempts")}
    if "dispatch_lock_key" not in columns:
        op.add_column("send_attempts", sa.Column("dispatch_lock_key", sa.String(), nullable=True))
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("send_attempts")}
    if "ix_send_attempts_dispatch_lock_key" not in indexes:
        op.create_index(
            "ix_send_attempts_dispatch_lock_key",
            "send_attempts",
            ["dispatch_lock_key"],
            unique=True,
        )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("send_attempts")}
    if "dispatch_lock_key" in columns:
        indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("send_attempts")}
        if "ix_send_attempts_dispatch_lock_key" in indexes:
            op.drop_index("ix_send_attempts_dispatch_lock_key", table_name="send_attempts")
        op.drop_column("send_attempts", "dispatch_lock_key")
