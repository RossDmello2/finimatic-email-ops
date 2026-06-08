from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Contact, ConversationMessage, Draft, FollowUpSequence, Reply, SendAttempt, SendQueue, Suppression
from app.db.session import SessionLocal
from app.replies.imap_fetcher import IMAPReplyFetcher
from app.replies.service import classify_intent, create_reply_record
from conftest import configure_sender


def _make_contact(client, email: str = "growth@example.com", *, tags: str | None = None):
    payload = {"email": email, "creator_name": "Growth Contact", "source": "manual"}
    if tags:
        payload["tags"] = tags
    return client.post("/api/contacts", json=payload).json()


def _make_draft(client, contact_id: str):
    return client.post(
        "/api/drafts",
        json={"contact_id": contact_id, "subject": "Initial", "body": "Hi there"},
    ).json()


def _queue_approved_draft_without_sending(
    client,
    contact_id: str,
    draft_id: str,
    *,
    sequence_num: int = 1,
    followup_id: str | None = None,
):
    with SessionLocal() as db:
        draft = db.get(Draft, draft_id)
        draft.approved = True
        draft.approved_at = datetime.now(timezone.utc)
        if followup_id:
            followup = db.get(FollowUpSequence, followup_id)
            followup.status = "queued"
            followup.draft_id = draft_id
        db.commit()
    return client.post(
        "/api/queue",
        json={"contact_id": contact_id, "draft_id": draft_id, "sequence_num": sequence_num},
    ).json()


def _make_due_followup(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _make_contact(client, "followup-growth@example.com")
    draft = _make_draft(client, contact["id"])
    client.post(f"/api/drafts/{draft['id']}/approve")
    sequence = client.get("/api/followups").json()["items"][0]
    past_due = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    client.patch(f"/api/followups/{sequence['id']}", json={"due_at": past_due})
    return contact, client.get(f"/api/followups/{sequence['id']}").json()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("positive_interest", "positive_interest"),
        ("negative_no", "negative_no"),
        ("question", "question"),
    ],
)
def test_intent_classification_groq_values(client, monkeypatch, raw, expected):
    configure_sender(client)
    monkeypatch.setattr("app.replies.service._call_groq_intent", lambda db, key, prompt: raw)

    with SessionLocal() as db:
        assert classify_intent("Re: Offer", "I am interested", db) == expected


def test_intent_classification_unknown_without_groq(client):
    with SessionLocal() as db:
        assert classify_intent("Re: Offer", "Maybe", db) == "unknown"


def test_intent_classification_refines_skeptical_question_to_objection(client, monkeypatch):
    configure_sender(client)
    monkeypatch.setattr("app.replies.service._call_groq_intent", lambda db, key, prompt: "question")

    with SessionLocal() as db:
        intent = classify_intent(
            "Re: RAG",
            "Is this just ChatGPT with a different name? What makes your RAG chatbot different?",
            db,
        )

    assert intent == "objection"


def test_intent_classification_ignores_trace_subject_auto_reply_token(client, monkeypatch):
    configure_sender(client)
    monkeypatch.setattr("app.replies.service._call_groq_intent", lambda db, key, prompt: "auto_reply")

    with SessionLocal() as db:
        intent = classify_intent(
            "Re: AUTO-REPLY-LIVE-20260524",
            "That sounds interesting, tell me more about how it would help with student Q&A.",
            db,
        )

    assert intent == "positive_interest"


def test_imap_classification_does_not_treat_trace_subject_as_auto_reply(client):
    with SessionLocal() as db:
        fetcher = IMAPReplyFetcher(db)
        classified = fetcher._classify(
            "Re: AUTO-REPLY-LIVE-20260524",
            "That sounds interesting, tell me more about how it would help with student Q&A.",
        )

    assert classified != "auto_reply"


