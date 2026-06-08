from datetime import timedelta
import threading

from app.agent.memory import get_or_create_session
from app.agent.pending import create_pending_action
from app.core.time import utcnow
from app.ai.schema import AIFailure
from app.db.models import AgentSession, Contact, ConversationMessage, Draft, PendingAgentAction, PendingEmailActionRow, SendAttempt, Suppression, Workbook, WorkflowRun
from app.db.session import SessionLocal
from app.audit.service import emit_event
from app.replies.service import create_reply_record
from conftest import configure_sender


SESSION_A = "session-token-a"
SESSION_B = "session-token-b"


def _create_contact(client, *, email="sarah@example.com", name="Sarah"):
    return client.post("/api/contacts", json={"email": email, "creator_name": name, "source": "manual"}).json()


def _chat(client, message: str, *, session_token=SESSION_A):
    return client.post("/api/agent/chat", json={"session_token": session_token, "message": message, "provider": "auto"}).json()


def _pending_draft(client, monkeypatch, *, email="sarah@example.com", name="Sarah"):
    monkeypatch.setenv("FINIMATIC_FAKE_AI", "1")
    configure_sender(client, canary_verified=True, dry_run=False)
    _create_contact(client, email=email, name=name)
    response = _chat(client, f"generate a reply for {name}")
    assert response["draft"]
    assert response["pending_action"]
    return response


def _pending_workflow_action(client, *, session_token=SESSION_A):
    response = _chat(client, "start workflow run", session_token=session_token)
    assert response["pending_action"]
    assert response["pending_action"]["capability"] == "workflow_run_start"
    return response


def test_capability_deny(client):
    response = _chat(client, "delete every email in the inbox")

    assert response["error_code"] == "capability_denied"
    assert "cannot perform" in response["response"]


def test_slot_missing(client):
    response = _chat(client, "show thread")

    assert response["is_clarification"] is True
    assert response["error_code"] == "missing_slots"
    assert "Which contact" in response["response"]


def test_tool_read_inbox(client):
    contact = _create_contact(client, email="reply-today@example.com", name="Reply Today")
    client.post("/api/replies", json={"contact_id": contact["id"], "classified_as": "reply", "raw_summary": "Interested in the offer."})

    response = _chat(client, "who replied today?")

    assert "reply-today@example.com" in response["response"]
    assert "Interested in the offer" in response["response"]
    assert "evidence" not in response


def test_tool_read_inbox_counts_contacts_today(client):
    first = _create_contact(client, email="count-one@example.com", name="Count One")
    second = _create_contact(client, email="count-two@example.com", name="Count Two")
    client.post("/api/replies", json={"contact_id": first["id"], "classified_as": "reply", "raw_summary": "First reply."})
    client.post("/api/replies", json={"contact_id": first["id"], "classified_as": "question", "raw_summary": "Second reply."})
    client.post("/api/replies", json={"contact_id": second["id"], "classified_as": "reply", "raw_summary": "Third reply."})

    response = _chat(client, "how many contacts replied today?")

    assert response["intent"] == "email_read_inbox"
    assert "2 contacts replied today" in response["response"]
    assert "3 replies matched" in response["response"]
    assert "First reply" not in response["response"]


def test_agent_awareness_distinguishes_outbound_replies(client):
    contact = _create_contact(client, email="outbound-awareness@example.com", name="Outbound Lead")
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="outbound",
                subject="Re: workflow",
                body="Yes, I can walk you through it.",
                source="manual_reply",
                occurred_at=utcnow() - timedelta(hours=1),
            )
        )
        db.commit()

    response = _chat(client, "whom all have I replied in last 10 hours")

    assert response["channel"] == "awareness"
    assert "1 contact you replied to in the last 10 hours" in response["response"]
    assert "Outbound Lead" in response["response"]
    assert "You wrote" in response["response"]


def test_agent_reuses_single_sent_contact_for_followup_draft(client, monkeypatch):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _create_contact(client, email="context-followup@example.com", name="Context Followup")
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="outbound",
                subject="Re: RAG chatbot",
                body="I sent the prior follow-up about the RAG chatbot scope.",
                source="queue",
                occurred_at=utcnow() - timedelta(minutes=20),
            )
        )
        db.commit()

    async def fake_next_reply(db, contact, history, payload):
        assert contact.email == "context-followup@example.com"
        assert "can we met and discuss" in payload.instruction
        return {
            "subject": "Re: RAG chatbot",
            "body": "Context Followup, can we meet and discuss the RAG chatbot scope this week?\n\nBest regards\nRoss Dmello",
            "warnings": [],
        }

    monkeypatch.setattr("app.agent.tools._generate_next_reply", fake_next_reply)

    first = _chat(client, "who all did i send a follow up mail in last hour", session_token="session-follow-context")
    second = _chat(client, "can u send another follow up mail asking can we met and discuss", session_token="session-follow-context")

    assert first["channel"] == "awareness"
    assert "context-followup@example.com" in first["response"]
    assert second["intent"] == "email_generate_draft"
    assert second["draft"]["to"] == "context-followup@example.com"
    assert "meet and discuss" in second["draft"]["body"]
    assert second["pending_action"]


