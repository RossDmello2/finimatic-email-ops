from __future__ import annotations

import asyncio
import json
import smtplib
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text

from app.agent.layman_formatter import format_for_layman
from app.core.time import utcnow
from app.db.models import AuditEvent, Contact, ConversationMessage, Draft, FollowUpSequence, SendAttempt, SendQueue
from app.db.session import SessionLocal, _datetime_column_sql_type
from app.send.fake_transport import FakeTransport
from app.send.outcomes import SendOutcome, TransportResolution
from app.send.queue_worker import process_pending_queue
from app.send.smtp_adapter import GmailApiTransport, SMTPTransport
from conftest import configure_sender

PHASE12_SESSION = "phase12-phase11-regression-session"

def test_lightweight_migration_datetime_type_is_postgresql_safe():
    assert _datetime_column_sql_type("postgresql") == "TIMESTAMP WITH TIME ZONE"
    assert _datetime_column_sql_type("sqlite") == "DATETIME"


def test_layman_sent_status_does_not_claim_delivery():
    result = format_for_layman("Status: sent")

    assert "accepted by the provider" in result
    assert "delivered" not in result


def _queued_send(client, *, email: str, dry_run: bool = False) -> dict:
    configure_sender(client, canary_verified=True, dry_run=dry_run)
    contact = client.post(
        "/api/contacts",
        json={"email": email, "creator_name": "Phase Eleven", "source": "test"},
    ).json()
    draft = client.post(
        "/api/drafts",
        json={"contact_id": contact["id"], "subject": "Phase 11", "body": "Synthetic transport truth test."},
    ).json()
    approved = client.post("/api/drafts/approve-bulk", json={"draft_ids": [draft["id"]]})
    assert approved.status_code == 200
    assert approved.json()["queued"] == 1
    queue = client.get("/api/queue").json()["items"][-1]
    return {"contact": contact, "draft": draft, "queue": queue}


def _assert_no_provider_success_side_effects(queue_id: str, contact_id: str) -> None:
    with SessionLocal() as db:
        queue = db.get(SendQueue, queue_id)
        contact = db.get(Contact, contact_id)
        assert queue.status not in {"sent", "provider_accepted"}
        assert contact.status != "sent"
        assert db.query(SendAttempt).filter_by(queue_id=queue_id, provider_accepted=True).count() == 0
        assert db.query(ConversationMessage).filter_by(contact_id=contact_id, direction="outbound").count() == 0
        assert db.query(FollowUpSequence).filter_by(contact_id=contact_id).count() == 0
        success_events = db.query(AuditEvent).filter_by(event_type="send.success", entity_id=queue_id).count()
        assert success_events == 0


def test_missing_canary_defers_queue_without_permanent_contact_block(client):
    configure_sender(client, canary_verified=False, dry_run=False)
    assert client.post("/api/settings/verify-email").json()["readiness"] in {"smtp_verified", "provider_verified"}
    contact = client.post(
        "/api/contacts",
        json={"email": "canary-wait@recipient.test", "creator_name": "Canary Wait", "source": "test"},
    ).json()
    draft = client.post(
        "/api/drafts",
        json={"contact_id": contact["id"], "subject": "Canary wait", "body": "Synthetic canary wait test."},
    ).json()

    approved = client.post(f"/api/drafts/{draft['id']}/approve")

    assert approved.status_code == 200
    payload = approved.json()
    assert payload["delivery_status"] == "deferred"
    assert payload["delivery_result"]["deferred"] == 1
    assert payload["queue"]["status"] == "pending"
    assert payload["queue"]["policy_block_reasons"] == ["CANARY_NOT_VERIFIED"]
    assert payload["queue"]["scheduled_at"].endswith("+00:00")
    assert client.get("/api/contacts").json()["items"][-1]["status"] == "approved"
    assert client.app.state.transport.sent == []


def test_successful_canary_releases_historical_prerequisite_block(client):
    configure_sender(client, canary_verified=False, dry_run=False)
    contact = client.post(
        "/api/contacts",
        json={"email": "canary-release@recipient.test", "creator_name": "Canary Release", "source": "test"},
    ).json()
    draft = client.post(
        "/api/drafts",
        json={"contact_id": contact["id"], "subject": "Canary release", "body": "Synthetic canary release test."},
    ).json()
    approved = client.post(f"/api/drafts/{draft['id']}/approve").json()

    with SessionLocal() as db:
        queue = db.get(SendQueue, approved["queue_id"])
        queue.status = "blocked"
        queue.policy_block_reasons = json.dumps(["CANARY_NOT_VERIFIED", "SEND_WINDOW_NOT_ELAPSED"])
        queue.contact.status = "blocked_by_policy"
        db.commit()

    canary = client.post("/api/canary/send")

    assert canary.status_code == 200
    assert canary.json()["provider_accepted"] is True
    assert canary.json()["released_queue_entries"] == 1
    checked = client.get(f"/api/queue/{approved['queue_id']}").json()
    assert checked["status"] == "pending"
    assert "CANARY_NOT_VERIFIED" not in checked["policy_block_reasons"]
    assert client.get("/api/contacts").json()["items"][-1]["status"] == "approved"
    with SessionLocal() as db:
        assert (
            db.query(AuditEvent)
            .filter_by(event_type="queue.prerequisite_released", entity_id=approved["queue_id"])
            .count()
            == 1
        )