def test_imap_model_auto_reply_without_auto_responder_cue_is_human_reply(client, monkeypatch):
    configure_sender(client)

    class FakeGroq:
        def __init__(self, api_key):
            self.chat = self
            self.completions = self

        def create(self, **kwargs):
            message = type("Message", (), {"content": "auto_reply"})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice]})()

    monkeypatch.setattr("app.replies.imap_fetcher.Groq", FakeGroq)

    with SessionLocal() as db:
        fetcher = IMAPReplyFetcher(db)
        classified = fetcher._classify(
            "Re: AUTO-LOOP-LIVE-20260524 Python support assistant",
            "Can it run short quizzes from my Python lesson examples and avoid making up answers?",
        )

    assert classified == "reply"


def test_imap_blank_body_classifies_unknown_without_model(client, monkeypatch):
    with SessionLocal() as db:
        fetcher = IMAPReplyFetcher(db)
        monkeypatch.setattr(fetcher, "_classify", lambda subject, snippet: pytest.fail("blank bodies should not call classifier"))
        monkeypatch.setattr("app.replies.imap_fetcher.classify_intent", lambda subject, snippet, db: pytest.fail("blank bodies should not call intent model"))

        classified, intent = fetcher._classify_with_intent("Fwd: Python course assistant", "")

    assert classified == "unknown"
    assert intent == "unknown"


def test_imap_forwarded_body_can_still_classify_question(client, monkeypatch):
    with SessionLocal() as db:
        fetcher = IMAPReplyFetcher(db)
        monkeypatch.setattr(fetcher, "_classify", lambda subject, snippet: "reply")
        monkeypatch.setattr("app.replies.imap_fetcher.classify_intent", lambda subject, snippet, db: "question")

        classified, intent = fetcher._classify_with_intent(
            "Fwd: Python course assistant",
            "Forwarded student question: can this assistant answer from my Python material?",
        )

    assert classified == "reply"
    assert intent == "question"


def test_same_external_message_id_maps_to_separate_contacts(client):
    first = _make_contact(client, "dual-first@example.com")
    second = _make_contact(client, "dual-second@example.com")
    shared_message_id = "<shared-reply@example.com>"

    with SessionLocal() as db:
        first_contact = db.get(Contact, first["id"])
        second_contact = db.get(Contact, second["id"])
        first_reply, first_created = create_reply_record(
            db,
            first_contact,
            "reply",
            "First account reply.",
            external_message_id=shared_message_id,
            intent="question",
        )
        second_reply, second_created = create_reply_record(
            db,
            second_contact,
            "reply",
            "Second account reply.",
            external_message_id=shared_message_id,
            intent="positive_interest",
        )
        db.commit()

        reply_rows = db.query(Reply).filter(Reply.external_message_id == shared_message_id).all()
        message_rows = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.external_message_id == shared_message_id)
            .all()
        )

    assert first_created is True
    assert second_created is True
    assert first_reply.contact_id == first["id"]
    assert second_reply.contact_id == second["id"]
    assert {row.contact_id for row in reply_rows} == {first["id"], second["id"]}
    assert {row.contact_id for row in message_rows} == {first["id"], second["id"]}

    filtered = client.get(f"/api/replies?contact_id={first['id']}").json()["items"]
    assert [item["contact_id"] for item in filtered] == [first["id"]]


def test_same_external_message_id_dedupes_within_contact(client):
    contact = _make_contact(client, "dedupe-same-contact@example.com")
    message_id = "<same-contact-reply@example.com>"

    with SessionLocal() as db:
        db_contact = db.get(Contact, contact["id"])
        _first_reply, first_created = create_reply_record(
            db,
            db_contact,
            "reply",
            "First body.",
            external_message_id=message_id,
            intent="question",
        )
        second_reply, second_created = create_reply_record(
            db,
            db_contact,
            "reply",
            "Second body.",
            external_message_id=message_id,
            intent="positive_interest",
        )
        db.commit()

        reply_count = (
            db.query(Reply)
            .filter(Reply.contact_id == contact["id"], Reply.external_message_id == message_id)
            .count()
        )
        message_count = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.contact_id == contact["id"], ConversationMessage.external_message_id == message_id)
            .count()
        )

    assert first_created is True
    assert second_created is False
    assert second_reply.contact_id == contact["id"]
    assert reply_count == 1
    assert message_count == 1