def test_agent_explicit_email_followup_request_generates_pending_draft(client, monkeypatch):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _create_contact(client, email="explicit-followup@example.com", name="Explicit Followup")
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="outbound",
                subject="Re: RAG chatbot",
                body="Prior follow-up.",
                source="queue",
                occurred_at=utcnow(),
            )
        )
        db.commit()

    async def fake_next_reply(db, contact, history, payload):
        assert contact.email == "explicit-followup@example.com"
        return {
            "subject": "Re: RAG chatbot",
            "body": "Explicit Followup, can we meet and discuss the RAG chatbot scope?\n\nBest regards\nRoss Dmello",
            "warnings": [],
        }

    monkeypatch.setattr("app.agent.tools._generate_next_reply", fake_next_reply)

    response = _chat(client, "can u send another follow up mail asking can we met and discuss for explicit-followup@example.com")

    assert response["intent"] == "email_generate_draft"
    assert response["draft"]["to"] == "explicit-followup@example.com"
    assert response["pending_action"]


def test_agent_uses_single_recent_reply_context_for_pronoun_question(client):
    contact = _create_contact(client, email="dev-khan@example.com", name="dev khan")
    client.post(
        "/api/replies",
        json={
            "contact_id": contact["id"],
            "classified_as": "question",
            "raw_summary": "Okay, what is this mail about and how can I contribute to it?",
        },
    )

    first = _chat(client, "who all replied in last 1 hour", session_token="session-dev-context")
    second = _chat(client, "what did he reply", session_token="session-dev-context")
    named = _chat(client, "what did dev khan replyed", session_token="session-dev-named")

    assert "dev khan" in first["response"].lower()
    assert "Which contact" not in second["response"]
    assert "what is this mail about" in second["response"]
    assert named["intent"] == "email_read_thread"
    assert "what is this mail about" in named["response"]


def test_agent_reply_back_uses_active_recent_reply_contact_for_draft(client, monkeypatch):
    monkeypatch.setenv("FINIMATIC_FAKE_AI", "1")
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _create_contact(client, email="reply-back@example.com", name="Reply Back")
    client.post(
        "/api/replies",
        json={
            "contact_id": contact["id"],
            "classified_as": "question",
            "raw_summary": "Can you share what this email is about?",
        },
    )

    first = _chat(client, "who all replied in last 1 hour", session_token="session-reply-back")
    second = _chat(client, "reply them back", session_token="session-reply-back")

    assert "reply-back@example.com" in first["response"]
    assert second["intent"] == "email_generate_draft"
    assert second["draft"]["to"] == "reply-back@example.com"
    assert second["pending_action"]
    assert "Which contact" not in second["response"]


def test_agent_generate_short_reply_for_contact_bypasses_status_shortcut(client, monkeypatch):
    monkeypatch.setenv("FINIMATIC_FAKE_AI", "1")
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _create_contact(client, email="already-sent@example.com", name="Already Sent")
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="outbound",
                subject="Previous proof",
                body="A previous proof email was sent.",
                source="queue",
                occurred_at=utcnow(),
            )
        )
        db.commit()

    response = _chat(client, "generate a short reply for already-sent@example.com saying this is a controlled local proof")

    assert response["intent"] == "email_generate_draft"
    assert response["draft"]["to"] == "already-sent@example.com"
    assert response["pending_action"]
    assert "already replied" not in response["response"].lower()


def test_agent_confirmation_hash_uses_stored_draft_body(client, monkeypatch):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _create_contact(client, email="stored-body@example.com", name="Stored Body")
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="outbound",
                subject="Previous proof",
                body="A previous proof email was sent.",
                source="queue",
                occurred_at=utcnow(),
            )
        )
        db.commit()

    async def fake_next_reply(db, contact, history, payload):
        return {
            "subject": "Re: Previous proof",
            "body": "Stored Body, this controlled proof mentions tokens and scopes without including any secret values.",
            "warnings": [],
        }

    monkeypatch.setattr("app.agent.tools._generate_next_reply", fake_next_reply)

    response = _chat(client, "generate a short reply for stored-body@example.com saying this is a controlled local proof")
    confirmed = client.post(
        "/api/agent/confirm",
        json={"session_token": SESSION_A, "action_id": response["pending_action"]["action_id"]},
    ).json()

    assert "tokens and scopes" in response["draft"]["body"]
    assert confirmed["error_code"] is None
    assert len(client.app.state.transport.sent) == 1
    assert "tokens and scopes" in client.app.state.transport.sent[0]["body"]


def test_tool_read_thread(client):
    contact = _create_contact(client, email="thread@example.com", name="Thread Lead")
    long_body = "x" * 260
    with SessionLocal() as db:
        from app.db.models import ConversationMessage

        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="inbound",
                subject="Re: hello",
                body=long_body,
                source="test",
                external_message_id="thread-1",
                occurred_at=utcnow(),
            )
        )
        db.commit()

    response = _chat(client, "show Thread Lead's thread")

    assert response["intent"] == "email_read_thread"
    assert "thread@example.com" in response["response"]
    assert "x" * 200 in response["response"]
    assert "x" * 201 not in response["response"]
    assert "evidence" not in response