def test_fake_transport_returns_simulated_non_provider_outcome():
    outcome = FakeTransport().send(
        sender="sender@finimatic.test",
        password="fixture",
        to="recipient@recipient.test",
        subject="Synthetic",
        body="Synthetic",
    )

    assert outcome.attempt_status == "simulated"
    assert outcome.simulated is True
    assert outcome.provider_contacted is False
    assert outcome.provider_accepted is False
    assert outcome.provider_message_id is None


def test_dry_run_persists_truth_without_success_side_effects(client):
    setup = _queued_send(client, email="phase11-dry-run@recipient.test", dry_run=True)

    result = client.post("/api/queue/process").json()
    queue = client.get(f"/api/queue/{setup['queue']['id']}").json()

    assert result["simulated"] == 1
    assert result["provider_accepted"] == 0
    assert queue["status"] == "simulated"
    assert queue["latest_attempt"]["simulated"] is True
    assert queue["latest_attempt"]["provider_contacted"] is False
    assert queue["latest_attempt"]["provider_accepted"] is False
    assert len(client.app.state.transport.sent) == 0
    _assert_no_provider_success_side_effects(setup["queue"]["id"], setup["contact"]["id"])


def test_live_mode_with_explicit_fake_transport_is_blocked(client):
    setup = _queued_send(client, email="phase11-fake-live@recipient.test", dry_run=False)
    fake = FakeTransport()

    with SessionLocal() as db:
        result = asyncio.run(process_pending_queue(db, transport=fake))

    queue = client.get(f"/api/queue/{setup['queue']['id']}").json()
    assert result["blocked"] == 1
    assert result["provider_accepted"] == 0
    assert queue["status"] == "blocked"
    assert queue["latest_attempt"]["effective_transport"] == "fake"
    assert queue["latest_attempt"]["simulated"] is True
    assert fake.sent == []
    _assert_no_provider_success_side_effects(setup["queue"]["id"], setup["contact"]["id"])


def test_environment_override_is_visible_and_cannot_claim_configured_gmail(client, monkeypatch):
    from app.send import smtp_adapter

    client.post(
        "/api/settings",
        json={
            "email_transport": "gmail_api",
            "gmail_api_client_id": "fixture-id",
            "gmail_api_client_secret": "fixture-secret",
            "gmail_api_refresh_token": "fixture-refresh",
        },
    )
    accepted_transport = client.app.state.transport
    smtp_adapter.set_test_transport_factory(None)
    monkeypatch.setenv("FINIMATIC_TRANSPORT", "fake")
    try:
        settings = client.get("/api/settings").json()
        with SessionLocal() as db:
            adapter = smtp_adapter.GmailAdapter.from_settings(db)
            outcome = asyncio.run(
                adapter.send_message(
                    "recipient@recipient.test",
                    "Synthetic",
                    "Synthetic",
                    "sender@finimatic.test",
                    "fixture",
                )
            )
    finally:
        smtp_adapter.set_test_transport_factory(lambda: accepted_transport)

    assert settings["configured_transport"] == "gmail_api"
    assert settings["effective_transport"] == "fake"
    assert settings["transport_source"] == "environment:FINIMATIC_TRANSPORT"
    assert settings["transport_simulated"] is True
    assert outcome.attempt_status == "blocked"
    assert outcome.provider_contacted is False
    assert outcome.provider_accepted is False


def test_fake_canary_cannot_verify_sender_or_emit_success(client, monkeypatch):
    from app.send import smtp_adapter

    configure_sender(client, canary_verified=False, dry_run=False)
    accepted_transport = client.app.state.transport
    smtp_adapter.set_test_transport_factory(None)
    monkeypatch.setenv("FINIMATIC_TRANSPORT", "fake")
    try:
        response = client.post("/api/canary/send").json()
    finally:
        smtp_adapter.set_test_transport_factory(lambda: accepted_transport)

    assert response["status"] == "blocked"
    assert response["simulated"] is True
    assert response["provider_accepted"] is False
    assert client.get("/api/settings").json()["canary_verified"] is False
    events = client.get("/api/audit").json()["items"]
    assert not any(row["event_type"] == "canary.success" for row in events)


