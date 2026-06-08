from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.db.models import Base
from app.core.idempotency import sha256_key


def test_alembic_upgrade_head_on_fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh_alembic.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    config = Config("alembic.ini")

    command.upgrade(config, "head")

    assert db_path.exists()


def test_alembic_tables_match_models_on_fresh_db(tmp_path, monkeypatch):
    from sqlalchemy import inspect

    db_path = tmp_path / "fresh_alembic_tables.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    command.upgrade(Config("alembic.ini"), "head")

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    inspector = inspect(engine)
    actual = set(inspector.get_table_names()) - {"alembic_version"}
    expected = set(Base.metadata.tables)

    assert actual == expected
    for table in Base.metadata.sorted_tables:
        actual_columns = {column["name"] for column in inspector.get_columns(table.name)}
        assert actual_columns == {column.name for column in table.columns}
        actual_indexes = {
            (tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(table.name)
        }
        for index in table.indexes:
            expected_index = (tuple(column.name for column in index.columns), bool(index.unique))
            assert expected_index in actual_indexes, f"missing index {index.name} on {table.name}"


def test_reply_dedupe_migration_handles_null_row_before_existing_key(tmp_path, monkeypatch):
    db_path = tmp_path / "mixed_reply_dedupe.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config("alembic.ini")
    command.upgrade(config, "0007_phase12_contact_send_stop_fence")
    engine = create_engine(database_url)
    dedupe_key = sha256_key("reply", "contact-1", "<legacy@example.com>")
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE replies ADD COLUMN dedupe_key VARCHAR"))
        connection.execute(
            text(
                "INSERT INTO contacts (id, email, source, status, send_stop_generation) "
                "VALUES ('contact-1', 'legacy@example.com', 'test', 'imported', 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO replies "
                "(id, contact_id, received_at, classified_as, external_message_id, dedupe_key) "
                "VALUES "
                "('reply-1', 'contact-1', CURRENT_TIMESTAMP, 'reply', '<LEGACY@EXAMPLE.COM>', NULL), "
                "('reply-2', 'contact-1', CURRENT_TIMESTAMP, 'reply', '<legacy@example.com>', :dedupe_key)"
            ),
            {"dedupe_key": dedupe_key},
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT id, dedupe_key FROM replies ORDER BY id")
        ).all()
    assert rows == [("reply-1", None), ("reply-2", dedupe_key)]


def test_upgrade_from_0003_repairs_legacy_false_success(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy_false_success.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config("alembic.ini")
    command.upgrade(config, "0003_reply_followup_campaigns")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO contacts (id, email, source, status) "
                "VALUES ('contact-legacy', 'legacy@recipient.test', 'test', 'sent')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO drafts "
                "(id, contact_id, subject, body, rejected, approved) "
                "VALUES ('draft-legacy', 'contact-legacy', 'Legacy', 'Legacy body', 0, 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO send_queue "
                "(id, contact_id, draft_id, sequence_num, scheduled_at, status, idempotency_key) "
                "VALUES ('queue-legacy', 'contact-legacy', 'draft-legacy', 1, CURRENT_TIMESTAMP, 'sent', 'legacy-key')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO send_attempts "
                "(id, queue_id, contact_id, draft_id, idempotency_key, provider_msg_id, status, sender_identity) "
                "VALUES ('attempt-legacy', 'queue-legacy', 'contact-legacy', 'draft-legacy', "
                "'legacy-key', 'fake-legacy', 'success', 'legacy-sender@finimatic.test')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, contact_id, direction, subject, body, source, auto_sent, occurred_at) "
                "VALUES ('message-legacy', 'contact-legacy', 'outbound', 'Legacy', "
                "'Legacy body', 'queue', 0, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO follow_up_sequences "
                "(id, contact_id, sequence_num, due_at, draft_id, status) "
                "VALUES ('followup-legacy', 'contact-legacy', 2, CURRENT_TIMESTAMP, 'draft-legacy', 'due')"
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        attempt = connection.execute(
            text(
                "SELECT status, provider_accepted, error_code, created_at "
                "FROM send_attempts WHERE id = 'attempt-legacy'"
            )
        ).one()
        queue_status = connection.execute(
            text("SELECT status FROM send_queue WHERE id = 'queue-legacy'")
        ).scalar_one()
        queue_schedule_source = connection.execute(
            text("SELECT schedule_source FROM send_queue WHERE id = 'queue-legacy'")
        ).scalar_one()
        contact_status = connection.execute(
            text("SELECT status FROM contacts WHERE id = 'contact-legacy'")
        ).scalar_one()
        message_source = connection.execute(
            text("SELECT source FROM conversation_messages WHERE id = 'message-legacy'")
        ).scalar_one()
        followup = connection.execute(
            text(
                "SELECT status, stop_reason FROM follow_up_sequences "
                "WHERE id = 'followup-legacy'"
            )
        ).one()

    assert attempt[0] == "reconciliation_required"
    assert attempt[1] in (False, 0)
    assert attempt[2] == "legacy_acceptance_unverified"
    assert attempt[3] is not None
    assert queue_status == "reconciliation_required"
    assert queue_schedule_source == "legacy"
    assert contact_status == "approved"
    assert message_source == "historical_unverified_queue"
    assert followup == ("stopped", "LEGACY_PROVIDER_ACCEPTANCE_UNVERIFIED")