def test_agent_answers_most_recent_named_contact_message(client):
    contact = _create_contact(client, email="educator@example.com", name="Data Science Educator")
    other = _create_contact(client, email="coach@example.com", name="Career Coach Creator")
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="inbound",
                subject="Re: Python",
                body="I am available Thursday or Friday afternoon IST for the Python chatbot call.",
                source="imap",
                occurred_at=utcnow(),
            )
        )
        db.add(
            ConversationMessage(
                contact_id=other["id"],
                direction="inbound",
                subject="Re: Coaching",
                body="Please remove me from your outreach.",
                source="imap",
                occurred_at=utcnow(),
            )
        )
        db.commit()

    response = _chat(client, "What did the data science educator say in their most recent message?")

    assert response["intent"] == "email_read_thread"
    assert "Thursday or Friday afternoon IST" in response["response"]
    assert "remove me" not in response["response"].lower()


def test_agent_answers_status_and_suppression_for_named_contact(client):
    contact = _create_contact(client, email="coach-status@example.com", name="Career Coach Creator")
    with SessionLocal() as db:
        db_contact = db.get(Contact, contact["id"])
        db_contact.status = "unsubscribed"
        db.add(Suppression(email=contact["email"], reason="unsubscribe", source="reply"))
        db.commit()

    status = _chat(client, "What is the current status of the career coaching contact?")
    suppressed = _chat(client, f"Is {contact['email']} currently suppressed?")

    assert "asked to be removed" in status["response"]
    assert "YES" in suppressed["response"]
    assert "unsubscribe" in suppressed["response"]


def test_agent_counts_autonomous_replies_last_two_hours(client):
    contact = _create_contact(client, email="auto-count@example.com", name="Auto Count")
    with SessionLocal() as db:
        for index in range(2):
            db.add(
                ConversationMessage(
                    contact_id=contact["id"],
                    direction="outbound",
                    subject=f"Re: {index}",
                    body="Autonomous reply.",
                    source="auto_reply",
                    auto_sent=True,
                    occurred_at=utcnow(),
                )
            )
        db.commit()

    response = _chat(client, "How many autonomous replies were sent in the last 2 hours?")

    assert response["intent"] == "queue_status"
    assert "2 autonomous replies" in response["response"]


def test_agent_drafts_followup_from_thread_context(client, monkeypatch):
    contact = _create_contact(client, email="educator-draft@example.com", name="Data Science Educator")
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="inbound",
                subject="Re: Python",
                body="My Python students need course Q&A and I can talk Thursday afternoon IST.",
                source="imap",
                occurred_at=utcnow(),
            )
        )
        db.commit()

    async def fake_next_reply(db, contact, history, payload):
        return {
            "subject": "Re: Python course Q&A assistant",
            "body": "Data Science Educator, based on the Python course Q&A thread and your Thursday afternoon IST availability, I can map the first chatbot scope around your course material. Would Thursday afternoon IST still work for a 20-minute scope call?\n\nBest regards\nRoss Dmello\nAI Systems Engineer",
            "warnings": [],
        }

    monkeypatch.setattr("app.agent.tools._generate_next_reply", fake_next_reply)

    response = _chat(client, "Draft a follow-up for the data science educator based on our conversation so far.")

    assert response["draft"]
    assert "Python course Q&A" in response["draft"]["body"]
    assert "Thursday afternoon IST" in response["draft"]["body"]
    assert "cohort" not in response["draft"]["body"].lower()


def test_agent_continues_contact_clarification_for_main_one_response(client, monkeypatch):
    contact = _create_contact(client, email="educator-main@example.com", name="Data Science Educator")
    _create_contact(client, email="coach-main@example.com", name="Career Coach Creator")
    with SessionLocal() as db:
        db_contact = db.get(Contact, contact["id"])
        db_contact.status = "replied"
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="inbound",
                subject="Re: Python course Q&A",
                body="Can the assistant answer only from my Python course material and avoid generic answers?",
                source="imap",
                occurred_at=utcnow(),
            )
        )
        db.commit()

    async def fake_next_reply(db, contact, history, payload):
        assert contact.email == "educator-main@example.com"
        assert any("Python course material" in message.body for message in history)
        return {
            "subject": "Re: Python course Q&A",
            "body": "Data Science Educator, yes. The assistant can stay grounded in your Python course material and route anything outside the course back to you.\n\nBest regards\nRoss Dmello\nAI Systems Engineer",
            "warnings": [],
        }

    monkeypatch.setattr("app.agent.tools._generate_next_reply", fake_next_reply)

    first = _chat(client, "generate a response", session_token="session-main-one")
    second = _chat(client, "the main one", session_token="session-main-one")

    assert first["is_clarification"] is True
    assert "Which contact" in first["response"]
    assert "Data Science Educator" in first["response"]
    assert second["intent"] == "email_generate_draft"
    assert second["draft"]
    assert second["pending_action"]
    assert "Python course material" in second["draft"]["body"]
    assert "Total contacts" not in second["response"]