def test_fake_conversation_send_cannot_create_outbound_message(client, monkeypatch):
    from app.send import smtp_adapter

    configure_sender(client, canary_verified=True, dry_run=False)
    contact = client.post(
        "/api/contacts",
        json={"email": "phase11-conversation@recipient.test", "creator_name": "Conversation", "source": "test"},
    ).json()
    accepted_transport = client.app.state.transport
    smtp_adapter.set_test_transport_factory(None)
    monkeypatch.setenv("FINIMATIC_TRANSPORT", "fake")
    try:
        response = client.post(
            f"/api/conversations/{contact['id']}/send",
            json={"session_token": PHASE12_SESSION, "subject": "Synthetic", "body": "Synthetic conversation reply."},
        )
        action_id = response.json()["pending_action"]["action_id"]
        response = client.post(
            f"/api/conversations/{contact['id']}/confirm-send",
            json={
                "session_token": PHASE12_SESSION,
                "action_id": action_id,
                "subject": "Synthetic",
                "body": "Synthetic conversation reply.",
            },
        ).json()
    finally:
        smtp_adapter.set_test_transport_factory(lambda: accepted_transport)

    assert response["status"] == "blocked"
    assert response["provider_accepted"] is False
    with SessionLocal() as db:
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 0
        assert db.query(SendAttempt).filter_by(contact_id=contact["id"], provider_accepted=True).count() == 0


class MissingIdResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {}


class MissingIdClient:
    def __init__(self, timeout):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, **kwargs):
        if url.endswith("/token"):
            return type("Token", (), {"raise_for_status": lambda self: None, "json": lambda self: {"access_token": "fixture"}})()
        return MissingIdResponse()


def test_gmail_api_missing_native_message_id_requires_reconciliation(monkeypatch):
    from app.send import smtp_adapter

    monkeypatch.setattr(smtp_adapter.httpx, "Client", MissingIdClient)
    outcome = GmailApiTransport(client_id="id", client_secret="secret", refresh_token="refresh").send(
        sender="sender@finimatic.test",
        password="",
        to="recipient@recipient.test",
        subject="Synthetic",
        body="Synthetic",
    )

    assert outcome.attempt_status == "reconciliation_required"
    assert outcome.provider_contacted is True
    assert outcome.provider_accepted is False
    assert outcome.provider_message_id is None


class MalformedSuccessResponse:
    def raise_for_status(self):
        return None

    def json(self):
        raise ValueError("synthetic malformed response")


class MalformedSuccessClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, **kwargs):
        if url.endswith("/token"):
            return type("Token", (), {"raise_for_status": lambda self: None, "json": lambda self: {"access_token": "fixture"}})()
        return MalformedSuccessResponse()


def test_gmail_malformed_2xx_requires_reconciliation_and_denies_retry(client, monkeypatch):
    from app.send import smtp_adapter
    from app.send.smtp_adapter import GmailAdapter

    monkeypatch.setattr(smtp_adapter.httpx, "Client", MalformedSuccessClient)
    adapter = GmailAdapter(
        GmailApiTransport(client_id="id", client_secret="secret", refresh_token="refresh"),
        TransportResolution("gmail_api", "gmail_api", "explicit_injection", False),
    )
    outcome = asyncio.run(
        adapter.send_message(
            "recipient@recipient.test",
            "Synthetic",
            "Synthetic",
            "sender@finimatic.test",
            "",
        )
    )

    assert outcome.attempt_status == "reconciliation_required"
    assert outcome.provider_contacted is True
    assert outcome.provider_accepted is False
    assert outcome.provider_response_classification == "gmail_api_invalid_success_response"

    setup = _queued_send(client, email="phase11-malformed-gmail@recipient.test", dry_run=False)
    with SessionLocal() as db:
        result = asyncio.run(
            process_pending_queue(
                db,
                transport=GmailApiTransport(client_id="id", client_secret="secret", refresh_token="refresh"),
            )
        )
    retry = client.post(f"/api/queue/{setup['queue']['id']}/retry")
    assert result["reconciliation_required"] == 1
    assert retry.status_code == 409
    assert retry.json()["detail"] == "queue_retry_requires_reconciliation"


class MissingProfileEmailClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, **kwargs):
        return type("Token", (), {"raise_for_status": lambda self: None, "json": lambda self: {"access_token": "fixture"}})()

    def get(self, url, **kwargs):
        return type("Profile", (), {"raise_for_status": lambda self: None, "json": lambda self: {}})()


def test_gmail_profile_without_identity_is_not_verified(monkeypatch):
    from app.send import smtp_adapter

    monkeypatch.setattr(smtp_adapter.httpx, "Client", MissingProfileEmailClient)
    with pytest.raises(ValueError, match="did not return an email identity"):
        GmailApiTransport(client_id="id", client_secret="secret", refresh_token="refresh").verify(
            "sender@finimatic.test",
            "",
        )


class AcceptedSMTP:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def login(self, sender, password):
        return None

    def send_message(self, message):
        return {}


def test_smtp_acceptance_has_explicit_contract(monkeypatch):
    from app.send import smtp_adapter

    monkeypatch.setattr(smtp_adapter.smtplib, "SMTP_SSL", AcceptedSMTP)
    outcome = SMTPTransport().send(
        sender="sender@finimatic.test",
        password="fixture",
        to="recipient@recipient.test",
        subject="Synthetic",
        body="Synthetic",
    )

    assert outcome.provider_accepted is True
    assert outcome.provider_contacted is True
    assert outcome.provider_message_id is None
    assert outcome.tracking_message_id
    assert outcome.provider_response_classification == "smtp_transaction_completed"


class TimeoutTransport:
    transport_name = "smtp"

    def send(self, **kwargs):
        raise TimeoutError("ambiguous fixture timeout")


def test_ambiguous_timeout_enters_reconciliation_and_is_not_retried(client):
    setup = _queued_send(client, email="phase11-timeout@recipient.test", dry_run=False)
    transport = TimeoutTransport()

    with SessionLocal() as db:
        first = asyncio.run(process_pending_queue(db, transport=transport))
    with SessionLocal() as db:
        second = asyncio.run(process_pending_queue(db, transport=transport))

    queue = client.get(f"/api/queue/{setup['queue']['id']}").json()
    assert first["reconciliation_required"] == 1
    assert second["processed"] == 0
    assert queue["status"] == "reconciliation_required"
    assert queue["latest_attempt"]["provider_contacted"] is False
    _assert_no_provider_success_side_effects(setup["queue"]["id"], setup["contact"]["id"])


class DisconnectedSMTP:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def login(self, sender, password):
        return None

    def send_message(self, message):
        raise smtplib.SMTPServerDisconnected("connection lost after dispatch")


def test_smtp_disconnect_after_dispatch_requires_reconciliation(monkeypatch):
    from app.send import smtp_adapter
    from app.send.smtp_adapter import GmailAdapter

    monkeypatch.setattr(smtp_adapter.smtplib, "SMTP_SSL", DisconnectedSMTP)
    adapter = GmailAdapter(
        SMTPTransport(),
        TransportResolution("smtp", "smtp", "explicit_injection", False),
    )
    outcome = asyncio.run(
        adapter.send_message(
            "recipient@recipient.test",
            "Synthetic",
            "Synthetic",
            "sender@finimatic.test",
            "fixture",
        )
    )

    assert outcome.attempt_status == "reconciliation_required"
    assert outcome.provider_contacted is True
    assert outcome.provider_accepted is False


class ResetSMTP(DisconnectedSMTP):
    def send_message(self, message):
        raise ConnectionResetError("connection reset after dispatch")


def test_smtp_connection_reset_after_dispatch_requires_reconciliation(monkeypatch):
    from app.send import smtp_adapter
    from app.send.smtp_adapter import GmailAdapter

    monkeypatch.setattr(smtp_adapter.smtplib, "SMTP_SSL", ResetSMTP)
    adapter = GmailAdapter(
        SMTPTransport(),
        TransportResolution("smtp", "smtp", "explicit_injection", False),
    )
    outcome = asyncio.run(
        adapter.send_message(
            "recipient@recipient.test",
            "Synthetic",
            "Synthetic",
            "sender@finimatic.test",
            "fixture",
        )
    )

    assert outcome.attempt_status == "reconciliation_required"
    assert outcome.provider_contacted is True
    assert outcome.provider_accepted is False


