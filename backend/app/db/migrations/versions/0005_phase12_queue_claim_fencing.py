"""Add Phase 12 Queue claim fencing.

Revision ID: 0005_phase12_queue_claim_fencing
Revises: 0004_orange_slice_upgrade
Create Date: 2026-06-06
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_phase12_queue_claim_fencing"
down_revision = "0004_orange_slice_upgrade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("send_queue")}
    if "processing_token" not in columns:
        op.add_column("send_queue", sa.Column("processing_token", sa.String(), nullable=True))
        op.create_index("ix_send_queue_processing_token", "send_queue", ["processing_token"])


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("send_queue")}
    if "processing_token" in columns:
        op.drop_index("ix_send_queue_processing_token", table_name="send_queue")
        op.drop_column("send_queue", "processing_token")