def test_agent_main_one_uses_offered_candidate_not_global_guess(client, monkeypatch):
    target = _create_contact(client, email="pending-candidate@example.com", name="Pending Candidate")
    distractor = _create_contact(client, email="already-answered@example.com", name="Already Answered")
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=target["id"],
                direction="inbound",
                subject="Re: Course bot",
                body="Can the bot answer only from my uploaded course material?",
                source="imap",
                occurred_at=utcnow(),
            )
        )
        db.add(
            ConversationMessage(
                contact_id=distractor["id"],
                direction="inbound",
                subject="Re: Old thread",
                body="Can we talk?",
                source="imap",
                occurred_at=utcnow() - timedelta(minutes=10),
            )
        )
        db.add(
            ConversationMessage(
                contact_id=distractor["id"],
                direction="outbound",
                subject="Re: Old thread",
                body="Yes, I replied already.",
                source="agent",
                occurred_at=utcnow(),
            )
        )
        db.commit()

    async def fake_next_reply(db, contact, history, payload):
        assert contact.email == "pending-candidate@example.com"
        return {
            "subject": "Re: Course bot",
            "body": "Pending Candidate, yes. The bot can stay grounded in your uploaded course material.\n\nBest regards\nRoss Dmello",
            "warnings": [],
        }

    monkeypatch.setattr("app.agent.tools._generate_next_reply", fake_next_reply)

    first = _chat(client, "generate a response", session_token="session-offered-candidate")
    second = _chat(client, "the main one", session_token="session-offered-candidate")

    assert "Pending Candidate" in first["response"]
    assert "Already Answered" not in first["response"]
    assert second["draft"]
    assert second["draft"]["to"] == "pending-candidate@example.com"


def test_agent_ok_after_contact_clarification_does_not_guess(client):
    first = _chat(client, "generate a response", session_token="session-ok-no-candidate")
    second = _chat(client, "ok", session_token="session-ok-no-candidate")

    assert first["is_clarification"] is True
    assert second["is_clarification"] is True
    assert second["draft"] is None
    assert second["pending_action"] is None
    assert "Which contact" in second["response"]
    assert "Total contacts" not in second["response"]


def test_agent_plain_ok_does_not_dump_campaign_snapshot(client):
    response = _chat(client, "ok", session_token="session-plain-ok")

    assert response["intent"] == "acknowledgement"
    assert "Got it" in response["response"]
    assert "Total contacts" not in response["response"]


def test_agent_refuses_duplicate_response_when_latest_message_is_outbound(client):
    contact = _create_contact(client, email="already-replied@example.com", name="Already Replied")
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="inbound",
                subject="Re: Python",
                body="Can it answer from my course material?",
                source="imap",
                occurred_at=utcnow() - timedelta(minutes=5),
            )
        )
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="outbound",
                subject="Re: Python",
                body="Yes, it can stay grounded in your course material.",
                source="agent",
                occurred_at=utcnow(),
            )
        )
        db.commit()

    response = _chat(client, "generate a response for Already Replied")

    assert response["draft"] is None
    assert response["pending_action"] is None
    assert response["error_code"] == "no_pending_reply"
    assert "already replied" in response["response"].lower()


def test_agent_refuses_duplicate_draft_when_latest_message_is_outbound(client):
    contact = _create_contact(client, email="already-drafted@example.com", name="Already Drafted")
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="inbound",
                subject="Re: Python",
                body="Can it answer from my course material?",
                source="imap",
                occurred_at=utcnow() - timedelta(minutes=5),
            )
        )
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="outbound",
                subject="Re: Python",
                body="Yes, it can stay grounded in your course material.",
                source="agent",
                occurred_at=utcnow(),
            )
        )
        db.commit()

    response = _chat(client, "generate a draft for Already Drafted")

    assert response["draft"] is None
    assert response["pending_action"] is None
    assert response["error_code"] == "no_pending_reply"
    assert "already replied" in response["response"].lower()


def test_agent_ok_with_pending_draft_reminds_confirm_without_snapshot(client, monkeypatch):
    pending = _pending_draft(client, monkeypatch, email="ok-pending@example.com", name="Ok Pending")

    response = _chat(client, "ok")

    assert pending["pending_action"]["action_id"]
    assert response["error_code"] == "confirmation_required"
    assert "Confirm" in response["response"]
    assert "Total contacts" not in response["response"]
    assert len(client.app.state.transport.sent) == 0


def test_agent_pending_draft_status_mentions_confirmation(client, monkeypatch):
    _pending_draft(client, monkeypatch, email="status-pending@example.com", name="Status Pending")

    response = _chat(client, "did I reply back or is it pending")

    assert response["intent"] == "email_read_thread"
    assert "waiting for your confirmation" in response["response"]
    assert "Confirm" in response["response"]
    assert "Total contacts" not in response["response"]


def test_agent_answers_contextual_reply_pending_for_active_contact(client):
    contact = _create_contact(client, email="context-status@example.com", name="Data Science Educator")
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="inbound",
                subject="Re: Python grounding",
                body="Can it answer only from my Python course material?",
                source="imap",
                occurred_at=utcnow(),
            )
        )
        db.commit()

    _chat(client, "show Data Science Educator's thread", session_token="session-context-status")
    response = _chat(client, "did u reply him back or is it pending", session_token="session-context-status")

    assert response["intent"] == "email_read_thread"
    assert "still waiting for your response" in response["response"]
    assert "Python course material" in response["response"]
    assert "Total contacts" not in response["response"]


def test_agent_help_question_returns_usage_not_campaign_snapshot(client):
    response = _chat(client, "what can u do", session_token="session-help")

    assert response["intent"] == "static_help"
    assert "reply questions" in response["response"].lower()
    assert "generate drafts" in response["response"].lower()
    assert "Total contacts" not in response["response"]