def test_stale_processing_attempt_requires_reconciliation_without_provider_call(client):
    setup = _queued_send(client, email="phase11-stale@recipient.test", dry_run=False)
    with SessionLocal() as db:
        queue = db.get(SendQueue, setup["queue"]["id"])
        queue.status = "processing"
        db.add(
            SendAttempt(
                queue_id=queue.id,
                contact_id=queue.contact_id,
                draft_id=queue.draft_id,
                idempotency_key=queue.idempotency_key,
                status="attempting",
                sender_identity="sender@finimatic.test",
                configured_transport="smtp",
                effective_transport="smtp",
                transport_source="test_fixture",
                simulated=False,
                provider_contacted=False,
                provider_accepted=False,
                created_at=utcnow() - timedelta(minutes=10),
            )
        )
        db.commit()
        result = asyncio.run(process_pending_queue(db))

    queue = client.get(f"/api/queue/{setup['queue']['id']}").json()
    assert result["processed"] == 0
    assert queue["status"] == "reconciliation_required"
    assert len(client.app.state.transport.sent) == 0


def test_orphaned_processing_claim_requires_reconciliation_without_provider_call(client):
    setup = _queued_send(client, email="phase11-orphaned-claim@recipient.test", dry_run=False)
    with SessionLocal() as db:
        queue = db.get(SendQueue, setup["queue"]["id"])
        queue.status = "processing"
        queue.processing_started_at = utcnow() - timedelta(minutes=10)
        db.commit()
        result = asyncio.run(process_pending_queue(db))

    queue = client.get(f"/api/queue/{setup['queue']['id']}").json()
    assert result["processed"] == 0
    assert queue["status"] == "reconciliation_required"
    assert queue["latest_attempt"]["error_code"] == "orphaned_processing_claim"
    assert len(client.app.state.transport.sent) == 0


class InvalidGmailAcceptanceTransport:
    transport_name = "gmail_api"

    def send(self, **kwargs):
        resolution = kwargs["resolution"]
        return SendOutcome(
            attempt_status="provider_accepted",
            configured_transport=resolution.configured_transport,
            effective_transport=resolution.effective_transport,
            transport_source=resolution.transport_source,
            simulated=False,
            provider_contacted=True,
            provider_accepted=True,
            provider_message_id="18f0a1b2c3d4e5f6",
            provider_response_classification="gmail_api_accepted",
        )


class SpoofedFakeAcceptanceTransport:
    transport_name = "fake"

    def send(self, **kwargs):
        resolution = kwargs["resolution"]
        return SendOutcome(
            attempt_status="provider_accepted",
            configured_transport=resolution.configured_transport,
            effective_transport=resolution.effective_transport,
            transport_source=resolution.transport_source,
            simulated=False,
            provider_contacted=True,
            provider_accepted=True,
            provider_message_id="spoofed-provider-id",
            provider_response_classification="spoofed_acceptance",
        )


def test_injected_gmail_acceptance_without_native_id_cannot_create_success_side_effects(client):
    setup = _queued_send(client, email="phase11-invalid-gmail-id@recipient.test", dry_run=False)
    with SessionLocal() as db:
        result = asyncio.run(process_pending_queue(db, transport=InvalidGmailAcceptanceTransport()))

    queue = client.get(f"/api/queue/{setup['queue']['id']}").json()
    assert result["provider_accepted"] == 0
    assert result["reconciliation_required"] == 1
    assert queue["status"] == "reconciliation_required"
    assert queue["latest_attempt"]["error_code"] == "untrusted_provider_adapter"
    _assert_no_provider_success_side_effects(setup["queue"]["id"], setup["contact"]["id"])


def test_spoofed_fake_adapter_cannot_claim_provider_acceptance(client):
    setup = _queued_send(client, email="phase11-spoofed-fake@recipient.test", dry_run=False)
    with SessionLocal() as db:
        result = asyncio.run(process_pending_queue(db, transport=SpoofedFakeAcceptanceTransport()))

    queue = client.get(f"/api/queue/{setup['queue']['id']}").json()
    assert result["provider_accepted"] == 0
    assert result["blocked"] == 1
    assert queue["status"] == "blocked"
    assert queue["latest_attempt"]["simulated"] is True
    assert queue["latest_attempt"]["provider_contacted"] is False
    assert queue["latest_attempt"]["provider_accepted"] is False
    assert queue["latest_attempt"]["error_code"] == "effective_transport_simulated"
    _assert_no_provider_success_side_effects(setup["queue"]["id"], setup["contact"]["id"])


def test_legacy_sent_row_without_provider_acceptance_is_explicitly_reclassified(client):
    setup = _queued_send(client, email="phase11-legacy-false-success@recipient.test", dry_run=False)
    with SessionLocal() as db:
        queue = db.get(SendQueue, setup["queue"]["id"])
        queue.status = "sent"
        db.add(
            SendAttempt(
                queue_id=queue.id,
                contact_id=queue.contact_id,
                draft_id=queue.draft_id,
                idempotency_key=queue.idempotency_key,
                status="success",
                sender_identity="sender@finimatic.test",
                effective_transport="fake",
                simulated=True,
                provider_contacted=False,
                provider_accepted=None,
                created_at=utcnow() - timedelta(days=1),
            )
        )
        db.commit()

    queue = client.get(f"/api/queue/{setup['queue']['id']}").json()
    assert queue["status"] == "reconciliation_required"
    assert queue["stored_status"] == "sent"
    assert "lacks durable provider acceptance" in queue["classification_note"]


