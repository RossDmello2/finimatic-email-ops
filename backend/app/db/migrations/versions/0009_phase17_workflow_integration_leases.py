"""Add durable workflow and integration execution leases.

Revision ID: 0009_phase17_workflow_integration_leases
Revises: 0008_phase13_reply_dedupe_key
Create Date: 2026-06-07
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0009_phase17_workflow_integration_leases"
down_revision = "0008_phase13_reply_dedupe_key"
branch_labels = None
depends_on = None


def _hash(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _columns(connection, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(connection).get_columns(table)}


def _indexes(connection, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(connection).get_indexes(table)}


def _add_column(connection, table: str, column: sa.Column) -> None:
    if column.name not in _columns(connection, table):
        op.add_column(table, column)


def _create_index(connection, name: str, table: str, columns: list[str], *, unique: bool = False) -> None:
    if name not in _indexes(connection, table):
        op.create_index(name, table, columns, unique=unique)


def _backfill_workflow_runs(connection) -> None:
    rows = list(
        connection.execute(
            sa.text(
                "SELECT id, workbook_id, status, active_claim_key, execution_hash "
                "FROM workflow_runs ORDER BY workbook_id, created_at DESC, id DESC"
            )
        ).mappings()
    )
    active_workbooks: set[str] = set()
    for row in rows:
        values: dict[str, object] = {}
        if not row["execution_hash"]:
            values["execution_hash"] = _hash("legacy-workflow-run", row["id"])
        if row["status"] == "running":
            if row["workbook_id"] not in active_workbooks:
                active_workbooks.add(row["workbook_id"])
                values["active_claim_key"] = row["active_claim_key"] or _hash(
                    "workflow-active",
                    row["workbook_id"],
                )
            else:
                values["status"] = "superseded"
                values["active_claim_key"] = None
                values["completed_at"] = datetime.now(timezone.utc)
        if values:
            assignments = ", ".join(f"{key} = :{key}" for key in values)
            connection.execute(
                sa.text(f"UPDATE workflow_runs SET {assignments} WHERE id = :row_id"),
                {**values, "row_id": row["id"]},
            )


def _backfill_integration_rows(connection) -> None:
    previews = list(
        connection.execute(
            sa.text(
                "SELECT id, provider, entity_id, status, idempotency_key, "
                "active_claim_key, execution_hash "
                "FROM external_write_previews ORDER BY created_at DESC, id DESC"
            )
        ).mappings()
    )
    active_previews: set[tuple[str, str]] = set()
    for row in previews:
        values: dict[str, object] = {}
        if not row["execution_hash"]:
            values["execution_hash"] = _hash("integration-preview", row["idempotency_key"])
        active_key = (row["provider"], row["entity_id"])
        if row["status"] in {"pending_confirmation", "executing"}:
            if active_key not in active_previews:
                active_previews.add(active_key)
                values["active_claim_key"] = row["active_claim_key"] or _hash(
                    "integration-active",
                    *active_key,
                )
            else:
                values["status"] = "stale"
                values["active_claim_key"] = None
        if values:
            assignments = ", ".join(f"{key} = :{key}" for key in values)
            connection.execute(
                sa.text(
                    f"UPDATE external_write_previews SET {assignments} WHERE id = :row_id"
                ),
                {**values, "row_id": row["id"]},
            )

    attempts = list(
        connection.execute(
            sa.text(
                "SELECT id, idempotency_key, execution_hash "
                "FROM external_write_attempts ORDER BY created_at, id"
            )
        ).mappings()
    )
    seen: set[str] = set()
    for row in attempts:
        idempotency_key = row["idempotency_key"]
        if idempotency_key in seen:
            idempotency_key = _hash("legacy-integration-attempt", idempotency_key, row["id"])
        seen.add(idempotency_key)
        connection.execute(
            sa.text(
                "UPDATE external_write_attempts "
                "SET idempotency_key = :idempotency_key, execution_hash = :execution_hash "
                "WHERE id = :row_id"
            ),
            {
                "idempotency_key": idempotency_key,
                "execution_hash": row["execution_hash"]
                or _hash("integration-attempt", idempotency_key),
                "row_id": row["id"],
            },
        )


def upgrade() -> None:
    connection = op.get_bind()
    _add_column(connection, "workflow_runs", sa.Column("active_claim_key", sa.String()))
    _add_column(connection, "workflow_runs", sa.Column("execution_hash", sa.String()))
    _add_column(connection, "workflow_runs", sa.Column("lease_token", sa.String()))
    _add_column(connection, "workflow_runs", sa.Column("lease_owner", sa.String()))
    _add_column(connection, "workflow_runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    _add_column(connection, "workflow_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    _add_column(
        connection,
        "workflow_runs",
        sa.Column("lease_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column(connection, "workflow_runs", sa.Column("checkpoint_json", sa.Text()))

    _add_column(connection, "external_write_previews", sa.Column("execution_hash", sa.String()))
    _add_column(connection, "external_write_previews", sa.Column("active_claim_key", sa.String()))
    _add_column(connection, "external_write_previews", sa.Column("lease_token", sa.String()))
    _add_column(connection, "external_write_previews", sa.Column("lease_owner", sa.String()))
    _add_column(
        connection,
        "external_write_previews",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    )
    _add_column(connection, "external_write_previews", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    _add_column(
        connection,
        "external_write_previews",
        sa.Column("lease_generation", sa.Integer(), nullable=False, server_default="0"),
    )

    _add_column(connection, "external_write_attempts", sa.Column("execution_hash", sa.String()))
    _add_column(connection, "external_write_attempts", sa.Column("lease_token", sa.String()))

    _backfill_workflow_runs(connection)
    _backfill_integration_rows(connection)

    _create_index(
        connection,
        "ix_workflow_runs_active_claim_key",
        "workflow_runs",
        ["active_claim_key"],
        unique=True,
    )
    _create_index(connection, "ix_workflow_runs_execution_hash", "workflow_runs", ["execution_hash"])
    _create_index(
        connection,
        "ix_workflow_runs_lease_token",
        "workflow_runs",
        ["lease_token"],
        unique=True,
    )
    _create_index(
        connection,
        "ix_external_write_previews_active_claim_key",
        "external_write_previews",
        ["active_claim_key"],
        unique=True,
    )
    _create_index(
        connection,
        "ix_external_write_previews_execution_hash",
        "external_write_previews",
        ["execution_hash"],
    )
    _create_index(
        connection,
        "ix_external_write_previews_lease_token",
        "external_write_previews",
        ["lease_token"],
        unique=True,
    )
    _create_index(
        connection,
        "ix_external_write_attempts_execution_hash",
        "external_write_attempts",
        ["execution_hash"],
    )
    _create_index(
        connection,
        "ix_external_write_attempts_lease_token",
        "external_write_attempts",
        ["lease_token"],
    )
    _create_index(
        connection,
        "ix_external_write_attempts_idempotency_key",
        "external_write_attempts",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table, indexes in (
        (
            "external_write_attempts",
            (
                "ix_external_write_attempts_idempotency_key",
                "ix_external_write_attempts_lease_token",
                "ix_external_write_attempts_execution_hash",
            ),
        ),
        (
            "external_write_previews",
            (
                "ix_external_write_previews_lease_token",
                "ix_external_write_previews_execution_hash",
                "ix_external_write_previews_active_claim_key",
            ),
        ),
        (
            "workflow_runs",
            (
                "ix_workflow_runs_lease_token",
                "ix_workflow_runs_execution_hash",
                "ix_workflow_runs_active_claim_key",
            ),
        ),
    ):
        existing = _indexes(connection, table)
        for name in indexes:
            if name in existing:
                op.drop_index(name, table_name=table)

    for table, columns in (
        ("external_write_attempts", ("lease_token", "execution_hash")),
        (
            "external_write_previews",
            (
                "lease_generation",
                "heartbeat_at",
                "lease_expires_at",
                "lease_owner",
                "lease_token",
                "active_claim_key",
                "execution_hash",
            ),
        ),
        (
            "workflow_runs",
            (
                "checkpoint_json",
                "lease_generation",
                "heartbeat_at",
                "lease_expires_at",
                "lease_owner",
                "lease_token",
                "execution_hash",
                "active_claim_key",
            ),
        ),
    ):
        existing = _columns(connection, table)
        for name in columns:
            if name in existing:
                op.drop_column(table, name)