def test_confirmation_required(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    response = _chat(client, "send it")

    assert response["error_code"] == "confirmation_required"
    assert len(client.app.state.transport.sent) == 0


def test_confirmation_valid(client, monkeypatch):
    pending = _pending_draft(client, monkeypatch)

    sent = client.post("/api/agent/confirm", json={"session_token": SESSION_A, "action_id": pending["pending_action"]["action_id"]}).json()

    assert sent["error_code"] is None
    assert "Sent" in sent["response"]
    assert len(client.app.state.transport.sent) == 1


def test_confirmation_consumed(client, monkeypatch):
    pending = _pending_draft(client, monkeypatch)
    payload = {"session_token": SESSION_A, "action_id": pending["pending_action"]["action_id"]}

    first = client.post("/api/agent/confirm", json=payload).json()
    second = client.post("/api/agent/confirm", json=payload).json()

    assert first["error_code"] is None
    assert second["error_code"] == "consumed"
    assert len(client.app.state.transport.sent) == 1


def test_dry_run_confirmation_is_consumed_before_live_mode(client, monkeypatch):
    pending = _pending_draft(
        client,
        monkeypatch,
        email="assistant-dry-run-replay@example.com",
        name="Dry Run Replay",
    )
    client.post("/api/settings", json={"dry_run": True})
    payload = {
        "session_token": SESSION_A,
        "action_id": pending["pending_action"]["action_id"],
    }

    simulated = client.post("/api/agent/confirm", json=payload).json()
    client.post("/api/settings", json={"dry_run": False})
    replay = client.post("/api/agent/confirm", json=payload).json()

    assert simulated["error_code"] == "dry_run"
    assert replay["error_code"] == "consumed"
    assert len(client.app.state.transport.sent) == 0
    with SessionLocal() as db:
        action = db.get(PendingEmailActionRow, pending["pending_action"]["action_id"])
        assert action.consumed is True


def test_distinct_assistant_confirmations_share_one_provider_dispatch(client, monkeypatch):
    pending = _pending_draft(
        client,
        monkeypatch,
        email="assistant-concurrent@example.com",
        name="Assistant Concurrent",
    )
    draft_id = pending["draft"]["draft_id"]
    with SessionLocal() as db:
        draft = db.get(Draft, draft_id)
        session_b = get_or_create_session(SESSION_B, db)
        second_action = create_pending_action(
            session_b.id,
            draft.id,
            draft.contact_id,
            draft.subject,
            draft.body,
            db,
        )
        db.commit()
        second_action_id = second_action.id

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
            "/api/agent/confirm",
            json={
                "session_token": SESSION_A,
                "action_id": pending["pending_action"]["action_id"],
            },
        )
        first_result["status_code"] = response.status_code
        first_result["body"] = response.json()

    worker = threading.Thread(target=confirm_first, daemon=True)
    worker.start()
    assert provider_entered.wait(timeout=10)
    competing = client.post(
        "/api/agent/confirm",
        json={"session_token": SESSION_B, "action_id": second_action_id},
    )
    provider_release.set()
    worker.join(timeout=10)
    assert not worker.is_alive()

    assert first_result["status_code"] == 200
    assert first_result["body"]["error_code"] is None
    assert competing.status_code == 409
    assert competing.json()["error_code"] == "reconciliation_required"
    assert len(client.app.state.transport.sent) == 1
    with SessionLocal() as db:
        attempts = db.query(SendAttempt).filter_by(queue_id="agent", draft_id=draft_id).all()
        assert len(attempts) == 1
        assert attempts[0].provider_accepted is True
        assert attempts[0].dispatch_lock_key