def test_latest_attempt_is_selected_by_created_at(client):
    setup = _queued_send(client, email="phase11-latest-attempt@recipient.test", dry_run=False)
    with SessionLocal() as db:
        queue = db.get(SendQueue, setup["queue"]["id"])
        db.add_all(
            [
                SendAttempt(
                    id="z-old",
                    queue_id=queue.id,
                    contact_id=queue.contact_id,
                    draft_id=queue.draft_id,
                    idempotency_key=f"{queue.idempotency_key}:old",
                    status="failed",
                    sender_identity="sender@finimatic.test",
                    simulated=False,
                    provider_contacted=False,
                    provider_accepted=False,
                    error_code="old_failure",
                    created_at=utcnow() - timedelta(minutes=2),
                ),
                SendAttempt(
                    id="a-new",
                    queue_id=queue.id,
                    contact_id=queue.contact_id,
                    draft_id=queue.draft_id,
                    idempotency_key=f"{queue.idempotency_key}:new",
                    status="blocked",
                    sender_identity="sender@finimatic.test",
                    simulated=True,
                    provider_contacted=False,
                    provider_accepted=False,
                    error_code="new_block",
                    created_at=utcnow(),
                ),
            ]
        )
        db.commit()

    queue = client.get(f"/api/queue/{setup['queue']['id']}").json()
    assert queue["latest_attempt"]["attempt_id"] == "a-new"
    assert queue["latest_attempt"]["error_code"] == "new_block"


def test_followup_approval_does_not_schedule_next_sequence_before_acceptance(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = client.post(
        "/api/contacts",
        json={"email": "phase11-followup-sequence@recipient.test", "creator_name": "Follow Up", "source": "test"},
    ).json()
    with SessionLocal() as db:
        draft = Draft(
            contact_id=contact["id"],
            subject="Sequence two",
            body="Synthetic follow-up.",
            ai_provider="manual",
            warnings="[]",
            source="followup",
            approved=False,
        )
        db.add(draft)
        db.flush()
        row = FollowUpSequence(
            contact_id=contact["id"],
            sequence_num=2,
            due_at=utcnow(),
            pending_draft_id=draft.id,
            status="draft_ready",
        )
        db.add(row)
        db.commit()
        sequence_id = row.id

    response = client.post(f"/api/followups/{sequence_id}/approve-draft")
    assert response.status_code == 409
    assert response.json()["detail"] == "prior_sequence_not_provider_accepted"
    with SessionLocal() as db:
        assert db.query(SendQueue).filter_by(contact_id=contact["id"], sequence_num=2).count() == 0
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=3).count() == 0


