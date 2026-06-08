from __future__ import annotations

import json
import asyncio
import threading
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.agent.memory import get_or_create_session
from app.agent.pending import create_generic_pending_action, create_pending_action
from app.conversations.auto_reply_service import AutoReplyService
from app.core.time import utcnow
from app.db.models import (
    AuditEvent,
    Contact,
    ConversationMessage,
    Draft,
    FollowUpSequence,
    PendingAgentAction,
    PendingEmailActionRow,
    Reply,
    SendAttempt,
    SendQueue,
)
from app.db.session import SessionLocal
from app.followups.service import approve_followup_draft
from app.replies.service import create_reply_record
from app.send.outcomes import SendOutcome
from app.send.queue_worker import process_pending_queue, reconcile_queue_entry
from app.send.sequence import sequence_prerequisite_met
from app.send.smtp_adapter import SMTPTransport
from app.send.stop_service import stop_contact_send_work
from conftest import configure_sender


SESSION_A = "phase12-session-alpha"


def _contact(client, email: str = "phase12-contact@recipient.test") -> dict:
    return client.post(
        "/api/contacts",
        json={"email": email, "creator_name": "Phase 12", "source": "test"},
    ).json()


def _draft(client, contact_id: str, *, subject: str = "Phase 12", body: str = "Synthetic body") -> dict:
    return client.post(
        "/api/drafts",
        json={"contact_id": contact_id, "subject": subject, "body": body},
    ).json()


def _dispatched_followup_work(client, email: str = "phase12-stop@recipient.test") -> tuple[dict, str, str]:
    contact = _contact(client, email)
    with SessionLocal() as db:
        draft = Draft(
            contact_id=contact["id"],
            subject="Sequence 2",
            body="Synthetic follow-up",
            ai_provider="manual",
            warnings="[]",
            source="followup",
            approved=True,
            approved_at=utcnow(),
        )
        db.add(draft)
        db.flush()
        queue = SendQueue(
            contact_id=contact["id"],
            draft_id=draft.id,
            sequence_num=2,
            scheduled_at=utcnow(),
            status="pending",
            idempotency_key=f"phase12-stop-{contact['id']}",
        )
        followup = FollowUpSequence(
            contact_id=contact["id"],
            sequence_num=2,
            due_at=utcnow(),
            draft_id=draft.id,
            pending_draft_id=draft.id,
            status="dispatched",
        )
        db.add_all([queue, followup])
        db.flush()

        session = get_or_create_session(SESSION_A, db)
        create_pending_action(session.id, draft.id, contact["id"], draft.subject, draft.body, db)
        create_generic_pending_action(
            session.id,
            capability="crm_preview_sync",
            entity_type="contact",
            entity_id=contact["id"],
            params={"provider": "google_sheets", "contact_id": contact["id"]},
            source_label="Integrations",
            goal="Synthetic pending action",
            evidence_summary="Synthetic",
            policy_result="allowed",
            proposed_side_effect="Synthetic",
            confirmation_prompt="Confirm synthetic action?",
            db=db,
        )
        db.commit()
        return contact, queue.id, followup.id


def test_positive_reply_atomically_cancels_dispatched_queue_followup_and_pending_actions(client):
    contact, queue_id, followup_id = _dispatched_followup_work(client)

    with SessionLocal() as db:
        row, created = create_reply_record(
            db,
            db.get(Contact, contact["id"]),
            "reply",
            "Interested. Please stop the sequence and discuss this reply.",
            subject="Re: Phase 12",
            external_message_id="<phase12-positive-stop@recipient.test>",
            stop_followups=True,
            intent="positive_interest",
        )
        assert row.id
        assert created is True
        db.commit()

    with SessionLocal() as db:
        queue = db.get(SendQueue, queue_id)
        followup = db.get(FollowUpSequence, followup_id)
        contact_row = db.get(Contact, contact["id"])
        assert contact_row.status == "conversation_active"
        assert queue.status == "cancelled"
        assert "RECIPIENT_REPLIED" in json.loads(queue.policy_block_reasons or "[]")
        assert followup.status == "stopped"
        assert followup.stop_reason == "RECIPIENT_REPLIED"
        assert db.query(PendingEmailActionRow).filter_by(contact_id=contact["id"], consumed=False).count() == 0
        assert (
            db.query(PendingAgentAction)
            .filter_by(entity_type="contact", entity_id=contact["id"], consumed=False)
            .count()
            == 0
        )
        events = db.query(AuditEvent).filter_by(event_type="send_work.cancelled", entity_id=contact["id"]).all()
        assert len(events) == 1
        payload = json.loads(events[0].payload or "{}")
        assert queue_id in payload["queue_ids"]
        assert followup_id in payload["followup_ids"]


def test_duplicate_reply_is_idempotent_at_stop_barrier(client):
    contact, queue_id, _followup_id = _dispatched_followup_work(client, "phase12-duplicate@recipient.test")
    kwargs = {
        "classified_as": "reply",
        "raw_summary": "Interested.",
        "subject": "Re: Phase 12",
        "external_message_id": "<phase12-duplicate-reply@recipient.test>",
        "stop_followups": True,
        "intent": "positive_interest",
    }
    with SessionLocal() as db:
        db_contact = db.get(Contact, contact["id"])
        first, created_first = create_reply_record(db, db_contact, **kwargs)
        db.commit()
        assert created_first is True
        assert first.id

    with SessionLocal() as db:
        queue = db.get(SendQueue, queue_id)
        generation = db.get(Contact, contact["id"]).send_stop_generation
        queue.status = "pending"
        queue.policy_block_reasons = "[]"
        db.commit()

    with SessionLocal() as db:
        duplicate, created_duplicate = create_reply_record(db, db.get(Contact, contact["id"]), **kwargs)
        db.commit()
        assert duplicate.id == first.id
        assert created_duplicate is False

    with SessionLocal() as db:
        assert db.get(SendQueue, queue_id).status == "pending"
        assert db.get(Contact, contact["id"]).send_stop_generation == generation
        assert db.query(AuditEvent).filter_by(event_type="send_work.cancelled", entity_id=contact["id"]).count() == 1


def test_conversation_active_reply_blocks_cold_queue_policy(client):
    contact = _contact(client, "phase12-policy@recipient.test")
    draft = _draft(client, contact["id"])
    with SessionLocal() as db:
        db_contact = db.get(Contact, contact["id"])
        db_contact.status = "conversation_active"
        create_reply_record(
            db,
            db_contact,
            "reply",
            "Interested.",
            external_message_id="<phase12-policy-reply@recipient.test>",
            intent="positive_interest",
        )
        queue = SendQueue(
            contact_id=contact["id"],
            draft_id=draft["id"],
            sequence_num=1,
            scheduled_at=utcnow(),
            status="pending",
            idempotency_key="phase12-policy-stop",
        )
        db.add(queue)
        db.commit()
        queue_id = queue.id

    result = client.post("/api/queue/process")
    assert result.status_code == 200
    with SessionLocal() as db:
        queue = db.get(SendQueue, queue_id)
        assert queue.status in {"blocked", "cancelled"}
        assert db.query(SendAttempt).filter_by(queue_id=queue_id, provider_accepted=True).count() == 0


def test_sequence_three_cannot_be_approved_before_sequence_two_provider_acceptance(client):
    contact = _contact(client, "phase12-sequence@recipient.test")
    draft = _draft(client, contact["id"], subject="Sequence 3")
    response = client.post(f"/api/drafts/{draft['id']}/approve", json={"sequence_num": 3})

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "prior_sequence_not_provider_accepted"
    with SessionLocal() as db:
        assert db.query(SendQueue).filter_by(contact_id=contact["id"], sequence_num=3).count() == 0