def test_reply_after_assistant_attempt_blocks_provider_dispatch(client, monkeypatch):
    pending = _pending_draft(
        client,
        monkeypatch,
        email="assistant-stop-fence@example.com",
        name="Assistant Stop Fence",
    )
    draft_id = pending["draft"]["draft_id"]
    from app.agent import tools as agent_tools

    original_begin = agent_tools.begin_provider_attempt

    def inject_reply_after_attempt(db, **kwargs):
        attempt, state = original_begin(db, **kwargs)
        if state == "ready":
            draft = db.get(Draft, draft_id)
            create_reply_record(
                db,
                db.get(Contact, draft.contact_id),
                "reply",
                "Stop before the Assistant provider call.",
                external_message_id="<assistant-stop-fence@example.com>",
                stop_followups=True,
                intent="positive_interest",
            )
            db.commit()
        return attempt, state

    monkeypatch.setattr(agent_tools, "begin_provider_attempt", inject_reply_after_attempt)
    response = client.post(
        "/api/agent/confirm",
        json={"session_token": SESSION_A, "action_id": pending["pending_action"]["action_id"]},
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "policy_changed_before_provider_call"
    assert len(client.app.state.transport.sent) == 0
    with SessionLocal() as db:
        draft = db.get(Draft, draft_id)
        attempt = db.query(SendAttempt).filter_by(queue_id="agent", draft_id=draft_id).one()
        contact = db.get(Contact, draft.contact_id)
        assert attempt.status == "blocked"
        assert attempt.provider_contacted is False
        assert attempt.provider_accepted is False
        assert contact.send_stop_generation > attempt.stop_generation
        assert db.query(ConversationMessage).filter_by(contact_id=draft.contact_id, direction="outbound").count() == 0


def test_assistant_stop_during_provider_call_requires_reconciliation(client, monkeypatch):
    pending = _pending_draft(
        client,
        monkeypatch,
        email="assistant-late-stop@example.com",
        name="Assistant Late Stop",
    )
    draft_id = pending["draft"]["draft_id"]
    original_send = client.app.state.transport.send

    def accept_then_stop(**kwargs):
        outcome = original_send(**kwargs)
        with SessionLocal() as db:
            draft = db.get(Draft, draft_id)
            create_reply_record(
                db,
                db.get(Contact, draft.contact_id),
                "reply",
                "Stop arrived while the Assistant provider call was in flight.",
                external_message_id="<assistant-late-stop@example.com>",
                stop_followups=True,
                intent="positive_interest",
            )
            db.commit()
        return outcome

    client.app.state.transport.send = accept_then_stop
    response = client.post(
        "/api/agent/confirm",
        json={"session_token": SESSION_A, "action_id": pending["pending_action"]["action_id"]},
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "contact_stopped_after_provider_call"
    assert len(client.app.state.transport.sent) == 1
    with SessionLocal() as db:
        draft = db.get(Draft, draft_id)
        attempt = db.query(SendAttempt).filter_by(queue_id="agent", draft_id=draft_id).one()
        assert attempt.status == "reconciliation_required"
        assert attempt.provider_accepted is True
        assert db.query(ConversationMessage).filter_by(contact_id=draft.contact_id, direction="outbound").count() == 0


def test_confirmation_expired(client, monkeypatch):
    pending = _pending_draft(client, monkeypatch)
    with SessionLocal() as db:
        action = db.get(PendingEmailActionRow, pending["pending_action"]["action_id"])
        action.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    response = client.post("/api/agent/confirm", json={"session_token": SESSION_A, "action_id": pending["pending_action"]["action_id"]}).json()

    assert response["error_code"] == "expired"
    assert len(client.app.state.transport.sent) == 0


def test_confirmation_session_mismatch(client, monkeypatch):
    pending = _pending_draft(client, monkeypatch)

    response = client.post("/api/agent/confirm", json={"session_token": SESSION_B, "action_id": pending["pending_action"]["action_id"]}).json()

    assert response["error_code"] == "session_mismatch"
    assert len(client.app.state.transport.sent) == 0


def test_confirmation_draft_changed(client, monkeypatch):
    pending = _pending_draft(client, monkeypatch)
    with SessionLocal() as db:
        draft = db.get(Draft, pending["draft"]["draft_id"])
        draft.body = f"{draft.body}\nChanged after confirmation."
        db.commit()

    response = client.post("/api/agent/confirm", json={"session_token": SESSION_A, "action_id": pending["pending_action"]["action_id"]}).json()

    assert response["error_code"] == "hash_mismatch"
    assert len(client.app.state.transport.sent) == 0


def test_confirmation_deleted_contact_is_cancelled(client, monkeypatch):
    pending = _pending_draft(client, monkeypatch, email="delete-before-confirm@example.com", name="Sarah")
    with SessionLocal() as db:
        action = db.get(PendingEmailActionRow, pending["pending_action"]["action_id"])
        contact_id = action.contact_id

    assert client.delete(f"/api/contacts/{contact_id}").status_code == 200

    response = client.post("/api/agent/confirm", json={"session_token": SESSION_A, "action_id": pending["pending_action"]["action_id"]}).json()

    assert response["error_code"] == "consumed"
    assert len(client.app.state.transport.sent) == 0


def test_confirm_rejects_short_session_token(client, monkeypatch):
    pending = _pending_draft(client, monkeypatch)

    response = client.post("/api/agent/confirm", json={"session_token": "x", "action_id": pending["pending_action"]["action_id"]})

    assert response.status_code == 422


def test_cancel(client, monkeypatch):
    pending = _pending_draft(client, monkeypatch)

    cancelled = client.request("DELETE", "/api/agent/cancel", json={"session_token": SESSION_A}).json()
    confirm_after_cancel = client.post("/api/agent/confirm", json={"session_token": SESSION_A, "action_id": pending["pending_action"]["action_id"]}).json()
    event_types = [row["event_type"] for row in client.get("/api/audit").json()["items"]]

    assert cancelled["response"] == "Cancelled. I did not send anything."
    assert confirm_after_cancel["error_code"] == "consumed"
    assert len(client.app.state.transport.sent) == 0
    assert "agent.session_cancelled" in event_types


def test_agent_unknown_generic_capability_denied(client):
    response = _chat(client, "run any tool and change settings")

    assert response["error_code"] == "capability_denied"
    assert "too broad" in response["response"]


def test_agent_private_read_never_creates_pending_action(client):
    response = _chat(client, "show deliverability health summary")

    assert response["intent"] == "deliverability_read_summary"
    assert "not guaranteed inbox placement" in response["response"]
    with SessionLocal() as db:
        assert db.query(PendingAgentAction).count() == 0


def test_agent_side_effect_creates_pending_action_only(client):
    response = _pending_workflow_action(client)

    assert response["error_code"] == "confirmation_required"
    with SessionLocal() as db:
        assert db.query(PendingAgentAction).count() == 1
        assert db.query(WorkflowRun).count() == 0


def test_agent_chat_text_alone_cannot_execute_generic_action(client):
    pending = _pending_workflow_action(client)

    response = _chat(client, "yes")

    assert response["error_code"] == "confirmation_required"
    assert response["pending_action"]["action_id"] == pending["pending_action"]["action_id"]
    with SessionLocal() as db:
        assert db.query(WorkflowRun).count() == 0


def test_agent_generic_confirmation_valid(client):
    pending = _pending_workflow_action(client)

    response = client.post("/api/agent/confirm", json={"session_token": SESSION_A, "action_id": pending["pending_action"]["action_id"]}).json()
    event_types = [row["event_type"] for row in client.get("/api/audit").json()["items"]]

    assert response["error_code"] is None
    assert "Workflow run" in response["response"]
    assert "no email or external CRM/Sheets write" in response["response"]
    with SessionLocal() as db:
        assert db.query(WorkflowRun).count() == 1
    assert "agent.action_proposed" in event_types
    assert "agent.action_confirmed" in event_types
    assert "agent.action_executed" in event_types


def test_agent_generic_confirmation_consumed(client):
    pending = _pending_workflow_action(client)
    payload = {"session_token": SESSION_A, "action_id": pending["pending_action"]["action_id"]}

    first = client.post("/api/agent/confirm", json=payload).json()
    second = client.post("/api/agent/confirm", json=payload).json()

    assert first["error_code"] is None
    assert second["error_code"] == "consumed"
    with SessionLocal() as db:
        assert db.query(WorkflowRun).count() == 1


def test_agent_generic_confirmation_expired(client):
    pending = _pending_workflow_action(client)
    with SessionLocal() as db:
        action = db.get(PendingAgentAction, pending["pending_action"]["action_id"])
        action.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    response = client.post("/api/agent/confirm", json={"session_token": SESSION_A, "action_id": pending["pending_action"]["action_id"]}).json()

    assert response["error_code"] == "expired"
    with SessionLocal() as db:
        assert db.query(WorkflowRun).count() == 0


def test_agent_generic_confirmation_session_mismatch(client):
    pending = _pending_workflow_action(client)

    response = client.post("/api/agent/confirm", json={"session_token": SESSION_B, "action_id": pending["pending_action"]["action_id"]}).json()

    assert response["error_code"] == "session_mismatch"
    with SessionLocal() as db:
        assert db.query(WorkflowRun).count() == 0


def test_agent_generic_confirmation_target_mismatch(client):
    pending = _pending_workflow_action(client)
    with SessionLocal() as db:
        action = db.get(PendingAgentAction, pending["pending_action"]["action_id"])
        action.entity_id = "missing-workbook"
        db.commit()

    response = client.post("/api/agent/confirm", json={"session_token": SESSION_A, "action_id": pending["pending_action"]["action_id"]}).json()

    assert response["error_code"] == "target_mismatch"
    with SessionLocal() as db:
        assert db.query(WorkflowRun).count() == 0


def test_agent_generic_confirmation_hash_mismatch(client):
    pending = _pending_workflow_action(client)
    with SessionLocal() as db:
        action = db.get(PendingAgentAction, pending["pending_action"]["action_id"])
        action.params_hash = "tampered"
        db.commit()

    response = client.post("/api/agent/confirm", json={"session_token": SESSION_A, "action_id": pending["pending_action"]["action_id"]}).json()

    assert response["error_code"] == "hash_mismatch"
    with SessionLocal() as db:
        assert db.query(WorkflowRun).count() == 0


def test_agent_generic_confirmation_policy_now_blocked(client):
    pending = _pending_workflow_action(client)
    with SessionLocal() as db:
        action = db.get(PendingAgentAction, pending["pending_action"]["action_id"])
        workbook = db.get(Workbook, action.entity_id)
        workbook.status = "archived"
        db.commit()

    response = client.post("/api/agent/confirm", json={"session_token": SESSION_A, "action_id": pending["pending_action"]["action_id"]}).json()

    assert response["error_code"] == "policy_now_blocked"
    with SessionLocal() as db:
        assert db.query(WorkflowRun).count() == 0


def test_agent_generic_response_omits_secret_like_values(client):
    response = _chat(client, "start workflow run")

    serialized = str(response)
    assert "gsk_" not in serialized
    assert "AIza" not in serialized
    assert "app_password" not in serialized


def test_no_raw_key_in_response(client):
    groq_prefix = "gsk" + "_"
    gemini_prefix = "AI" + "za"
    contact = _create_contact(client, email="secret-reply@example.com", name="Secret Reply")
    client.post(
        "/api/replies",
        json={
            "contact_id": contact["id"],
            "classified_as": "reply",
            "raw_summary": f"{groq_prefix}secret {gemini_prefix}Secret app_password",
        },
    )

    response = _chat(client, "who replied today?")

    assert groq_prefix not in str(response)
    assert gemini_prefix not in str(response)
    assert "app_password" not in str(response)


def test_agent_response_omits_evidence_payload(client):
    contact = _create_contact(client, email="evidence-hidden@example.com", name="Evidence Hidden")
    client.post("/api/replies", json={"contact_id": contact["id"], "classified_as": "reply", "raw_summary": "Interested."})

    response = _chat(client, "who replied today?")

    assert "evidence" not in response


def test_agent_long_message_preserves_tail_contact_identifier(client, monkeypatch):
    monkeypatch.setenv("FINIMATIC_FAKE_AI", "1")
    configure_sender(client, canary_verified=True, dry_run=False)
    contact = _create_contact(client, email="long-tail@example.com", name="Long Tail")
    message = "context " + ("x" * 5200) + " generate a reply for long-tail@example.com"

    response = _chat(client, message)

    assert response["draft"]["contact_id"] == contact["id"]
    assert response["draft"]["to"] == "long-tail@example.com"
    assert response["pending_action"]["action_id"]


def test_agent_ai_failure_does_not_create_blank_draft(client, monkeypatch):
    configure_sender(client, canary_verified=True, dry_run=False)
    _create_contact(client, email="ai-fail@example.com", name="AI Fail")

    async def fail_generate(self, contact, provider, tone, length):
        return AIFailure(error_code="model_unavailable_rate_limited", provider=provider, detail="rate_limit")

    monkeypatch.setattr("app.ai.gateway.AIGateway.generate_draft", fail_generate)

    response = _chat(client, "generate a reply for ai-fail@example.com")

    assert response["error_code"] == "model_unavailable_rate_limited"
    assert response.get("draft") is None
    assert "manual draft" in response["response"].lower() or "retry" in response["response"].lower()
    with SessionLocal() as db:
        assert db.query(Draft).count() == 0


def test_expired_agent_session_reuses_token_without_integrity_error(client):
    first = _chat(client, "queue status", session_token="session-token-expire")
    assert first["intent"] == "queue_status"
    with SessionLocal() as db:
        session = db.query(AgentSession).one()
        session.current_goal = "old goal"
        session.expires_at = utcnow() - timedelta(minutes=31)
        db.commit()

    second = _chat(client, "queue status", session_token="session-token-expire")

    assert second["intent"] == "queue_status"
    assert second["error_code"] is None
    with SessionLocal() as db:
        sessions = db.query(AgentSession).all()
        assert len(sessions) == 1
        assert sessions[0].current_goal == "queue status"


def test_audit_redacts_secret_like_values(client):
    groq_prefix = "gsk" + "_"
    gemini_prefix = "AI" + "za"
    with SessionLocal() as db:
        emit_event(
            db,
            "agent.security_test",
            payload={
                "message": (
                    "token=abc123secret "
                    f"{groq_prefix}liveSecret {gemini_prefix}LiveSecret "
                    "gAAAAabcdefghijklmnopqrstuvwxyz0123456789"
                ),
                "nested": {"api_key": "plain-secret"},
            },
        )
        db.commit()

    payloads = [row["payload"] for row in client.get("/api/audit").json()["items"]]
    rendered = str(payloads)

    assert groq_prefix not in rendered
    assert gemini_prefix not in rendered
    assert "gAAAA" not in rendered
    assert "abc123secret" not in rendered
    assert "plain-secret" not in rendered


def test_audit_rows_include_layman_contact_detail(client):
    contact = _create_contact(client, email="audit-readable@example.com", name="Audit Readable")
    with SessionLocal() as db:
        draft = Draft(contact_id=contact["id"], subject="Readable audit subject", body="Body", approved=True)
        db.add(draft)
        db.flush()
        emit_event(db, "draft.approved", entity_type="draft", entity_id=draft.id, payload={"queue_id": "queue-123"})
        db.commit()

    row = [item for item in client.get("/api/audit").json()["items"] if item["event_type"] == "draft.approved"][-1]

    assert row["event_label"] == "Draft approved"
    assert row["contact_name"] == "Audit Readable"
    assert row["contact_email"] == "audit-readable@example.com"
    assert "Audit Readable (audit-readable@example.com)" in row["detail"]
    assert "draft.approved" not in row["detail"]


def test_generate_draft_not_send(client, monkeypatch):
    response = _pending_draft(client, monkeypatch)

    assert response["draft"]["to"] == "sarah@example.com"
    assert response["pending_action"]["action_id"]
    assert len(client.app.state.transport.sent) == 0


def test_agent_composes_explicit_certification_email_with_pending_confirm(client):
    _create_contact(client, email="crce.9955.ce@gmail.com", name="Career Coach Creator")

    response = _chat(
        client,
        "Compose and send a certification confirmation email to crce.9955.ce@gmail.com with subject: "
        "Finimatic Certification Complete and body confirming that all dual-account, autonomous reply, "
        "policy gate, and quality audit tests passed today with the current timestamp. "
        "Sign it as Ross Dmello, AI Systems Engineer.",
    )

    assert response["intent"] == "email_generate_draft"
    assert response["draft"]["to"] == "crce.9955.ce@gmail.com"
    assert response["draft"]["subject"] == "Finimatic Certification Complete"
    assert "All dual-account, autonomous reply, policy gate, and quality audit tests passed today at" in response["draft"]["body"]
    assert "Ross Dmello\nAI Systems Engineer" in response["draft"]["body"]
    assert response["pending_action"]["action_id"]
    assert len(client.app.state.transport.sent) == 0


def test_audit_written(client):
    _chat(client, "queue status")

    event_types = [row["event_type"] for row in client.get("/api/audit").json()["items"]]
    assert "agent.goal_framed" in event_types
    assert "agent.tool_executed" in event_types