def test_reply_dedupe_key_is_database_enforced(client):
    contact = _make_contact(client, "dedupe-database@example.com")
    with SessionLocal() as db:
        first = Reply(
            contact_id=contact["id"],
            received_at=datetime.now(timezone.utc),
            classified_as="reply",
            intent="question",
            external_message_id="<dedupe-database@example.com>",
            dedupe_key="phase13-dedupe-key",
        )
        db.add(first)
        db.commit()

    with SessionLocal() as db:
        db.add(
            Reply(
                contact_id=contact["id"],
                received_at=datetime.now(timezone.utc),
                classified_as="reply",
                intent="question",
                external_message_id="<dedupe-database@example.com>",
                dedupe_key="phase13-dedupe-key",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_historical_null_dedupe_key_is_normalized_without_duplicate_stop(client):
    contact = _make_contact(client, "legacy-dedupe@example.com")
    with SessionLocal() as db:
        db.add(
            Reply(
                contact_id=contact["id"],
                received_at=datetime.now(timezone.utc),
                classified_as="reply",
                intent="question",
                external_message_id="<LEGACY-REPLY@EXAMPLE.COM>",
                dedupe_key=None,
            )
        )
        db.commit()

    with SessionLocal() as db:
        row, created = create_reply_record(
            db,
            db.get(Contact, contact["id"]),
            "reply",
            "Normalized replay of a historical provider message.",
            external_message_id=" <legacy-reply@example.com> ",
            stop_followups=True,
            intent="question",
        )
        db.commit()
        assert created is False
        assert row.dedupe_key
        assert db.query(Reply).filter_by(contact_id=contact["id"]).count() == 1
        assert db.get(Contact, contact["id"]).send_stop_generation == 0


def test_mixed_historical_duplicate_replay_prefers_existing_key(client):
    contact = _make_contact(client, "mixed-legacy-dedupe@example.com")
    normalized_message_id = "<mixed-legacy@example.com>"
    from app.core.idempotency import sha256_key

    dedupe_key = sha256_key("reply", contact["id"], normalized_message_id)
    with SessionLocal() as db:
        db.add_all(
            [
                Reply(
                    id="legacy-null-reply",
                    contact_id=contact["id"],
                    received_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                    classified_as="reply",
                    intent="question",
                    external_message_id="<MIXED-LEGACY@EXAMPLE.COM>",
                    dedupe_key=None,
                ),
                Reply(
                    id="legacy-keyed-reply",
                    contact_id=contact["id"],
                    received_at=datetime.now(timezone.utc),
                    classified_as="reply",
                    intent="question",
                    external_message_id=normalized_message_id,
                    dedupe_key=dedupe_key,
                ),
            ]
        )
        db.commit()

    with SessionLocal() as db:
        row, created = create_reply_record(
            db,
            db.get(Contact, contact["id"]),
            "reply",
            "Replay should resolve to the canonical keyed historical row.",
            external_message_id=" <mixed-legacy@example.com> ",
            stop_followups=True,
            intent="question",
        )
        db.commit()
        assert created is False
        assert row.id == "legacy-keyed-reply"
        assert db.query(Reply).filter_by(contact_id=contact["id"]).count() == 2
        assert db.get(Contact, contact["id"]).send_stop_generation == 0


def test_thread_header_contact_mapping_prefers_message_id_contact(client):
    sender_contact = _make_contact(client, "shared-sender@example.com")
    target_contact = _make_contact(client, "thread-target@example.com")

    with SessionLocal() as db:
        db.add(
            SendAttempt(
                queue_id="queue-thread-target",
                contact_id=target_contact["id"],
                draft_id="draft-thread-target",
                provider_msg_id="18f4a8c2d3145678",
                tracking_message_id="<sent-target@example.com>",
                status="provider_accepted",
                configured_transport="gmail_api",
                effective_transport="gmail_api",
                transport_source="test_fixture",
                simulated=False,
                provider_contacted=True,
                provider_accepted=True,
                provider_response_classification="gmail_api_accepted",
                sender_identity="primary@example.com",
                sent_at=datetime.now(timezone.utc),
            )
        )
        db.flush()
        fetcher = IMAPReplyFetcher(db)
        attempts = fetcher._sent_attempts_by_message_id()
        message = EmailMessage()
        message["From"] = f"Shared Sender <{sender_contact['email']}>"
        message["References"] = "<older@example.com> <sent-target@example.com>"

        resolved_contact = fetcher._contact_from_thread_headers(message, attempts)

    assert resolved_contact is not None
    assert resolved_contact.id == target_contact["id"]


def test_positive_interest_escalates_contact_to_conversation_active(client):
    contact = _make_contact(client, "positive-routing@example.com")

    response = client.post(
        "/api/replies",
        json={"contact_id": contact["id"], "classified_as": "reply", "raw_summary": "Interested.", "intent": "positive_interest"},
    )

    assert response.status_code == 200
    assert response.json()["intent"] == "positive_interest"
    assert client.get("/api/contacts").json()["items"][0]["status"] == "conversation_active"
    event_types = [row["event_type"] for row in client.get("/api/audit").json()["items"]]
    assert "reply.escalated" in event_types


def test_suppression_can_be_removed_from_api(client):
    contact = _make_contact(client, "remove-suppression@example.com")
    created = client.post("/api/suppressions", json={"email": contact["email"], "reason": "manual", "source": "ui"}).json()
    assert client.get("/api/contacts").json()["items"][0]["status"] == "suppressed"

    response = client.delete(f"/api/suppressions/{created['id']}")

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    suppressions = client.get("/api/suppressions").json()["items"]
    assert not any(row["email"] == "remove-suppression@example.com" for row in suppressions)
    assert client.get("/api/contacts").json()["items"][0]["status"] == "imported"
    event_types = [row["event_type"] for row in client.get("/api/audit").json()["items"]]
    assert "suppression.removed" in event_types


def test_negative_no_stops_due_followups(client):
    contact, sequence = _make_due_followup(client)

    with SessionLocal() as db:
        db_contact = db.get(Contact, contact["id"])
        create_reply_record(db, db_contact, "reply", "No thanks.", intent="negative_no")
        db.commit()

    stopped = client.get(f"/api/followups/{sequence['id']}").json()
    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "RECIPIENT_NEGATIVE_NO"


def test_unsubscribe_reply_creates_suppression_and_stops_followups(client):
    contact, sequence = _make_due_followup(client)

    with SessionLocal() as db:
        db_contact = db.get(Contact, contact["id"])
        create_reply_record(
            db,
            db_contact,
            "unsubscribe",
            "Please remove me from your mailing list and do not contact me again.",
            intent="unsubscribe",
            stop_followups=True,
        )
        db.commit()

    stopped = client.get(f"/api/followups/{sequence['id']}").json()
    contact_row = next(item for item in client.get("/api/contacts").json()["items"] if item["id"] == contact["id"])
    with SessionLocal() as db:
        suppression = db.query(Suppression).filter_by(email=contact["email"]).first()

    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] in {"RECIPIENT_UNSUBSCRIBED", "RECIPIENT_SUPPRESSED"}
    assert contact_row["status"] == "unsubscribed"
    assert suppression is not None
    assert suppression.reason == "unsubscribe"


def test_hostile_negative_no_creates_suppression(client):
    contact, _sequence = _make_due_followup(client)

    with SessionLocal() as db:
        db_contact = db.get(Contact, contact["id"])
        create_reply_record(
            db,
            db_contact,
            "reply",
            "STOP SPAMMING ME. I will report this as spam and file a complaint.",
            intent="negative_no",
            stop_followups=True,
        )
        db.commit()

    contact_row = next(item for item in client.get("/api/contacts").json()["items"] if item["id"] == contact["id"])
    with SessionLocal() as db:
        suppression = db.query(Suppression).filter_by(email=contact["email"]).first()

    assert contact_row["status"] == "follow_up_stopped"
    assert suppression is not None
    assert suppression.reason == "hostile_or_stop_request"


def test_followup_process_proposes_unapproved_draft(client):
    _contact, sequence = _make_due_followup(client)

    result = client.post("/api/followups/process").json()
    updated = client.get(f"/api/followups/{sequence['id']}").json()

    assert result["processed"] == 1
    assert updated["status"] == "pending_approval"
    assert updated["pending_draft_id"]
    assert updated["pending_draft"]["approved"] is False
    with SessionLocal() as db:
        draft = db.get(Draft, updated["pending_draft_id"])
        assert draft.approved is False
        assert draft.notes == "followup_auto:seq2"
        assert "[" not in draft.body
        assert "I wanted to follow up" not in draft.body
        assert "I hope" not in draft.body
        assert "hope you're doing well" not in draft.body.lower()
        assert "Ross Dmello" in draft.body
        assert "AI Systems Engineer" in draft.body


def test_followup_approve_draft_endpoint_sends_immediately_and_schedules_next(client):
    contact, sequence = _make_due_followup(client)
    client.post("/api/followups/process")
    pending = client.get(f"/api/followups/{sequence['id']}").json()

    result = client.post(f"/api/followups/{sequence['id']}/approve-draft")
    updated = client.get(f"/api/followups/{sequence['id']}").json()

    assert result.status_code == 200
    assert result.json()["status"] == "queued"
    assert result.json()["delivery_status"] == "provider_accepted"
    assert updated["status"] == "provider_accepted"
    with SessionLocal() as db:
        draft = db.get(Draft, pending["pending_draft_id"])
        queue = db.get(SendQueue, result.json()["queue_id"])
        next_sequence = db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=3).first()
        assert draft.approved is True
        assert queue.draft_id == draft.id
        assert queue.status == "provider_accepted"
        assert next_sequence is not None
    assert len(client.app.state.transport.sent) == 2