def test_conversation_send_creates_confirmation_without_provider_call(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _contact(client, "phase12-conversation@recipient.test")
    response = client.post(
        f"/api/conversations/{contact['id']}/send",
        json={
            "session_token": SESSION_A,
            "subject": "Governed reply",
            "body": "Synthetic governed conversation reply.",
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "pending_confirmation"
    assert payload["pending_action"]["capability"] == "conversation_send_reply"
    assert len(client.app.state.transport.sent) == 0
    with SessionLocal() as db:
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 0
        assert db.query(SendAttempt).filter_by(queue_id="conversation").count() == 0


def test_auto_reply_approval_creates_confirmation_without_provider_call(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _contact(client, "phase12-auto-reply@recipient.test")
    with SessionLocal() as db:
        draft = Draft(
            contact_id=contact["id"],
            subject="Governed auto reply",
            body="Synthetic governed auto reply.",
            ai_provider="fake",
            warnings="[]",
            source="auto_reply_proposed",
            approved=False,
            notes="auto_reply_reply_id:phase12-reply",
        )
        db.add(draft)
        db.commit()
        draft_id = draft.id

    response = client.post(
        f"/api/auto-reply/approve/{draft_id}",
        json={"session_token": SESSION_A},
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "pending_confirmation"
    assert payload["pending_action"]["capability"] == "auto_reply_approve"
    assert len(client.app.state.transport.sent) == 0
    with SessionLocal() as db:
        assert db.query(SendAttempt).filter_by(queue_id="auto_reply", draft_id=draft_id).count() == 0


def test_autonomous_mode_cannot_be_enabled_by_direct_settings_write(client):
    response = client.post(
        "/api/settings",
        json={"auto_reply_enabled": True, "auto_reply_mode": "autonomous"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "autonomous_confirmation_required"
    settings = client.get("/api/settings").json()
    assert settings["auto_reply_enabled"] is False
    assert settings["auto_reply_kill_switch"] is True


def test_reply_after_sequence_acceptance_stops_future_due_work(client):
    contact = _contact(client, "phase12-after-acceptance@recipient.test")
    draft = _draft(client, contact["id"])
    with SessionLocal() as db:
        accepted = SendAttempt(
            queue_id="phase12-seq1",
            contact_id=contact["id"],
            draft_id=draft["id"],
            idempotency_key="phase12-seq1-key",
            provider_msg_id="provider-native-phase12",
            status="provider_accepted",
            sender_identity="phase12-sender@finimatic.test",
            configured_transport="gmail_api",
            effective_transport="gmail_api",
            transport_source="test",
            simulated=False,
            provider_contacted=True,
            provider_accepted=True,
            sent_at=utcnow(),
        )
        followup = FollowUpSequence(
            contact_id=contact["id"],
            sequence_num=2,
            due_at=utcnow() + timedelta(days=1),
            draft_id=draft["id"],
            status="due",
        )
        db.add_all([accepted, followup])
        db.flush()
        followup_id = followup.id
        create_reply_record(
            db,
            db.get(Contact, contact["id"]),
            "reply",
            "Interested.",
            external_message_id="<phase12-after-acceptance-reply@recipient.test>",
            stop_followups=True,
            intent="positive_interest",
        )
        db.commit()

    with SessionLocal() as db:
        followup = db.get(FollowUpSequence, followup_id)
        assert followup.status == "stopped"
        assert followup.stop_reason == "RECIPIENT_REPLIED"


def _approved_queue(client, email: str, *, dry_run: bool = False) -> tuple[dict, dict]:
    configure_sender(client, canary_verified=True, dry_run=dry_run)
    contact = _contact(client, email)
    draft = _draft(client, contact["id"], subject="Sequence 1")
    approved = client.post("/api/drafts/approve-bulk", json={"draft_ids": [draft["id"]]})
    assert approved.status_code == 200
    assert approved.json()["queued"] == 1
    queue = client.get("/api/queue").json()["items"][-1]
    return contact, {"queue_id": queue["id"], "queue": queue}


def test_manual_suppression_cancels_dispatched_work(client):
    contact, queue_id, followup_id = _dispatched_followup_work(client, "phase12-suppression@recipient.test")

    response = client.post(
        "/api/suppressions",
        json={"email": contact["email"], "reason": "manual", "source": "test"},
    )
    assert response.status_code == 200

    with SessionLocal() as db:
        assert db.get(Contact, contact["id"]).status == "suppressed"
        assert db.get(SendQueue, queue_id).status == "cancelled"
        assert db.get(FollowUpSequence, followup_id).status == "stopped"
        assert db.query(PendingEmailActionRow).filter_by(contact_id=contact["id"], consumed=False).count() == 0


def test_sequence_three_appears_once_only_after_sequence_two_provider_acceptance(client):
    contact, _approved = _approved_queue(client, "phase12-seq-chain@recipient.test")
    first = client.post("/api/queue/process").json()
    assert first["provider_accepted"] == 1

    with SessionLocal() as db:
        sequence_two = db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=2).one()
        sequence_two.due_at = utcnow() - timedelta(seconds=1)
        db.commit()
        sequence_two_id = sequence_two.id

    assert client.post("/api/followups/process").status_code == 200
    approved_two = client.post(f"/api/followups/{sequence_two_id}/approve-draft")
    assert approved_two.status_code == 200
    with SessionLocal() as db:
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=3).count() == 1
        assert db.get(FollowUpSequence, sequence_two_id).status == "provider_accepted"

    duplicate = client.post("/api/queue/process").json()
    assert duplicate["processed"] == 0
    with SessionLocal() as db:
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=3).count() == 1
        assert db.get(FollowUpSequence, sequence_two_id).status == "provider_accepted"


def test_simulated_sequence_two_creates_no_sequence_three(client):
    contact, _approved = _approved_queue(client, "phase12-seq-simulated@recipient.test", dry_run=False)
    client.post("/api/queue/process")
    with SessionLocal() as db:
        sequence_two = db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=2).one()
        sequence_two.due_at = utcnow() - timedelta(seconds=1)
        db.commit()
        sequence_two_id = sequence_two.id
    client.post("/api/followups/process")
    with SessionLocal() as db:
        approve_followup_draft(db, sequence_two_id, immediate=False)
    client.post("/api/settings", json={"dry_run": True})

    result = client.post("/api/queue/process").json()
    assert result["simulated"] == 1
    with SessionLocal() as db:
        assert db.get(FollowUpSequence, sequence_two_id).status == "simulated"
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=3).count() == 0


def _prepare_conversation(client, contact_id: str, *, token: str = SESSION_A, body: str = "Synthetic governed reply.") -> dict:
    response = client.post(
        f"/api/conversations/{contact_id}/send",
        json={"session_token": token, "subject": "Governed reply", "body": body},
    )
    assert response.status_code == 202
    return response.json()["pending_action"]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("consumed", "consumed"),
        ("expired", "expired"),
        ("session", "session_mismatch"),
        ("target", "target_mismatch"),
        ("hash", "hash_mismatch"),
    ],
)
def test_conversation_confirmation_failures_are_closed(client, mutation, expected):
    configure_sender(client, canary_verified=True, dry_run=True)
    contact = _contact(client, f"phase12-confirm-{mutation}@recipient.test")
    action = _prepare_conversation(client, contact["id"])
    token = SESSION_A
    target_id = contact["id"]
    body = "Synthetic governed reply."
    if mutation == "consumed":
        cancel = client.post(
            f"/api/governed-actions/{action['action_id']}/cancel",
            json={"session_token": SESSION_A},
        )
        assert cancel.status_code == 200
    elif mutation == "expired":
        with SessionLocal() as db:
            row = db.get(PendingAgentAction, action["action_id"])
            row.expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
    elif mutation == "session":
        token = "phase12-session-other"
    elif mutation == "target":
        target_id = _contact(client, "phase12-confirm-other@recipient.test")["id"]
    elif mutation == "hash":
        body = "Changed after review."

    response = client.post(
        f"/api/conversations/{target_id}/confirm-send",
        json={
            "session_token": token,
            "action_id": action["action_id"],
            "subject": "Governed reply",
            "body": body,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == expected
    assert len(client.app.state.transport.sent) == 0


def test_duplicate_conversation_confirmation_cannot_duplicate_provider_call(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _contact(client, "phase12-confirm-duplicate@recipient.test")
    action = _prepare_conversation(client, contact["id"])
    payload = {
        "session_token": SESSION_A,
        "action_id": action["action_id"],
        "subject": "Governed reply",
        "body": "Synthetic governed reply.",
    }

    first = client.post(f"/api/conversations/{contact['id']}/confirm-send", json=payload)
    second = client.post(f"/api/conversations/{contact['id']}/confirm-send", json=payload)

    assert first.status_code == 200
    assert first.json()["status"] == "provider_accepted"
    assert second.status_code == 409
    assert second.json()["detail"] == "consumed"
    assert len(client.app.state.transport.sent) == 1
    with SessionLocal() as db:
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 1


def test_delete_restore_invalidates_generic_conversation_confirmation(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _contact(client, "phase14-delete-restore@recipient.test")
    action = _prepare_conversation(client, contact["id"])

    assert client.delete(f"/api/contacts/{contact['id']}").status_code == 200
    assert client.post(f"/api/contacts/{contact['id']}/restore").status_code == 200

    response = client.post(
        f"/api/conversations/{contact['id']}/confirm-send",
        json={
            "session_token": SESSION_A,
            "action_id": action["action_id"],
            "subject": "Governed reply",
            "body": "Synthetic governed reply.",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "consumed"
    assert len(client.app.state.transport.sent) == 0


def test_direct_bounced_status_cancels_queue_and_blocks_provider(client):
    contact, approved = _approved_queue(client, "phase14-direct-bounce@recipient.test", dry_run=False)

    response = client.patch(f"/api/contacts/{contact['id']}", json={"status": "bounced"})
    assert response.status_code == 200
    result = client.post("/api/queue/process").json()

    assert result["provider_accepted"] == 0
    assert len(client.app.state.transport.sent) == 0
    with SessionLocal() as db:
        queue = db.get(SendQueue, approved["queue_id"])
        assert queue.status == "cancelled"
        assert "RECIPIENT_BOUNCED" in json.loads(queue.policy_block_reasons)


def test_queue_rejects_nonpositive_sequence_and_foreign_draft(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    first = _contact(client, "phase14-queue-owner-a@recipient.test")
    second = _contact(client, "phase14-queue-owner-b@recipient.test")
    draft = _draft(client, first["id"], subject="Owner A")

    nonpositive = client.post(
        "/api/queue",
        json={"contact_id": first["id"], "draft_id": draft["id"], "sequence_num": 0},
    )
    foreign = client.post(
        "/api/queue",
        json={"contact_id": second["id"], "draft_id": draft["id"], "sequence_num": 1},
    )

    assert nonpositive.status_code == 422
    assert foreign.status_code == 409
    assert foreign.json()["detail"] == "draft_contact_mismatch"
    assert client.get("/api/queue").json()["total"] == 0
    assert len(client.app.state.transport.sent) == 0


def test_redacted_confirmation_values_do_not_hash_collide(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _contact(client, "phase14-hash-collision@recipient.test")
    action = _prepare_conversation(client, contact["id"], body="Use token=alpha.")

    response = client.post(
        f"/api/conversations/{contact['id']}/confirm-send",
        json={
            "session_token": SESSION_A,
            "action_id": action["action_id"],
            "subject": "Governed reply",
            "body": "Use token=beta.",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "hash_mismatch"
    assert len(client.app.state.transport.sent) == 0


def test_distinct_conversation_confirmations_share_one_provider_dispatch(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _contact(client, "phase12-confirm-concurrent@recipient.test")
    first_action = _prepare_conversation(client, contact["id"])
    second = client.post(
        f"/api/conversations/{contact['id']}/send",
        json={
            "session_token": "phase12-session-beta",
            "subject": "Governed reply",
            "body": "Synthetic governed reply.",
        },
    )
    assert second.status_code == 202
    second_action = second.json()["pending_action"]
    original_send = client.app.state.transport.send
    provider_entered = threading.Event()
    provider_release = threading.Event()

    def blocking_send(**kwargs):
        provider_entered.set()
        assert provider_release.wait(timeout=10)
        return original_send(**kwargs)

    client.app.state.transport.send = blocking_send
    first_result: dict = {}

    def confirm_first():
        response = client.post(
            f"/api/conversations/{contact['id']}/confirm-send",
            json={
                "session_token": SESSION_A,
                "action_id": first_action["action_id"],
                "subject": "Governed reply",
                "body": "Synthetic governed reply.",
            },
        )
        first_result["status_code"] = response.status_code
        first_result["body"] = response.json()

    worker = threading.Thread(target=confirm_first, daemon=True)
    worker.start()
    assert provider_entered.wait(timeout=10)
    competing = client.post(
        f"/api/conversations/{contact['id']}/confirm-send",
        json={
            "session_token": "phase12-session-beta",
            "action_id": second_action["action_id"],
            "subject": "Governed reply",
            "body": "Synthetic governed reply.",
        },
    )
    provider_release.set()
    worker.join(timeout=10)
    assert not worker.is_alive()

    assert first_result["status_code"] == 200
    assert first_result["body"]["status"] == "provider_accepted"
    assert competing.status_code == 409
    assert competing.json()["detail"] == "reconciliation_required"
    assert len(client.app.state.transport.sent) == 1
    with SessionLocal() as db:
        attempts = db.query(SendAttempt).filter_by(queue_id="conversation", contact_id=contact["id"]).all()
        assert len(attempts) == 1
        assert attempts[0].provider_accepted is True
        assert attempts[0].dispatch_lock_key
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 1


def test_conversation_attempt_is_durable_before_provider_call(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _contact(client, "phase12-pre-provider-attempt@recipient.test")
    action = _prepare_conversation(client, contact["id"])
    original_send = client.app.state.transport.send
    observed = {"attempt_id": None}

    def assert_attempt_persisted(**kwargs):
        with SessionLocal() as db:
            attempt = (
                db.query(SendAttempt)
                .filter_by(queue_id="conversation", contact_id=contact["id"], status="attempting")
                .one()
            )
            assert attempt.provider_contacted is False
            assert attempt.provider_accepted is False
            observed["attempt_id"] = attempt.id
        return original_send(**kwargs)

    client.app.state.transport.send = assert_attempt_persisted
    response = client.post(
        f"/api/conversations/{contact['id']}/confirm-send",
        json={
            "session_token": SESSION_A,
            "action_id": action["action_id"],
            "subject": "Governed reply",
            "body": "Synthetic governed reply.",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "provider_accepted"
    assert observed["attempt_id"]
    assert len(client.app.state.transport.sent) == 1


def test_reply_after_conversation_attempt_blocks_provider_dispatch(client, monkeypatch):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _contact(client, "phase12-conversation-stop-fence@recipient.test")
    action = _prepare_conversation(client, contact["id"])
    from app.conversations import router as conversations_router

    original_begin = conversations_router.begin_provider_attempt

    def inject_reply_after_attempt(db, **kwargs):
        attempt, state = original_begin(db, **kwargs)
        if state == "ready":
            create_reply_record(
                db,
                db.get(Contact, contact["id"]),
                "reply",
                "Stop before the governed conversation provider call.",
                external_message_id="<phase12-conversation-stop-fence@recipient.test>",
                stop_followups=True,
                intent="positive_interest",
            )
            db.commit()
        return attempt, state

    monkeypatch.setattr(conversations_router, "begin_provider_attempt", inject_reply_after_attempt)
    response = client.post(
        f"/api/conversations/{contact['id']}/confirm-send",
        json={
            "session_token": SESSION_A,
            "action_id": action["action_id"],
            "subject": "Governed reply",
            "body": "Synthetic governed reply.",
        },
    )

    assert response.status_code == 200
    assert response.json()["error_code"] == "policy_changed_before_provider_call"
    assert len(client.app.state.transport.sent) == 0
    with SessionLocal() as db:
        attempt = db.query(SendAttempt).filter_by(queue_id="conversation", contact_id=contact["id"]).one()
        contact_row = db.get(Contact, contact["id"])
        assert attempt.status == "blocked"
        assert attempt.provider_contacted is False
        assert attempt.provider_accepted is False
        assert contact_row.send_stop_generation > attempt.stop_generation
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 0


def test_conversation_provider_acceptance_commit_failure_requires_reconciliation(client, monkeypatch):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _contact(client, "phase12-conversation-reconcile@recipient.test")
    action = _prepare_conversation(client, contact["id"])
    original_commit = Session.commit
    calls = {"count": 0}

    def fail_outcome_commit_once(session):
        calls["count"] += 1
        if calls["count"] == 3:
            raise RuntimeError("synthetic direct-send commit failure")
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", fail_outcome_commit_once)
    payload = {
        "session_token": SESSION_A,
        "action_id": action["action_id"],
        "subject": "Governed reply",
        "body": "Synthetic governed reply.",
    }
    first = client.post(f"/api/conversations/{contact['id']}/confirm-send", json=payload)
    monkeypatch.setattr(Session, "commit", original_commit)
    repeated = client.post(f"/api/conversations/{contact['id']}/confirm-send", json=payload)

    assert first.status_code == 200
    assert first.json()["status"] == "reconciliation_required"
    assert repeated.status_code == 409
    assert repeated.json()["detail"] == "consumed"
    assert len(client.app.state.transport.sent) == 1
    with SessionLocal() as db:
        attempt = db.query(SendAttempt).filter_by(queue_id="conversation", contact_id=contact["id"]).one()
        assert attempt.status == "reconciliation_required"
        assert attempt.provider_accepted is True
        assert attempt.provider_msg_id
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 0


def test_autonomous_activation_requires_confirmation_and_kill_blocks_send(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    primed = client.post(
        "/api/settings",
        json={
            "auto_reply_autonomous_authorized": True,
            "auto_reply_kill_switch": False,
        },
    )
    assert primed.status_code == 200
    primed_settings = primed.json()
    assert primed_settings["auto_reply_autonomous_authorized"] is False
    assert primed_settings["auto_reply_kill_switch"] is True
    direct = client.post("/api/settings", json={"auto_reply_enabled": True, "auto_reply_mode": "autonomous"})
    assert direct.status_code == 409

    prepared = client.post(
        "/api/auto-reply/autonomous/prepare",
        json={"session_token": SESSION_A},
    )
    assert prepared.status_code == 202
    action = prepared.json()["pending_action"]
    confirmed = client.post(
        "/api/auto-reply/autonomous/confirm",
        json={"session_token": SESSION_A, "action_id": action["action_id"]},
    )
    assert confirmed.status_code == 200
    settings = client.get("/api/settings").json()
    assert settings["auto_reply_enabled"] is True
    assert settings["auto_reply_mode"] == "autonomous"
    assert settings["auto_reply_autonomous_authorized"] is True
    assert settings["auto_reply_kill_switch"] is False

    killed = client.post("/api/auto-reply/kill")
    assert killed.status_code == 200
    settings = client.get("/api/settings").json()
    assert settings["auto_reply_enabled"] is False
    assert settings["auto_reply_autonomous_authorized"] is False
    assert settings["auto_reply_kill_switch"] is True


def test_downgrading_autonomous_mode_revokes_authorization(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    prepared = client.post(
        "/api/auto-reply/autonomous/prepare",
        json={"session_token": SESSION_A},
    ).json()
    assert client.post(
        "/api/auto-reply/autonomous/confirm",
        json={"session_token": SESSION_A, "action_id": prepared["pending_action"]["action_id"]},
    ).status_code == 200

    downgraded = client.post(
        "/api/settings",
        json={"auto_reply_enabled": True, "auto_reply_mode": "propose"},
    )
    reactivation = client.post(
        "/api/settings",
        json={"auto_reply_enabled": True, "auto_reply_mode": "autonomous"},
    )

    assert downgraded.status_code == 200
    assert downgraded.json()["auto_reply_autonomous_authorized"] is False
    assert downgraded.json()["auto_reply_kill_switch"] is True
    assert reactivation.status_code == 409
    assert reactivation.json()["detail"] == "autonomous_confirmation_required"


def test_manual_reply_entry_cannot_trigger_autonomous_provider_send(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _contact(client, "phase14-manual-reply@recipient.test")
    from app.settings.service import set_value

    with SessionLocal() as db:
        set_value(db, "auto_reply_enabled", "true")
        set_value(db, "auto_reply_mode", "autonomous")
        set_value(db, "auto_reply_autonomous_authorized", "true")
        set_value(db, "auto_reply_kill_switch", "false")
        db.commit()

    response = client.post(
        "/api/replies",
        json={
            "contact_id": contact["id"],
            "classified_as": "reply",
            "intent": "question",
            "raw_summary": "?",
        },
    )

    assert response.status_code == 200
    assert len(client.app.state.transport.sent) == 0
    with SessionLocal() as db:
        proposal = db.query(Draft).filter_by(
            contact_id=contact["id"],
            source="auto_reply_proposed",
            approved=False,
        ).one()
        assert proposal is not None
        assert db.query(SendAttempt).filter_by(contact_id=contact["id"]).count() == 0
        assert db.query(ConversationMessage).filter_by(
            contact_id=contact["id"],
            direction="outbound",
            auto_sent=True,
        ).count() == 0


def test_kill_invalidates_pending_autonomous_activation(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    prepared = client.post(
        "/api/auto-reply/autonomous/prepare",
        json={"session_token": SESSION_A},
    )
    assert prepared.status_code == 202
    action = prepared.json()["pending_action"]

    killed = client.post("/api/auto-reply/kill")
    assert killed.status_code == 200
    assert killed.json()["invalidated_pending_actions"] == 1
    confirmed = client.post(
        "/api/auto-reply/autonomous/confirm",
        json={"session_token": SESSION_A, "action_id": action["action_id"]},
    )

    assert confirmed.status_code == 409
    assert confirmed.json()["detail"] == "consumed"
    settings = client.get("/api/settings").json()
    assert settings["auto_reply_enabled"] is False
    assert settings["auto_reply_autonomous_authorized"] is False
    assert settings["auto_reply_kill_switch"] is True


def test_autonomous_provider_boundary_rechecks_kill_switch(client, monkeypatch):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _contact(client, "phase12-autonomous-kill-boundary@recipient.test")
    with SessionLocal() as db:
        draft = Draft(
            contact_id=contact["id"],
            subject="Re: synthetic autonomous",
            body="Synthetic autonomous reply.",
            ai_provider="auto",
            warnings="[]",
            source="auto_reply",
            approved=True,
            approved_at=utcnow(),
        )
        db.add(draft)
        from app.settings.service import set_value

        set_value(db, "auto_reply_enabled", "true")
        set_value(db, "auto_reply_mode", "autonomous")
        set_value(db, "auto_reply_autonomous_authorized", "true")
        set_value(db, "auto_reply_kill_switch", "false")
        db.commit()
        draft_id = draft.id

    from app.conversations import auto_reply_service

    original_begin = auto_reply_service.begin_provider_attempt

    def kill_after_attempt(db, **kwargs):
        attempt, state = original_begin(db, **kwargs)
        from app.settings.service import set_value

        set_value(db, "auto_reply_enabled", "false")
        set_value(db, "auto_reply_autonomous_authorized", "false")
        set_value(db, "auto_reply_kill_switch", "true")
        db.commit()
        return attempt, state

    monkeypatch.setattr(auto_reply_service, "begin_provider_attempt", kill_after_attempt)
    with SessionLocal() as db:
        result = asyncio.run(
            AutoReplyService()._send_draft(
                db,
                db.get(Contact, contact["id"]),
                db.get(Draft, draft_id),
                source="auto_reply",
                reply_id="phase12-kill-boundary",
            )
        )

    assert result.action == "blocked"
    assert "AUTO_REPLY_KILLED" in (result.reason or "")
    assert len(client.app.state.transport.sent) == 0


def test_manual_auto_reply_approval_rechecks_stop_now_generation(client, monkeypatch):
    configure_sender(client, canary_verified=True, dry_run=False)
    assert client.post(
        "/api/settings",
        json={"auto_reply_enabled": True, "auto_reply_mode": "propose"},
    ).status_code == 200
    contact = _contact(client, "phase14-manual-auto-stop@recipient.test")
    with SessionLocal() as db:
        draft = Draft(
            contact_id=contact["id"],
            subject="Re: governed proposal",
            body="Synthetic reviewed proposal.",
            ai_provider="manual",
            warnings="[]",
            source="auto_reply_proposed",
            approved=False,
        )
        db.add(draft)
        db.commit()
        draft_id = draft.id

    prepared = client.post(
        f"/api/auto-reply/approve/{draft_id}",
        json={"session_token": SESSION_A},
    ).json()
    from app.conversations import auto_reply_service
    from app.settings.service import get_int, set_value

    original_begin = auto_reply_service.begin_provider_attempt

    def stop_after_attempt(db, **kwargs):
        attempt, state = original_begin(db, **kwargs)
        if state == "ready":
            set_value(
                db,
                "auto_reply_kill_generation",
                str(get_int(db, "auto_reply_kill_generation") + 1),
            )
            db.commit()
        return attempt, state

    monkeypatch.setattr(auto_reply_service, "begin_provider_attempt", stop_after_attempt)
    response = client.post(
        f"/api/auto-reply/confirm/{draft_id}",
        json={
            "session_token": SESSION_A,
            "action_id": prepared["pending_action"]["action_id"],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert "AUTO_REPLY_KILLED" in response.json()["reason"]
    assert len(client.app.state.transport.sent) == 0


def test_autonomous_provider_boundary_rechecks_contact_stop_fence(client, monkeypatch):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _contact(client, "phase12-autonomous-stop-fence@recipient.test")
    with SessionLocal() as db:
        draft = Draft(
            contact_id=contact["id"],
            subject="Re: synthetic autonomous stop",
            body="Synthetic autonomous reply.",
            ai_provider="auto",
            warnings="[]",
            source="auto_reply",
            approved=True,
            approved_at=utcnow(),
        )
        db.add(draft)
        from app.settings.service import set_value

        set_value(db, "auto_reply_enabled", "true")
        set_value(db, "auto_reply_mode", "autonomous")
        set_value(db, "auto_reply_autonomous_authorized", "true")
        set_value(db, "auto_reply_kill_switch", "false")
        db.commit()
        draft_id = draft.id

    from app.conversations import auto_reply_service

    original_begin = auto_reply_service.begin_provider_attempt

    def reply_after_attempt(db, **kwargs):
        attempt, state = original_begin(db, **kwargs)
        if state == "ready":
            create_reply_record(
                db,
                db.get(Contact, contact["id"]),
                "reply",
                "Stop before autonomous provider dispatch.",
                external_message_id="<phase12-autonomous-stop-fence@recipient.test>",
                stop_followups=True,
                intent="positive_interest",
            )
            db.commit()
        return attempt, state

    monkeypatch.setattr(auto_reply_service, "begin_provider_attempt", reply_after_attempt)
    with SessionLocal() as db:
        result = asyncio.run(
            AutoReplyService()._send_draft(
                db,
                db.get(Contact, contact["id"]),
                db.get(Draft, draft_id),
                source="auto_reply",
                reply_id="phase12-autonomous-stop-fence",
            )
        )

    assert result.action == "blocked"
    assert "CONTACT_SEND_STOPPED" in (result.reason or "")
    assert len(client.app.state.transport.sent) == 0
    with SessionLocal() as db:
        attempt = db.query(SendAttempt).filter_by(queue_id="auto_reply", contact_id=contact["id"]).one()
        contact_row = db.get(Contact, contact["id"])
        assert attempt.provider_contacted is False
        assert attempt.provider_accepted is False
        assert contact_row.send_stop_generation > attempt.stop_generation
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 0


def test_queue_provider_boundary_rechecks_contact_stop_fence(client, monkeypatch):
    contact, approved = _approved_queue(client, "phase12-queue-late-stop@recipient.test")
    queue_id = approved["queue_id"]

    transport = client.app.state.transport
    original_send = transport.send

    def stop_after_provider_acceptance(**kwargs):
        outcome = original_send(**kwargs)
        with SessionLocal() as stop_db:
            stop_contact_send_work(stop_db, contact["id"], "RECIPIENT_REPLIED")
            stop_db.commit()
        return outcome

    monkeypatch.setattr(transport, "send", stop_after_provider_acceptance)
    result = client.post("/api/queue/process").json()

    assert result["provider_accepted"] == 0
    assert result["reconciliation_required"] == 1
    assert len(transport.sent) == 1
    with SessionLocal() as db:
        queue = db.get(SendQueue, queue_id)
        attempt = db.query(SendAttempt).filter_by(queue_id=queue_id).one()
        assert queue.status == "reconciliation_required"
        assert attempt.status == "reconciliation_required"
        assert attempt.provider_contacted is True
        assert attempt.provider_accepted is True
        assert attempt.error_code == "contact_stopped_after_provider_call"
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], source="queue").count() == 0
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"]).count() == 0
        assert db.query(AuditEvent).filter_by(event_type="send.success", entity_id=queue_id).count() == 0


def test_provider_acceptance_commit_failure_enters_reconciliation_and_finalizes_once(client, monkeypatch):
    contact, approved = _approved_queue(client, "phase12-commit-failure@recipient.test")
    original_commit = Session.commit
    calls = {"count": 0}

    def fail_final_commit_once(session):
        calls["count"] += 1
        if calls["count"] == 3:
            raise RuntimeError("synthetic final commit failure")
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", fail_final_commit_once)
    result = client.post("/api/queue/process").json()
    monkeypatch.setattr(Session, "commit", original_commit)

    assert result["provider_accepted"] == 0
    assert result["reconciliation_required"] == 1
    assert len(client.app.state.transport.sent) == 1
    with SessionLocal() as db:
        queue = db.get(SendQueue, approved["queue_id"])
        attempt = db.query(SendAttempt).filter_by(queue_id=queue.id).one()
        assert queue.status == "reconciliation_required"
        assert attempt.status == "reconciliation_required"
        assert attempt.provider_accepted is True
        assert attempt.provider_msg_id
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 0
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=2).count() == 0

    finalized = client.post(
        f"/api/queue/{approved['queue_id']}/reconcile",
        json={"action": "finalize_provider_accepted"},
    )
    repeated = client.post(
        f"/api/queue/{approved['queue_id']}/reconcile",
        json={"action": "finalize_provider_accepted"},
    )
    assert finalized.status_code == 200
    assert finalized.json()["status"] == "provider_accepted"
    assert repeated.status_code == 409
    with SessionLocal() as db:
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=2).count() == 1
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 1


def test_reply_revoked_queue_reconciliation_cannot_finalize_or_schedule_followup(client, monkeypatch):
    contact, approved = _approved_queue(client, "phase12-reconciliation-stop@recipient.test")
    original_commit = Session.commit
    calls = {"count": 0}

    def fail_final_commit_once(session):
        calls["count"] += 1
        if calls["count"] == 3:
            raise RuntimeError("synthetic final commit failure")
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", fail_final_commit_once)
    result = client.post("/api/queue/process").json()
    monkeypatch.setattr(Session, "commit", original_commit)
    assert result["reconciliation_required"] == 1

    with SessionLocal() as db:
        create_reply_record(
            db,
            db.get(Contact, contact["id"]),
            "reply",
            "Reply arrived after provider acceptance but before reconciliation.",
            external_message_id="<phase12-reconciliation-stop@recipient.test>",
            stop_followups=True,
            intent="positive_interest",
        )
        db.commit()

    finalized = client.post(
        f"/api/queue/{approved['queue_id']}/reconcile",
        json={"action": "finalize_provider_accepted"},
    )
    assert finalized.status_code == 409
    assert finalized.json()["detail"] in {"queue_not_reconciliation_required", "contact_send_stopped"}
    with SessionLocal() as db:
        queue = db.get(SendQueue, approved["queue_id"])
        attempt = db.query(SendAttempt).filter_by(queue_id=queue.id).one()
        assert queue.status == "cancelled"
        assert attempt.status == "reconciliation_required"
        assert attempt.provider_accepted is True
        assert db.get(Contact, contact["id"]).status == "conversation_active"
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 0
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=2).count() == 0


def test_conversation_stop_during_provider_call_requires_reconciliation(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _contact(client, "phase12-conversation-late-stop@recipient.test")
    action = _prepare_conversation(client, contact["id"])
    original_send = client.app.state.transport.send

    def accept_then_stop(**kwargs):
        outcome = original_send(**kwargs)
        with SessionLocal() as db:
            create_reply_record(
                db,
                db.get(Contact, contact["id"]),
                "reply",
                "Stop arrived while the provider call was in flight.",
                external_message_id="<phase12-conversation-late-stop@recipient.test>",
                stop_followups=True,
                intent="positive_interest",
            )
            db.commit()
        return outcome

    client.app.state.transport.send = accept_then_stop
    response = client.post(
        f"/api/conversations/{contact['id']}/confirm-send",
        json={
            "session_token": SESSION_A,
            "action_id": action["action_id"],
            "subject": "Governed reply",
            "body": "Synthetic governed reply.",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "reconciliation_required"
    assert response.json()["provider_accepted"] is True
    assert len(client.app.state.transport.sent) == 1
    with SessionLocal() as db:
        attempt = db.query(SendAttempt).filter_by(queue_id="conversation", contact_id=contact["id"]).one()
        assert attempt.status == "reconciliation_required"
        assert attempt.provider_accepted is True
        assert attempt.error_code == "contact_stopped_after_provider_call"
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 0


def test_autonomous_kill_during_provider_call_requires_reconciliation(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _contact(client, "phase12-autonomous-late-kill@recipient.test")
    with SessionLocal() as db:
        draft = Draft(
            contact_id=contact["id"],
            subject="Re: synthetic autonomous late kill",
            body="Synthetic autonomous reply.",
            ai_provider="auto",
            warnings="[]",
            source="auto_reply",
            approved=True,
            approved_at=utcnow(),
        )
        db.add(draft)
        from app.settings.service import set_value

        set_value(db, "auto_reply_enabled", "true")
        set_value(db, "auto_reply_mode", "autonomous")
        set_value(db, "auto_reply_autonomous_authorized", "true")
        set_value(db, "auto_reply_kill_switch", "false")
        db.commit()
        draft_id = draft.id

    original_send = client.app.state.transport.send

    def accept_then_kill(**kwargs):
        outcome = original_send(**kwargs)
        with SessionLocal() as db:
            from app.settings.service import set_value

            set_value(db, "auto_reply_enabled", "false")
            set_value(db, "auto_reply_autonomous_authorized", "false")
            set_value(db, "auto_reply_kill_switch", "true")
            db.commit()
        return outcome

    client.app.state.transport.send = accept_then_kill
    with SessionLocal() as db:
        result = asyncio.run(
            AutoReplyService()._send_draft(
                db,
                db.get(Contact, contact["id"]),
                db.get(Draft, draft_id),
                source="auto_reply",
                reply_id="phase12-autonomous-late-kill",
            )
        )

    assert result.action == "reconciliation_required"
    assert result.reason == "auto_reply_killed_after_provider_call"
    assert len(client.app.state.transport.sent) == 1
    with SessionLocal() as db:
        attempt = db.query(SendAttempt).filter_by(queue_id="auto_reply", contact_id=contact["id"]).one()
        assert attempt.status == "reconciliation_required"
        assert attempt.provider_accepted is True
        assert attempt.error_code == "auto_reply_killed_after_provider_call"
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 0


def test_reply_after_claim_is_blocked_by_final_policy_recheck(client, monkeypatch):
    contact, approved = _approved_queue(client, "phase12-after-claim@recipient.test")
    from app.send import queue_worker

    original = queue_worker.evaluate_policy
    calls = {"count": 0}

    def inject_reply_before_final_check(entry, db):
        calls["count"] += 1
        if calls["count"] == 2:
            create_reply_record(
                db,
                db.get(Contact, contact["id"]),
                "reply",
                "Interested. Stop the sequence.",
                external_message_id="<phase12-after-claim-reply@recipient.test>",
                stop_followups=True,
                intent="positive_interest",
            )
            db.flush()
        return original(entry, db)

    monkeypatch.setattr(queue_worker, "evaluate_policy", inject_reply_before_final_check)
    result = client.post("/api/queue/process").json()

    assert result["blocked"] == 1
    assert len(client.app.state.transport.sent) == 0
    with SessionLocal() as db:
        queue = db.get(SendQueue, approved["queue_id"])
        assert queue.status == "blocked"
        assert "RECIPIENT_REPLIED" in json.loads(queue.policy_block_reasons)
        assert db.query(SendAttempt).filter_by(queue_id=queue.id, provider_accepted=True).count() == 0


def test_reply_after_final_policy_check_revokes_claim_before_provider_call(client, monkeypatch):
    contact, approved = _approved_queue(client, "phase12-last-moment-reply@recipient.test")
    from app.send import queue_worker

    original_get_secret = queue_worker.get_secret
    injected = {"done": False}

    def inject_reply_during_credential_read(db, key):
        value = original_get_secret(db, key)
        if not injected["done"]:
            injected["done"] = True
            create_reply_record(
                db,
                db.get(Contact, contact["id"]),
                "reply",
                "Stop before provider dispatch.",
                external_message_id="<phase12-last-moment-reply@recipient.test>",
                stop_followups=True,
                intent="positive_interest",
            )
            db.commit()
        return value

    monkeypatch.setattr(queue_worker, "get_secret", inject_reply_during_credential_read)
    result = client.post("/api/queue/process").json()

    assert result["blocked"] == 1
    assert len(client.app.state.transport.sent) == 0
    with SessionLocal() as db:
        queue = db.get(SendQueue, approved["queue_id"])
        attempt = db.query(SendAttempt).filter_by(queue_id=queue.id).one()
        assert queue.status == "cancelled"
        assert queue.processing_token is None
        assert attempt.status == "blocked"
        assert attempt.provider_accepted is False
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=2).count() == 0


class ProviderNotContactedTransport:
    transport_name = "test_provider"

    def verify(self, user: str, password: str) -> bool:
        return True

    def send(self, *, sender: str, password: str, to: str, subject: str, body: str, resolution) -> SendOutcome:
        return SendOutcome(
            attempt_status="failed",
            configured_transport=resolution.configured_transport,
            effective_transport=resolution.effective_transport,
            transport_source=resolution.transport_source,
            simulated=False,
            provider_contacted=False,
            provider_accepted=False,
            error_code="provider_not_contacted",
            error_detail_redacted="Synthetic pre-provider failure",
        )


class BlockingAcceptedTransport(SMTPTransport):
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def verify(self, user: str, password: str) -> bool:
        return True

    def send(self, *, sender: str, password: str, to: str, subject: str, body: str, resolution) -> SendOutcome:
        self.calls += 1
        self.entered.set()
        assert self.release.wait(timeout=10)
        return SendOutcome(
            attempt_status="provider_accepted",
            configured_transport=resolution.configured_transport,
            effective_transport=resolution.effective_transport,
            transport_source=resolution.transport_source,
            simulated=False,
            provider_contacted=True,
            provider_accepted=True,
            provider_message_id="fenced123phase12",
            provider_response_classification="smtp_transaction_completed",
        )


def test_revoked_stale_worker_cannot_finalize_provider_acceptance(client):
    contact, approved = _approved_queue(client, "phase12-fenced-worker@recipient.test")
    transport = BlockingAcceptedTransport()
    first_result: dict = {}

    def run_first_worker():
        with SessionLocal() as db:
            first_result.update(asyncio.run(process_pending_queue(db, transport=transport)))

    worker = threading.Thread(target=run_first_worker, daemon=True)
    worker.start()
    assert transport.entered.wait(timeout=10)

    with SessionLocal() as db:
        queue = db.get(SendQueue, approved["queue_id"])
        assert queue.status == "processing"
        assert queue.processing_token
        queue.processing_started_at = utcnow() - timedelta(minutes=10)
        db.commit()
        second = asyncio.run(process_pending_queue(db, transport=transport))

    assert second["processed"] == 0
    transport.release.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert first_result["provider_accepted"] == 0
    assert first_result["reconciliation_required"] == 1
    assert transport.calls == 1

    with SessionLocal() as db:
        queue = db.get(SendQueue, approved["queue_id"])
        attempt = db.query(SendAttempt).filter_by(queue_id=queue.id).one()
        assert queue.status == "reconciliation_required"
        assert queue.processing_token is None
        assert attempt.status == "reconciliation_required"
        assert attempt.provider_accepted is True
        assert attempt.provider_msg_id == "fenced123phase12"
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 0
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=2).count() == 0


def test_definite_pre_provider_failure_is_explicitly_retryable(client):
    _contact_row, approved = _approved_queue(client, "phase12-retry@recipient.test")
    with SessionLocal() as db:
        first = asyncio.run(process_pending_queue(db, transport=ProviderNotContactedTransport()))
    assert first["failed"] == 1

    retried = client.post(f"/api/queue/{approved['queue_id']}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "pending"
    accepted = client.post("/api/queue/process").json()
    assert accepted["provider_accepted"] == 1
    assert len(client.app.state.transport.sent) == 1


def test_manual_pause_cancels_existing_work(client):
    contact, queue_id, followup_id = _dispatched_followup_work(client, "phase12-pause@recipient.test")
    response = client.patch(f"/api/contacts/{contact['id']}", json={"status": "manually_paused"})

    assert response.status_code == 200
    with SessionLocal() as db:
        assert db.get(SendQueue, queue_id).status == "cancelled"
        assert db.get(FollowUpSequence, followup_id).status == "stopped"
        assert db.get(FollowUpSequence, followup_id).stop_reason == "RECIPIENT_MANUALLY_PAUSED"


def test_simulated_acceptance_evidence_cannot_unlock_next_sequence(client):
    contact, approved = _approved_queue(client, "phase13-simulated-evidence@recipient.test")
    with SessionLocal() as db:
        queue = db.get(SendQueue, approved["queue_id"])
        queue.status = "provider_accepted"
        db.add(
            SendAttempt(
                queue_id=queue.id,
                contact_id=contact["id"],
                draft_id=queue.draft_id,
                idempotency_key=queue.idempotency_key,
                provider_msg_id="abcdef1234567890",
                status="provider_accepted",
                sender_identity="phase13-sender@finimatic.test",
                configured_transport="gmail_api",
                effective_transport="gmail_api",
                transport_source="test_fixture",
                simulated=True,
                provider_contacted=False,
                provider_accepted=True,
                provider_response_classification="gmail_api_accepted",
                sent_at=utcnow(),
            )
        )
        db.commit()
        assert sequence_prerequisite_met(db, contact["id"], 2) is False


def test_corrupt_simulated_acceptance_racing_after_policy_requires_reconciliation(client, monkeypatch):
    contact, approved = _approved_queue(client, "phase14-corrupt-acceptance@recipient.test", dry_run=False)
    from app.send import queue_worker

    original_evaluate_policy = queue_worker.evaluate_policy
    injected = False

    def inject_corrupt_acceptance(entry, db):
        nonlocal injected
        decision = original_evaluate_policy(entry, db)
        if not injected:
            injected = True
            db.add(
                SendAttempt(
                    queue_id=entry.id,
                    contact_id=entry.contact_id,
                    draft_id=entry.draft_id,
                    idempotency_key=entry.idempotency_key,
                    provider_msg_id="abcdef1234567890",
                    status="provider_accepted",
                    sender_identity="phase14-sender@finimatic.test",
                    configured_transport="gmail_api",
                    effective_transport="gmail_api",
                    transport_source="race_fixture",
                    simulated=True,
                    provider_contacted=False,
                    provider_accepted=True,
                    provider_response_classification="gmail_api_accepted",
                    sent_at=utcnow(),
                )
            )
            db.flush()
        return decision

    monkeypatch.setattr(queue_worker, "evaluate_policy", inject_corrupt_acceptance)

    result = client.post("/api/queue/process").json()

    assert result["provider_accepted"] == 0
    assert result["reconciliation_required"] == 1
    assert len(client.app.state.transport.sent) == 0
    with SessionLocal() as db:
        queue = db.get(SendQueue, approved["queue_id"])
        assert queue.status == "reconciliation_required"
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"]).count() == 0
        assert db.query(AuditEvent).filter_by(event_type="send.success", entity_id=queue.id).count() == 0


def test_corrupt_returned_provider_outcome_cannot_become_success(client, monkeypatch):
    contact, approved = _approved_queue(client, "phase14-corrupt-outcome@recipient.test", dry_run=False)
    from app.send.smtp_adapter import GmailAdapter

    async def corrupt_send_message(self, to, subject, body, sender, password):
        return SendOutcome(
            attempt_status="provider_accepted",
            configured_transport=self.resolution.configured_transport,
            effective_transport=self.resolution.effective_transport,
            transport_source=self.resolution.transport_source,
            simulated=True,
            provider_contacted=False,
            provider_accepted=True,
            provider_message_id="test-provider-corrupt",
            provider_response_classification="test_provider_accepted",
        )

    monkeypatch.setattr(GmailAdapter, "send_message", corrupt_send_message)

    result = client.post("/api/queue/process").json()

    assert result["provider_accepted"] == 0
    assert result["failed"] == 1
    with SessionLocal() as db:
        queue = db.get(SendQueue, approved["queue_id"])
        attempt = db.query(SendAttempt).filter_by(queue_id=queue.id).one()
        assert queue.status == "failed"
        assert attempt.provider_accepted is False
        assert attempt.provider_msg_id is None
        assert attempt.error_code == "accepted_without_provider_contact"
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"]).count() == 0
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 0
        assert db.query(AuditEvent).filter_by(event_type="send.success", entity_id=queue.id).count() == 0


def test_prior_acceptance_must_match_exact_queue_contact_and_draft(client, monkeypatch):
    contact, approved = _approved_queue(client, "phase14-misbound-acceptance@recipient.test", dry_run=False)
    from app.send import queue_worker

    original_evaluate_policy = queue_worker.evaluate_policy
    injected = False

    def inject_misbound_acceptance(entry, db):
        nonlocal injected
        decision = original_evaluate_policy(entry, db)
        if not injected:
            injected = True
            db.add(
                SendAttempt(
                    queue_id="unrelated-queue",
                    contact_id="unrelated-contact",
                    draft_id="unrelated-draft",
                    idempotency_key=entry.idempotency_key,
                    provider_msg_id="test-provider-misbound",
                    status="provider_accepted",
                    sender_identity="phase14-sender@finimatic.test",
                    configured_transport="test_provider",
                    effective_transport="test_provider",
                    transport_source="corrupt_fixture",
                    simulated=False,
                    provider_contacted=True,
                    provider_accepted=True,
                    provider_response_classification="test_provider_accepted",
                    sent_at=utcnow(),
                )
            )
            db.flush()
        return decision

    monkeypatch.setattr(queue_worker, "evaluate_policy", inject_misbound_acceptance)

    result = client.post("/api/queue/process").json()

    assert result["provider_accepted"] == 0
    assert result["reconciliation_required"] == 1
    assert len(client.app.state.transport.sent) == 0
    with SessionLocal() as db:
        queue = db.get(SendQueue, approved["queue_id"])
        assert queue.status == "reconciliation_required"
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"]).count() == 0
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 0
        assert db.query(AuditEvent).filter_by(event_type="send.success", entity_id=queue.id).count() == 0


def test_conversation_confirmation_rechecks_canary_after_review(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _contact(client, "phase14-conversation-canary@recipient.test")
    action = _prepare_conversation(client, contact["id"])
    from app.settings.service import set_value

    with SessionLocal() as db:
        set_value(db, "canary_verified", "false")
        set_value(db, "sender_readiness", "configured")
        db.commit()

    response = client.post(
        f"/api/conversations/{contact['id']}/confirm-send",
        json={
            "session_token": SESSION_A,
            "action_id": action["action_id"],
            "subject": "Governed reply",
            "body": "Synthetic governed reply.",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "policy_now_blocked"
    assert len(client.app.state.transport.sent) == 0


def test_stop_generation_increment_uses_database_value_not_stale_session(client):
    contact = _contact(client, "phase13-atomic-stop@recipient.test")
    first = SessionLocal()
    second = SessionLocal()
    try:
        first.get(Contact, contact["id"])
        second.get(Contact, contact["id"])
        stop_contact_send_work(first, contact["id"], "FIRST_STOP")
        first.commit()
        stop_contact_send_work(second, contact["id"], "SECOND_STOP")
        second.commit()
    finally:
        first.close()
        second.close()
    with SessionLocal() as db:
        assert db.get(Contact, contact["id"]).send_stop_generation == 2


def test_reconciliation_finalizes_attempt_and_unlocks_next_sequence(client, monkeypatch):
    contact, approved = _approved_queue(client, "phase13-reconcile-sequence@recipient.test")
    original_commit = Session.commit
    calls = {"count": 0}

    def fail_final_commit_once(session):
        calls["count"] += 1
        if calls["count"] == 3:
            raise RuntimeError("synthetic final commit failure")
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", fail_final_commit_once)
    result = client.post("/api/queue/process").json()
    monkeypatch.setattr(Session, "commit", original_commit)
    assert result["reconciliation_required"] == 1

    finalized = client.post(
        f"/api/queue/{approved['queue_id']}/reconcile",
        json={"action": "finalize_provider_accepted"},
    )
    assert finalized.status_code == 200
    with SessionLocal() as db:
        attempt = db.query(SendAttempt).filter_by(queue_id=approved["queue_id"]).one()
        assert attempt.status == "provider_accepted"
        assert sequence_prerequisite_met(db, contact["id"], 2) is True


def test_smtp_reconciliation_persists_tracking_id_conversation(client):
    contact, approved = _approved_queue(client, "phase13-smtp-reconcile@recipient.test")
    tracking_id = "<phase13-smtp-reconcile@finimatic.test>"
    with SessionLocal() as db:
        queue = db.get(SendQueue, approved["queue_id"])
        queue.status = "reconciliation_required"
        db.add(
            SendAttempt(
                queue_id=queue.id,
                contact_id=contact["id"],
                draft_id=queue.draft_id,
                idempotency_key=queue.idempotency_key,
                stop_generation=db.get(Contact, contact["id"]).send_stop_generation,
                tracking_message_id=tracking_id,
                status="reconciliation_required",
                sender_identity="phase13-sender@finimatic.test",
                configured_transport="smtp",
                effective_transport="smtp",
                transport_source="test_fixture",
                simulated=False,
                provider_contacted=True,
                provider_accepted=True,
                provider_response_classification="smtp_transaction_completed",
                sent_at=utcnow(),
            )
        )
        db.commit()

    finalized = client.post(
        f"/api/queue/{approved['queue_id']}/reconcile",
        json={"action": "finalize_provider_accepted"},
    )
    assert finalized.status_code == 200
    with SessionLocal() as db:
        message = (
            db.query(ConversationMessage)
            .filter_by(contact_id=contact["id"], direction="outbound")
            .one()
        )
        assert message.external_message_id == tracking_id


def test_stale_reconciliation_session_cannot_overwrite_committed_stop(client):
    contact, approved = _approved_queue(client, "phase13-stale-reconcile-stop@recipient.test")
    with SessionLocal() as db:
        queue = db.get(SendQueue, approved["queue_id"])
        queue.status = "reconciliation_required"
        db.add(
            SendAttempt(
                queue_id=queue.id,
                contact_id=contact["id"],
                draft_id=queue.draft_id,
                idempotency_key=queue.idempotency_key,
                stop_generation=db.get(Contact, contact["id"]).send_stop_generation,
                provider_msg_id="abcdef1234567890",
                status="reconciliation_required",
                sender_identity="phase13-sender@finimatic.test",
                configured_transport="gmail_api",
                effective_transport="gmail_api",
                transport_source="test_fixture",
                simulated=False,
                provider_contacted=True,
                provider_accepted=True,
                provider_response_classification="gmail_api_accepted",
                sent_at=utcnow(),
            )
        )
        db.commit()

    stale_db = SessionLocal()
    try:
        stale_queue = stale_db.get(SendQueue, approved["queue_id"])
        stale_contact = stale_db.get(Contact, contact["id"])
        assert stale_queue.status == "reconciliation_required"
        assert stale_contact.send_stop_generation == 0
        stale_db.commit()
        with SessionLocal() as stop_db:
            stop_contact_send_work(stop_db, contact["id"], "RECIPIENT_REPLIED")
            stop_db.commit()

        with pytest.raises(ValueError, match="contact_send_stopped"):
            reconcile_queue_entry(stale_db, approved["queue_id"], "finalize_provider_accepted")
    finally:
        stale_db.close()

    with SessionLocal() as db:
        assert db.get(SendQueue, approved["queue_id"]).status == "cancelled"
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=2).count() == 0
        assert db.query(ConversationMessage).filter_by(contact_id=contact["id"], direction="outbound").count() == 0
