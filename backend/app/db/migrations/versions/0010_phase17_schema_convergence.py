"""Converge migration-managed schema with the Phase 17 runtime models.

Revision ID: 0010_phase17_schema_convergence
Revises: 0009_phase17_workflow_integration_leases
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_phase17_schema_convergence"
down_revision = "0009_phase17_workflow_integration_leases"
branch_labels = None
depends_on = None


SEND_ATTEMPT_COLUMNS = (
    sa.Column("tracking_message_id", sa.String(), nullable=True),
    sa.Column("configured_transport", sa.String(), nullable=True),
    sa.Column("effective_transport", sa.String(), nullable=True),
    sa.Column("transport_source", sa.String(), nullable=True),
    sa.Column("simulated", sa.Boolean(), nullable=True),
    sa.Column("provider_contacted", sa.Boolean(), nullable=True),
    sa.Column("provider_accepted", sa.Boolean(), nullable=True),
    sa.Column("provider_response_classification", sa.String(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
)

MODEL_INDEXES = (
    ("ix_deliverability_checks_sender_email", "deliverability_checks", ("sender_email",), False),
    ("ix_deliverability_checks_domain", "deliverability_checks", ("domain",), False),
    ("ix_external_write_previews_provider", "external_write_previews", ("provider",), False),
    ("ix_external_write_previews_idempotency_key", "external_write_previews", ("idempotency_key",), True),
    ("ix_inbox_placement_tests_recipient_domain", "inbox_placement_tests", ("recipient_domain",), False),
    ("ix_inbox_placement_tests_sender_email", "inbox_placement_tests", ("sender_email",), False),
    ("ix_integration_connections_provider", "integration_connections", ("provider",), False),
    ("ix_recipient_domain_caps_sender_email", "recipient_domain_caps", ("sender_email",), False),
    ("ix_recipient_domain_caps_recipient_domain", "recipient_domain_caps", ("recipient_domain",), False),
    ("ix_send_attempts_provider_accepted", "send_attempts", ("provider_accepted",), False),
    ("ix_sender_domain_health_domain", "sender_domain_health", ("domain",), True),
    ("ix_sender_mailboxes_email", "sender_mailboxes", ("email",), True),
    ("ix_sender_mailboxes_domain", "sender_mailboxes", ("domain",), False),
    ("ix_external_write_attempts_provider", "external_write_attempts", ("provider",), False),
    ("ix_external_write_attempts_preview_id", "external_write_attempts", ("preview_id",), False),
    ("ix_integration_mappings_connection_id", "integration_mappings", ("connection_id",), False),
    ("ix_sync_journals_provider", "sync_journals", ("provider",), False),
    ("ix_sync_journals_idempotency_key", "sync_journals", ("idempotency_key",), True),
    ("ix_workbook_columns_workbook_id", "workbook_columns", ("workbook_id",), False),
    ("ix_workflow_runs_workbook_id", "workflow_runs", ("workbook_id",), False),
    ("ix_account_facts_contact_id", "account_facts", ("contact_id",), False),
    ("ix_account_facts_account_key", "account_facts", ("account_key",), False),
    ("ix_email_verifications_email", "email_verifications", ("email",), True),
    ("ix_email_verifications_contact_id", "email_verifications", ("contact_id",), False),
    ("ix_lead_facts_contact_id", "lead_facts", ("contact_id",), False),
    ("ix_workbook_rows_workbook_id", "workbook_rows", ("workbook_id",), False),
    ("ix_workbook_rows_contact_id", "workbook_rows", ("contact_id",), False),
    ("ix_workflow_steps_workflow_run_id", "workflow_steps", ("workflow_run_id",), False),
    ("ix_cell_outputs_workbook_row_id", "cell_outputs", ("workbook_row_id",), False),
    ("ix_cell_outputs_workbook_column_id", "cell_outputs", ("workbook_column_id",), False),
    ("ix_cell_outputs_workbook_id", "cell_outputs", ("workbook_id",), False),
    ("ix_draft_evidence_checks_contact_id", "draft_evidence_checks", ("contact_id",), False),
    ("ix_draft_evidence_checks_draft_id", "draft_evidence_checks", ("draft_id",), False),
    ("ix_email_verification_attempts_verification_id", "email_verification_attempts", ("verification_id",), False),
    ("ix_pending_agent_actions_session_id", "pending_agent_actions", ("session_id",), False),
    ("ix_workflow_step_attempts_workflow_step_id", "workflow_step_attempts", ("workflow_step_id",), False),
    ("ix_workflow_step_attempts_workbook_row_id", "workflow_step_attempts", ("workbook_row_id",), False),
)


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    attempt_columns = {column["name"] for column in inspector.get_columns("send_attempts")}
    for column in SEND_ATTEMPT_COLUMNS:
        if column.name not in attempt_columns:
            op.add_column("send_attempts", column)

    queue_columns = {column["name"] for column in inspector.get_columns("send_queue")}
    if "processing_started_at" not in queue_columns:
        op.add_column(
            "send_queue",
            sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        )

    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    indexes_by_table = {
        table: {index["name"] for index in inspector.get_indexes(table)}
        for table in tables
    }
    for name, table, columns, unique in MODEL_INDEXES:
        if table in tables and name not in indexes_by_table[table]:
            op.create_index(name, table, list(columns), unique=unique)

    _repair_legacy_acceptance(connection, tables)


def _repair_legacy_acceptance(connection, tables: set[str]) -> None:
    if not {"send_attempts", "send_queue"}.issubset(tables):
        return
    connection.execute(
        sa.text(
            """
            UPDATE send_attempts
            SET created_at = COALESCE(created_at, sent_at, CURRENT_TIMESTAMP)
            WHERE created_at IS NULL
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE send_attempts
            SET status = 'reconciliation_required',
                provider_accepted = FALSE,
                error_code = COALESCE(error_code, 'legacy_acceptance_unverified'),
                error_detail = COALESCE(
                    error_detail,
                    'Historical success predates durable provider acceptance evidence'
                )
            WHERE provider_accepted IS NULL
              AND status IN ('success', 'sent', 'provider_accepted')
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE send_queue
            SET status = 'reconciliation_required'
            WHERE status IN ('sent', 'provider_accepted')
              AND NOT EXISTS (
                  SELECT 1
                  FROM send_attempts
                  WHERE send_attempts.queue_id = send_queue.id
                    AND send_attempts.provider_accepted = TRUE
              )
            """
        )
    )
    if "contacts" in tables:
        connection.execute(
            sa.text(
                """
                UPDATE contacts
                SET status = 'approved'
                WHERE status = 'sent'
                  AND EXISTS (
                      SELECT 1
                      FROM send_queue
                      WHERE send_queue.contact_id = contacts.id
                        AND send_queue.status = 'reconciliation_required'
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM send_attempts
                      WHERE send_attempts.contact_id = contacts.id
                        AND send_attempts.provider_accepted = TRUE
                  )
                """
            )
        )
    if "conversation_messages" in tables:
        connection.execute(
            sa.text(
                """
                UPDATE conversation_messages
                SET source = 'historical_unverified_queue'
                WHERE direction = 'outbound'
                  AND source = 'queue'
                  AND EXISTS (
                      SELECT 1
                      FROM send_queue
                      WHERE send_queue.contact_id = conversation_messages.contact_id
                        AND send_queue.status = 'reconciliation_required'
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM send_attempts
                      WHERE send_attempts.contact_id = conversation_messages.contact_id
                        AND send_attempts.provider_accepted = TRUE
                  )
                """
            )
        )
    if "follow_up_sequences" in tables:
        connection.execute(
            sa.text(
                """
                UPDATE follow_up_sequences
                SET status = 'stopped',
                    stop_reason = 'LEGACY_PROVIDER_ACCEPTANCE_UNVERIFIED'
                WHERE status IN ('due', 'draft_ready', 'dispatched')
                  AND EXISTS (
                      SELECT 1
                      FROM send_queue
                      WHERE send_queue.contact_id = follow_up_sequences.contact_id
                        AND send_queue.status = 'reconciliation_required'
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM send_attempts
                      WHERE send_attempts.contact_id = follow_up_sequences.contact_id
                        AND send_attempts.provider_accepted = TRUE
                  )
                """
            )
        )


def downgrade() -> None:
    raise RuntimeError(
        "0010_phase17_schema_convergence is deliberately irreversible; "
        "restore a database snapshot and apply a forward repair migration"
    )
