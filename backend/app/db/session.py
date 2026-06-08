from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.idempotency import sha256_key
from app.db.models import Base


_engine = None
_SessionMaker = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False)


def _normalize_database_url(url: str) -> str:
    if url.startswith("sqlite+aiosqlite:///"):
        return "sqlite:///" + url.split("sqlite+aiosqlite:///", 1)[1]
    return url


def configure_database(database_url: str | None = None):
    global _engine
    url = _normalize_database_url(database_url or os.getenv("DATABASE_URL", "sqlite:///./finimatic.db"))
    if url.startswith("sqlite:///"):
        db_path = url.removeprefix("sqlite:///")
        if db_path and db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    _engine = create_engine(url, connect_args=connect_args, future=True)
    _SessionMaker.configure(bind=_engine)
    return _engine


def get_engine():
    global _engine
    if _engine is None:
        configure_database()
    return _engine


def init_db() -> None:
    engine = get_engine()
    if os.getenv("FINIMATIC_TEST_SCHEMA_CREATE") == "1":
        Base.metadata.create_all(bind=engine)
        return
    inspector = inspect(engine)
    if "alembic_version" not in inspector.get_table_names():
        raise RuntimeError("database_schema_not_migrated")
    with engine.connect() as connection:
        current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    expected = ScriptDirectory.from_config(config).get_current_head()
    if current != expected:
        raise RuntimeError(f"database_schema_revision_mismatch:{current or 'none'}:{expected}")


def _datetime_column_sql_type(dialect_name: str) -> str:
    return "TIMESTAMP WITH TIME ZONE" if dialect_name == "postgresql" else "DATETIME"


def _backfill_reply_dedupe_keys(connection) -> None:
    rows = list(
        connection.execute(
        text(
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
        normalized = row["external_message_id"].strip().lower()
        if not normalized:
            continue
        dedupe_key = sha256_key("reply", row["contact_id"], normalized)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        pending.append({"reply_id": row["id"], "dedupe_key": dedupe_key})
    if pending:
        connection.execute(
            text("UPDATE replies SET dedupe_key = :dedupe_key WHERE id = :reply_id"),
            pending,
        )


def _apply_lightweight_migrations() -> None:
    engine = get_engine()
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    datetime_type = _datetime_column_sql_type(engine.dialect.name)
    if "replies" in table_names:
        reply_columns = {column["name"] for column in inspector.get_columns("replies")}
        if "archived_at" not in reply_columns:
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE replies ADD COLUMN archived_at {datetime_type}"))
        if "external_message_id" not in reply_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE replies ADD COLUMN external_message_id VARCHAR"))
        if "intent" not in reply_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE replies ADD COLUMN intent TEXT"))
        if "dedupe_key" not in reply_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE replies ADD COLUMN dedupe_key VARCHAR"))
        with engine.begin() as connection:
            _backfill_reply_dedupe_keys(connection)
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "ix_replies_dedupe_key ON replies (dedupe_key)"
                )
            )
    if "drafts" in table_names:
        draft_columns = {column["name"] for column in inspector.get_columns("drafts")}
        if "notes" not in draft_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE drafts ADD COLUMN notes TEXT"))
        if "source" not in draft_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE drafts ADD COLUMN source VARCHAR"))
        if "rejected" not in draft_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE drafts ADD COLUMN rejected BOOLEAN NOT NULL DEFAULT 0"))
    if "contacts" in table_names:
        contact_columns = {column["name"] for column in inspector.get_columns("contacts")}
        if "auto_reply_override" not in contact_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE contacts ADD COLUMN auto_reply_override TEXT"))
        if "deleted_at" not in contact_columns:
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE contacts ADD COLUMN deleted_at {datetime_type}"))
        if "send_stop_generation" not in contact_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE contacts ADD COLUMN send_stop_generation INTEGER NOT NULL DEFAULT 0"))
    if "conversation_messages" in table_names:
        message_columns = {column["name"] for column in inspector.get_columns("conversation_messages")}
        if "auto_sent" not in message_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE conversation_messages ADD COLUMN auto_sent BOOLEAN NOT NULL DEFAULT 0"))
    if "follow_up_sequences" in table_names:
        followup_columns = {column["name"] for column in inspector.get_columns("follow_up_sequences")}
        if "pending_draft_id" not in followup_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE follow_up_sequences ADD COLUMN pending_draft_id VARCHAR"))
    if "agent_sessions" in table_names:
        agent_session_columns = {column["name"] for column in inspector.get_columns("agent_sessions")}
        for column_name in ("context_loaded_at", "contact_name_map", "turn_history", "current_channel"):
            if column_name not in agent_session_columns:
                with engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE agent_sessions ADD COLUMN {column_name} TEXT"))
    if "send_attempts" in table_names:
        attempt_columns = {column["name"] for column in inspector.get_columns("send_attempts")}
        additions = {
            "dispatch_lock_key": "VARCHAR",
            "stop_generation": "INTEGER NOT NULL DEFAULT 0",
            "tracking_message_id": "VARCHAR",
            "configured_transport": "VARCHAR",
            "effective_transport": "VARCHAR",
            "transport_source": "VARCHAR",
            "simulated": "BOOLEAN",
            "provider_contacted": "BOOLEAN",
            "provider_accepted": "BOOLEAN",
            "provider_response_classification": "VARCHAR",
            "created_at": datetime_type,
        }
        for column_name, column_type in additions.items():
            if column_name not in attempt_columns:
                with engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE send_attempts ADD COLUMN {column_name} {column_type}"))
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "ix_send_attempts_dispatch_lock_key ON send_attempts (dispatch_lock_key)"
                )
            )
    if "send_queue" in table_names:
        queue_columns = {column["name"] for column in inspector.get_columns("send_queue")}
        if "processing_started_at" not in queue_columns:
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE send_queue ADD COLUMN processing_started_at {datetime_type}"))
        if "processing_token" not in queue_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE send_queue ADD COLUMN processing_token VARCHAR"))
    if "send_attempts" in table_names and "send_queue" in table_names:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE send_attempts
                    SET created_at = COALESCE(created_at, sent_at, CURRENT_TIMESTAMP)
                    WHERE created_at IS NULL
                    """
                )
            )
            connection.execute(
                text(
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
                text(
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
            connection.execute(
                text(
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
            connection.execute(
                text(
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
            connection.execute(
                text(
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


def SessionLocal() -> Session:
    if _engine is None:
        configure_database()
    return _SessionMaker()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