def test_sender_identity_change_invalidates_canary_verification(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    assert client.get("/api/settings").json()["canary_verified"] is True

    updated = client.post("/api/settings", json={"gmail_user": "changed-sender@finimatic.test"}).json()
    assert updated["canary_verified"] is False
    assert updated["sender_readiness"] == "configured"


def test_canary_reverification_uses_new_key_after_identity_or_recipient_change(client):
    configure_sender(client, canary_verified=False, dry_run=False)

    first = client.post("/api/canary/send").json()
    assert first["status"] == "provider_accepted"
    assert len(client.app.state.transport.sent) == 1

    updated = client.post("/api/settings", json={"gmail_app_password": "replacement-fixture"}).json()
    assert updated["canary_verified"] is False
    second = client.post("/api/canary/send").json()
    assert second["status"] == "provider_accepted"
    assert len(client.app.state.transport.sent) == 2

    updated = client.post("/api/settings", json={"report_recipient": "new-report@recipient.test"}).json()
    assert updated["canary_verified"] is False
    third = client.post("/api/canary/send").json()
    assert third["status"] == "provider_accepted"
    assert len(client.app.state.transport.sent) == 3


def test_client_cannot_self_assert_canary_or_sender_readiness(client):
    configure_sender(client, canary_verified=False, dry_run=False)

    settings = client.post(
        "/api/settings",
        json={"canary_verified": True, "sender_readiness": "canary_verified"},
    ).json()

    assert settings["canary_verified"] is False
    assert settings["sender_readiness"] == "configured"


def test_duplicate_queue_processing_calls_provider_once(client):
    setup = _queued_send(client, email="phase11-idempotent@recipient.test", dry_run=False)

    first = client.post("/api/queue/process").json()
    second = client.post("/api/queue/process").json()

    assert first["provider_accepted"] == 1
    assert second["processed"] == 0
    assert len(client.app.state.transport.sent) == 1
    with SessionLocal() as db:
        assert db.query(SendAttempt).filter_by(queue_id=setup["queue"]["id"], provider_accepted=True).count() == 1
        assert db.query(ConversationMessage).filter_by(contact_id=setup["contact"]["id"], direction="outbound").count() == 1
        assert db.query(FollowUpSequence).filter_by(contact_id=setup["contact"]["id"]).count() == 1


def test_audit_contains_transport_truth_without_secret_values(client):
    setup = _queued_send(client, email="phase11-audit@recipient.test", dry_run=True)
    client.post("/api/queue/process")

    events = client.get("/api/audit").json()["items"]
    event = next(row for row in events if row["event_type"] == "send.simulated" and row["entity_id"] == setup["queue"]["id"])
    payload = event["payload"]
    assert payload["simulated"] is True
    assert payload["provider_contacted"] is False
    assert payload["provider_accepted"] is False
    serialized = json.dumps(payload)
    assert "valid-app-password" not in serialized
    assert "groq-test-one" not in serialized


def test_authorization_seam_protects_operational_routes_and_keeps_health_public(client):
    client.app.state.authorization_checker = lambda request: False
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/security/status").status_code == 200
    assert client.get("/api/settings").status_code == 401
    assert client.post("/api/queue/process").status_code == 401

    client.app.state.authorization_checker = lambda request: {
        "subject": "test-operator",
        "roles": ("operator", "admin"),
        "authenticated": True,
        "authorized": True,
    }
    assert client.get("/api/settings").status_code == 200


def test_authorization_seam_rejects_truthy_unauthenticated_or_unauthorized_results(client):
    client.app.state.authorization_checker = lambda request: {"authenticated": False, "authorized": True}
    assert client.get("/api/settings").status_code == 401

    client.app.state.authorization_checker = lambda request: {"authenticated": True, "authorized": False}
    assert client.get("/api/settings").status_code == 403

    client.app.state.authorization_checker = lambda request: {"authenticated": True, "authorized": True}
    assert client.get("/api/settings").status_code == 401

    client.app.state.authorization_production_ready = True
    status = client.get("/api/security/status").json()
    assert status["checker_configured"] is True
    assert status["release_blocked"] is False
    assert status["authentication_enforced"] is True
    assert status["authorization_enforced"] is True


def test_assistant_dry_run_persists_simulated_transport_truth(client, monkeypatch):
    from test_agent import SESSION_A, _pending_draft

    pending = _pending_draft(client, monkeypatch, email="phase11-agent-dry-run@recipient.test")
    client.post("/api/settings", json={"dry_run": True})
    response = client.post(
        "/api/agent/confirm",
        json={"session_token": SESSION_A, "action_id": pending["pending_action"]["action_id"]},
    ).json()

    assert response["error_code"] == "dry_run"
    with SessionLocal() as db:
        attempt = db.query(SendAttempt).filter_by(queue_id="agent").one()
        assert attempt.status == "simulated"
        assert attempt.simulated is True
        assert attempt.provider_contacted is False
        assert attempt.provider_accepted is False


def test_auto_reply_approval_dry_run_persists_simulated_transport_truth(client):
    configure_sender(client, canary_verified=True, dry_run=True)
    contact = client.post(
        "/api/contacts",
        json={"email": "phase11-auto-reply-dry-run@recipient.test", "creator_name": "Auto Reply", "source": "test"},
    ).json()
    with SessionLocal() as db:
        draft = Draft(
            contact_id=contact["id"],
            subject="Synthetic approval",
            body="Synthetic dry-run approval.",
            ai_provider="fake",
            warnings="[]",
            source="auto_reply_proposed",
            approved=False,
        )
        db.add(draft)
        db.commit()
        draft_id = draft.id

    prepared = client.post(
        f"/api/auto-reply/approve/{draft_id}",
        json={"session_token": PHASE12_SESSION},
    )
    response = client.post(
        f"/api/auto-reply/confirm/{draft_id}",
        json={
            "session_token": PHASE12_SESSION,
            "action_id": prepared.json()["pending_action"]["action_id"],
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "simulated"
    with SessionLocal() as db:
        attempt = db.query(SendAttempt).filter_by(queue_id="auto_reply", draft_id=draft_id).one()
        assert attempt.status == "simulated"
        assert attempt.simulated is True
        assert attempt.provider_contacted is False
        assert attempt.provider_accepted is False


def test_security_and_settings_endpoints_expose_no_raw_credentials(client):
    configure_sender(client)
    settings = client.get("/api/settings")
    security = client.get("/api/security/status")

    assert settings.status_code == 200
    assert security.status_code == 200
    serialized = settings.text + security.text
    assert "valid-app-password" not in serialized
    assert "groq-test-one" not in serialized
    assert settings.json()["release_blocked"] is False
    assert settings.json()["api_security_enforced"] is True


class ConnectTimeoutClient:
    def __init__(self, timeout):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, **kwargs):
        if url.endswith("/token"):
            return type(
                "Token",
                (),
                {"raise_for_status": lambda self: None, "json": lambda self: {"access_token": "fixture"}},
            )()
        raise httpx.ConnectTimeout("connect failed", request=httpx.Request("POST", url))


def test_gmail_connect_timeout_does_not_claim_provider_contact(monkeypatch):
    from app.send import smtp_adapter
    from app.send.smtp_adapter import GmailAdapter

    monkeypatch.setattr(smtp_adapter.httpx, "Client", ConnectTimeoutClient)
    adapter = GmailAdapter(
        GmailApiTransport(client_id="id", client_secret="secret", refresh_token="refresh"),
        TransportResolution("gmail_api", "gmail_api", "persisted:email_transport", False),
    )
    outcome = asyncio.run(
        adapter.send_message(
            "recipient@recipient.test",
            "Synthetic",
            "Synthetic",
            "sender@finimatic.test",
            "",
        )
    )

    assert outcome.attempt_status == "failed"
    assert outcome.provider_contacted is False
    assert outcome.provider_accepted is False
    assert outcome.error_code == "provider_not_contacted"


def test_legacy_false_success_migration_corrects_all_derived_surfaces(client):
    from app.db import session as db_session

    with SessionLocal() as db:
        contact = Contact(email="legacy-false-success@recipient.test", creator_name="Legacy", source="test", status="sent")
        db.add(contact)
        db.flush()
        draft = Draft(
            contact_id=contact.id,
            subject="Legacy false success",
            body="Historical evidence retained.",
            ai_provider="manual",
            warnings="[]",
            source="manual",
            approved=True,
        )
        db.add(draft)
        db.flush()
        queue = SendQueue(
            contact_id=contact.id,
            draft_id=draft.id,
            sequence_num=1,
            scheduled_at=utcnow() - timedelta(days=1),
            status="sent",
            idempotency_key="legacy-false-success-key",
        )
        db.add(queue)
        db.flush()
        attempt = SendAttempt(
            queue_id=queue.id,
            contact_id=contact.id,
            draft_id=draft.id,
            idempotency_key=queue.idempotency_key,
            provider_msg_id="fake-legacy",
            status="success",
            sender_identity="sender@finimatic.test",
            effective_transport="fake",
            simulated=True,
            provider_contacted=False,
            provider_accepted=None,
            sent_at=utcnow() - timedelta(days=1),
            created_at=None,
        )
        db.add(attempt)
        db.add(
            ConversationMessage(
                contact_id=contact.id,
                direction="outbound",
                subject=draft.subject,
                body=draft.body,
                source="queue",
                external_message_id="fake-legacy",
                occurred_at=utcnow() - timedelta(days=1),
            )
        )
        db.add(
            FollowUpSequence(
                contact_id=contact.id,
                sequence_num=2,
                due_at=utcnow(),
                draft_id=draft.id,
                status="due",
            )
        )
        db.commit()
        db.execute(text("UPDATE send_attempts SET created_at = NULL WHERE id = :id"), {"id": attempt.id})
        db.commit()
        contact_id = contact.id
        queue_id = queue.id
        attempt_id = attempt.id

    db_session._apply_lightweight_migrations()

    with SessionLocal() as db:
        contact = db.get(Contact, contact_id)
        queue = db.get(SendQueue, queue_id)
        attempt = db.get(SendAttempt, attempt_id)
        message = db.query(ConversationMessage).filter_by(contact_id=contact_id).one()
        followup = db.query(FollowUpSequence).filter_by(contact_id=contact_id, sequence_num=2).one()
        assert contact.status == "approved"
        assert queue.status == "reconciliation_required"
        assert attempt.status == "reconciliation_required"
        assert attempt.provider_accepted is False
        assert attempt.created_at is not None
        assert message.source == "historical_unverified_queue"
        assert followup.status == "stopped"
        assert followup.stop_reason == "LEGACY_PROVIDER_ACCEPTANCE_UNVERIFIED"