def test_followup_approve_draft_endpoint_queues(client):
    _contact, sequence = _make_due_followup(client)
    client.post("/api/followups/process")

    result = client.post(f"/api/followups/{sequence['id']}/approve-draft")

    assert result.status_code == 200
    assert result.json()["status"] == "queued"
    assert result.json()["queue_id"]
    assert result.json()["queue"]["status"] == "provider_accepted"
    assert result.json()["queue"]["latest_attempt"]["provider_accepted"] is True


def test_followup_approval_dry_run_blocks_without_queueing(client):
    contact, sequence = _make_due_followup(client)
    client.post("/api/followups/process")
    pending = client.get(f"/api/followups/{sequence['id']}").json()
    client.post("/api/settings", json={"dry_run": True})

    response = client.post(f"/api/followups/{sequence['id']}/approve-draft")

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "DRY_RUN_ENABLED"
    with SessionLocal() as db:
        draft = db.get(Draft, pending["pending_draft_id"])
        queue_count = db.query(SendQueue).filter(SendQueue.contact_id == contact["id"], SendQueue.sequence_num == 2).count()
        assert draft.approved is False
        assert queue_count == 0


def test_reply_stops_pending_followup_approval(client):
    contact, sequence = _make_due_followup(client)
    client.post("/api/followups/process")
    pending = client.get(f"/api/followups/{sequence['id']}").json()
    assert pending["status"] == "pending_approval"

    client.post("/api/replies", json={"contact_id": contact["id"], "classified_as": "reply", "raw_summary": "Interested, let's discuss."})
    stopped = client.get(f"/api/followups/{sequence['id']}").json()
    response = client.post(f"/api/followups/{sequence['id']}/approve-draft")

    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "RECIPIENT_REPLIED"
    assert response.status_code == 409
    assert response.json()["detail"] == "RECIPIENT_REPLIED"


