"""Add a durable reply deduplication key.

Revision ID: 0008_phase13_reply_dedupe_key
Revises: 0007_phase12_contact_send_stop_fence
Create Date: 2026-06-07
"""

import hashlib

from alembic import op
import sqlalchemy as sa


revision = "0008_phase13_reply_dedupe_key"
down_revision = "0007_phase12_contact_send_stop_fence"
branch_labels = None
depends_on = None


def _dedupe_key(contact_id: str, external_message_id: str) -> str:
    raw = f"reply|{contact_id}|{external_message_id.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _backfill_dedupe_keys(connection) -> None:
    rows = list(
        connection.execute(
        sa.text(
            "SELECT id, contact_id, external_message_id, dedupe_key "
            "FROM replies WHERE external_message_id IS NOT NULL ORDER BY id"
        )
        ).mappings()
    )
    seen = {row["dedupe_key"] for row in rows if row["dedupe_key"]}
    pending = []
    for row in rows:
        if row["dedupe_key"]:
            continue
        normalized = row["external_message_id"].strip()
        if not normalized:
            continue
        dedupe_key = _dedupe_key(row["contact_id"], normalized)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        pending.append({"reply_id": row["id"], "dedupe_key": dedupe_key})
    if pending:
        connection.execute(
            sa.text("UPDATE replies SET dedupe_key = :dedupe_key WHERE id = :reply_id"),
            pending,
        )


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    reply_columns = {column["name"] for column in inspector.get_columns("replies")}
    if "dedupe_key" not in reply_columns:
        op.add_column("replies", sa.Column("dedupe_key", sa.String(), nullable=True))

    _backfill_dedupe_keys(connection)
    indexes = {index["name"] for index in sa.inspect(connection).get_indexes("replies")}
    if "ix_replies_dedupe_key" not in indexes:
        op.create_index("ix_replies_dedupe_key", "replies", ["dedupe_key"], unique=True)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("replies")}
    if "ix_replies_dedupe_key" in indexes:
        op.drop_index("ix_replies_dedupe_key", table_name="replies")

    reply_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("replies")}
    if "dedupe_key" in reply_columns:
        op.drop_column("replies", "dedupe_key")
