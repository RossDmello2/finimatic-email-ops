"""Track why queue rows are scheduled.

Revision ID: 0012_phase17_queue_schedule_source
Revises: 0011_phase17_operator_sessions
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_phase17_queue_schedule_source"
down_revision = "0011_phase17_operator_sessions"
branch_labels = None
depends_on = None


TEMPORARY_REASONS = ("CANARY_NOT_VERIFIED", "SEND_WINDOW_NOT_ELAPSED")


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    queue_columns = {column["name"] for column in inspector.get_columns("send_queue")}
    if "schedule_source" not in queue_columns:
        op.add_column(
            "send_queue",
            sa.Column(
                "schedule_source",
                sa.String(),
                nullable=False,
                server_default="legacy",
            ),
        )
    connection.execute(
        sa.text(
            """
            UPDATE send_queue
            SET schedule_source = 'policy_deferral'
            WHERE status = 'pending'
              AND policy_block_reasons IS NOT NULL
              AND (
                  policy_block_reasons LIKE :canary
                  OR policy_block_reasons LIKE :window
              )
            """
        ),
        {
            "canary": f"%{TEMPORARY_REASONS[0]}%",
            "window": f"%{TEMPORARY_REASONS[1]}%",
        },
    )


def downgrade() -> None:
    raise RuntimeError(
        "0012_phase17_queue_schedule_source is deliberately irreversible; "
        "restore a database snapshot and apply a forward repair migration"
    )