def test_followup_approve_draft_rejects_missing_pending_draft(client):
    _contact, sequence = _make_due_followup(client)

    response = client.post(f"/api/followups/{sequence['id']}/approve-draft")

    assert response.status_code == 409
    assert response.json()["detail"] == "pending_draft_missing"


def test_campaign_plan_creation_stores_ai_steps(client, monkeypatch):
    configure_sender(client)
    monkeypatch.setattr(
        "app.campaigns.router._call_groq_campaign",
        lambda db, key, prompt: json.dumps(
            {
                "step_1": {"subject": "Initial RAG", "body": "Pitch RAG.", "purpose": "initial outreach"},
                "step_2": {"subject": "Useful RAG note", "body": "Add value.", "purpose": "value-add follow-up"},
                "step_3": {"subject": "Close the loop", "body": "Breakup.", "purpose": "polite breakup email"},
            }
        ),
    )

    response = client.post("/api/campaigns", json={"name": "Course RAG", "goal": "Pitch RAG chatbots", "target_tags": "course"})

    assert response.status_code == 200
    row = response.json()
    assert row["step_1_draft"]["subject"] == "Initial RAG"
    assert row["step_2_draft"]["purpose"] == "value-add follow-up"


def test_campaign_plan_creation_falls_back_to_empty_steps_without_keys(client):
    response = client.post("/api/campaigns", json={"name": "Manual", "goal": "No AI", "target_tags": ""})

    assert response.status_code == 200
    assert response.json()["step_1_draft"]["subject"] == ""


def test_campaign_patch_updates_step_cards(client):
    campaign = client.post("/api/campaigns", json={"name": "Patch", "goal": "Goal", "target_tags": ""}).json()

    response = client.patch(
        f"/api/campaigns/{campaign['id']}",
        json={"step_1_draft": {"subject": "Edited", "body": "Edited body", "purpose": "initial outreach"}, "status": "paused"},
    )

    assert response.status_code == 200
    assert response.json()["step_1_draft"]["subject"] == "Edited"
    assert response.json()["status"] == "paused"


def test_pausing_campaign_invalidates_queued_draft_and_blocks_send(client, monkeypatch):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _make_contact(client, "campaign-pause@example.com", tags="phase14")
    monkeypatch.setattr(
        "app.campaigns.router._call_groq_campaign",
        lambda db, key, prompt: json.dumps(
            {
                "step_1": {"subject": "Reviewed campaign", "body": "Reviewed campaign body.", "purpose": "initial outreach"},
                "step_2": {"subject": "Follow", "body": "Value", "purpose": "value-add follow-up"},
                "step_3": {"subject": "Close", "body": "Breakup", "purpose": "polite breakup email"},
            }
        ),
    )
    campaign = client.post(
        "/api/campaigns",
        json={"name": "Pause Guard", "goal": "Guard sends", "target_tags": "phase14"},
    ).json()
    assert client.post(f"/api/campaigns/{campaign['id']}/activate").status_code == 200
    draft = next(row for row in client.get("/api/drafts").json()["items"] if row["contact_id"] == contact["id"])
    queued = _queue_approved_draft_without_sending(client, contact["id"], draft["id"])

    paused = client.patch(f"/api/campaigns/{campaign['id']}", json={"status": "paused"})
    processed = client.post("/api/queue/process").json()

    assert paused.status_code == 200
    assert processed["provider_accepted"] == 0
    assert len(client.app.state.transport.sent) == 0
    with SessionLocal() as db:
        assert db.get(Draft, draft["id"]).approved is False
        assert db.get(SendQueue, queued["id"]).status == "cancelled"


def test_pausing_campaign_stops_already_scheduled_followup(client, monkeypatch):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _make_contact(client, "campaign-followup-pause@recipient.test", tags="phase14-followup")
    monkeypatch.setattr(
        "app.campaigns.router._call_groq_campaign",
        lambda db, key, prompt: json.dumps(
            {
                "step_1": {"subject": "Campaign initial", "body": "Campaign initial body.", "purpose": "initial outreach"},
                "step_2": {"subject": "Campaign follow", "body": "Campaign value.", "purpose": "value-add follow-up"},
                "step_3": {"subject": "Campaign close", "body": "Campaign close.", "purpose": "polite breakup email"},
            }
        ),
    )
    campaign = client.post(
        "/api/campaigns",
        json={"name": "Pause Scheduled Followup", "goal": "Stop later work", "target_tags": "phase14-followup"},
    ).json()
    assert client.post(f"/api/campaigns/{campaign['id']}/activate").status_code == 200
    draft = next(row for row in client.get("/api/drafts").json()["items"] if row["contact_id"] == contact["id"])
    initial_approval = client.post(f"/api/drafts/{draft['id']}/approve")
    assert initial_approval.status_code == 200
    assert initial_approval.json()["delivery_status"] == "provider_accepted"
    followup = client.get("/api/followups").json()["items"][0]
    past_due = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    assert client.patch(f"/api/followups/{followup['id']}", json={"due_at": past_due}).status_code == 200
    assert client.post("/api/followups/process").status_code == 200
    pending_followup = client.get(f"/api/followups/{followup['id']}").json()
    queued_followup = _queue_approved_draft_without_sending(
        client,
        contact["id"],
        pending_followup["pending_draft_id"],
        sequence_num=2,
        followup_id=followup["id"],
    )
    followup_queue_id = queued_followup["id"]

    paused = client.patch(f"/api/campaigns/{campaign['id']}", json={"status": "paused"})
    processed = client.post("/api/queue/process").json()

    assert paused.status_code == 200
    assert processed["provider_accepted"] == 0
    assert len(client.app.state.transport.sent) == 1
    with SessionLocal() as db:
        followup = db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=2).one()
        assert followup.status == "stopped"
        assert followup.stop_reason == "CAMPAIGN_NOT_ACTIVE"
        assert db.get(SendQueue, followup_queue_id).status == "cancelled"
        assert db.get(Draft, followup.pending_draft_id).approved is False


def test_blank_campaign_step_cannot_activate(client):
    campaign = client.post(
        "/api/campaigns",
        json={"name": "Incomplete", "goal": "No configured AI", "target_tags": ""},
    ).json()

    response = client.post(f"/api/campaigns/{campaign['id']}/activate")

    assert response.status_code == 422
    assert response.json()["detail"] == "campaign_step_1_incomplete"
    assert client.get("/api/drafts").json()["total"] == 0


def test_campaign_activate_assigns_unapproved_drafts_to_tagged_contacts(client, monkeypatch):
    configure_sender(client)
    _make_contact(client, "tagged-one@example.com", tags="course,rag")
    _make_contact(client, "untagged@example.com", tags="other")
    monkeypatch.setattr(
        "app.campaigns.router._call_groq_campaign",
        lambda db, key, prompt: json.dumps(
            {
                "step_1": {"subject": "Hello {{first_name}}", "body": "RAG for {{niche}}", "purpose": "initial outreach"},
                "step_2": {"subject": "Follow", "body": "Value", "purpose": "value-add follow-up"},
                "step_3": {"subject": "Close", "body": "Breakup", "purpose": "polite breakup email"},
            }
        ),
    )
    campaign = client.post("/api/campaigns", json={"name": "Tagged", "goal": "Pitch", "target_tags": "rag"}).json()

    activated = client.post(f"/api/campaigns/{campaign['id']}/activate").json()
    drafts = client.get("/api/drafts").json()["items"]

    assert activated["contacts_count"] == 1
    assert activated["drafts_created"] == 1
    assert len(drafts) == 1
    assert drafts[0]["approved"] is False
    assert "Hello Growth" in drafts[0]["subject"]


def test_campaign_list_returns_created_campaigns(client):
    client.post("/api/campaigns", json={"name": "Listable", "goal": "Goal", "target_tags": ""})

    response = client.get("/api/campaigns")

    assert response.status_code == 200
    assert response.json()["total"] == 1
