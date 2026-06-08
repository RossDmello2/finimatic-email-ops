from __future__ import annotations

import asyncio
from datetime import timedelta

from app.ai.schema import DraftSuggestion
from app.conversations.auto_reply_service import AutoReplyService
from app.core.time import utcnow
from app.db.models import Contact, ConversationMessage, Draft, Reply, Suppression
from app.db.session import SessionLocal
from app.replies import imap_fetcher
from app.replies.service import create_reply_record
from conftest import configure_sender

PHASE12_SESSION = "phase12-auto-reply-session"


def _configure_auto(client, *, mode="propose", dry_run=False, cap=20, min_gap=60):
    configure_sender(client, canary_verified=True, dry_run=dry_run)
    settings_mode = "propose" if mode == "autonomous" else mode
    response = client.post(
        "/api/settings",
        json={
            "auto_reply_enabled": True,
            "auto_reply_mode": settings_mode,
            "auto_reply_daily_cap": cap,
            "auto_reply_min_gap_minutes": min_gap,
            "auto_reply_safe_intents": "positive_interest,objection,question",
            "send_window_start": "00:00",
            "send_window_end": "23:59",
            "send_timezone": "UTC",
        },
    )
    assert response.status_code == 200
    if mode == "autonomous":
        prepared = client.post(
            "/api/auto-reply/autonomous/prepare",
            json={"session_token": PHASE12_SESSION},
        )
        assert prepared.status_code == 202
        confirmed = client.post(
            "/api/auto-reply/autonomous/confirm",
            json={
                "session_token": PHASE12_SESSION,
                "action_id": prepared.json()["pending_action"]["action_id"],
            },
        )
        assert confirmed.status_code == 200


def _approve_auto_reply(client, draft_id: str):
    prepared = client.post(
        f"/api/auto-reply/approve/{draft_id}",
        json={"session_token": PHASE12_SESSION},
    )
    assert prepared.status_code == 202
    return client.post(
        f"/api/auto-reply/confirm/{draft_id}",
        json={
            "session_token": PHASE12_SESSION,
            "action_id": prepared.json()["pending_action"]["action_id"],
        },
    )


def _contact(client, email="auto@example.com", name="Auto Contact", **extra):
    payload = {"email": email, "creator_name": name, "source": "manual", **extra}
    response = client.post("/api/contacts", json=payload)
    assert response.status_code == 200
    return response.json()


def _reply(contact_id: str, *, intent="positive_interest", classified_as="reply", raw="That sounds interesting, tell me more.") -> str:
    with SessionLocal() as db:
        contact = db.get(Contact, contact_id)
        row, created = create_reply_record(
            db,
            contact,
            classified_as,
            raw,
            subject="Re: RAG chatbot",
            external_message_id=f"<{contact_id}-{intent}-{classified_as}@example>",
            intent=intent,
        )
        db.commit()
        assert created is True
        return row.id


async def _good_reply(db, contact, messages, payload):
    return {
        "subject": "Re: RAG chatbot",
        "body": (
            f"Hi {contact.creator_name},\n\n"
            "Your latest note about the course chatbot is clear. A sensible next step is to map the recurring student questions, "
            "then decide what source material should ground the answers. I can keep the first version focused on course Q&A "
            "instead of a broad automation build.\n\n"
            "Would you be open to sharing two suitable times for a short call?\n\n"
            "Best regards\nRoss Dmello\nAI Systems Engineer"
        ),
        "provider": "fake",
        "model": "fake",
        "reasoning_summary": "test",
    }


async def _bad_reply(db, contact, messages, payload):
    return {
        "subject": "RAG chatbot",
        "body": (
            "I hope this finds you well. This cutting-edge system will increase revenue by 40% and create synergy for your course. "
            "Let me know. Can we touch base?\n\nBest regards\nRoss Dmello\nAI Systems Engineer"
        ),
        "provider": "fake",
        "model": "fake",
        "reasoning_summary": "test",
    }


def test_auto_reply_disabled_globally(client):
    contact = _contact(client)
    reply_id = _reply(contact["id"])
    with SessionLocal() as db:
        should, mode = asyncio.run(AutoReplyService().should_auto_reply(db.get(Contact, contact["id"]), db.get(Reply, reply_id), db))
    assert should is False
    assert mode == ""


def test_auto_reply_unsafe_intent_blocked(client):
    _configure_auto(client)
    contact = _contact(client, email="unsafe-intent@example.com")
    reply_id = _reply(contact["id"], intent="negative_no", raw="No thanks.")
    with SessionLocal() as db:
        should, mode = asyncio.run(AutoReplyService().should_auto_reply(db.get(Contact, contact["id"]), db.get(Reply, reply_id), db))
    assert (should, mode) == (False, "")


def test_auto_reply_unsubscribe_blocked(client):
    _configure_auto(client)
    contact = _contact(client, email="unsubscribe-auto@example.com")
    reply_id = _reply(contact["id"], intent="unsubscribe", classified_as="unsubscribe", raw="Please remove me.")
    with SessionLocal() as db:
        should, mode = asyncio.run(AutoReplyService().should_auto_reply(db.get(Contact, contact["id"]), db.get(Reply, reply_id), db))
    assert (should, mode) == (False, "")


def test_auto_reply_suppressed_contact_blocked(client):
    _configure_auto(client)
    contact = _contact(client, email="suppressed-auto@example.com")
    reply_id = _reply(contact["id"])
    with SessionLocal() as db:
        db.add(Suppression(email=contact["email"], reason="manual", source="test"))
        db.commit()
        should, mode = asyncio.run(AutoReplyService().should_auto_reply(db.get(Contact, contact["id"]), db.get(Reply, reply_id), db))
    assert (should, mode) == (False, "")


def test_auto_reply_gap_too_short(client):
    _configure_auto(client, min_gap=60)
    contact = _contact(client, email="gap-auto@example.com")
    reply_id = _reply(contact["id"])
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="outbound",
                subject="Re: prior",
                body="Prior auto reply",
                source="auto_reply",
                auto_sent=True,
                external_message_id="prior-auto",
                occurred_at=utcnow() - timedelta(minutes=10),
            )
        )
        db.commit()
        should, mode = asyncio.run(AutoReplyService().should_auto_reply(db.get(Contact, contact["id"]), db.get(Reply, reply_id), db))
    assert (should, mode) == (False, "")


def test_auto_reply_gap_counts_pending_proposal_per_contact(client):
    _configure_auto(client, min_gap=60)
    contact_a = _contact(client, email="gap-proposed-a@example.com")
    contact_b = _contact(client, email="gap-proposed-b@example.com")
    reply_a = _reply(contact_a["id"], raw="Can the chatbot run quizzes?")
    reply_b = _reply(contact_b["id"], raw="How do you handle data privacy?")
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact_a["id"],
                direction="outbound",
                subject="Re: prior",
                body="Prior proposed auto reply",
                source="auto_reply_proposed",
                auto_sent=False,
                external_message_id="draft:prior-proposed-auto",
                occurred_at=utcnow() - timedelta(minutes=1),
            )
        )
        db.commit()
        should_a, mode_a = asyncio.run(AutoReplyService().should_auto_reply(db.get(Contact, contact_a["id"]), db.get(Reply, reply_a), db))
        should_b, mode_b = asyncio.run(AutoReplyService().should_auto_reply(db.get(Contact, contact_b["id"]), db.get(Reply, reply_b), db))
    assert (should_a, mode_a) == (False, "")
    assert (should_b, mode_b) == (True, "propose")


def test_auto_reply_context_resets_after_fresh_reengagement_send(client, monkeypatch):
    _configure_auto(client, mode="propose", min_gap=0)
    contact = _contact(client, email="reengaged-history@example.com", name="Career Coach Creator")
    captured: dict[str, list[str]] = {}

    async def _capture_reply(db, contact, messages, payload):
        captured["bodies"] = [message.body for message in messages]
        return await _good_reply(db, contact, messages, payload)

    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="outbound",
                subject="AI support that keeps your career coaching personal",
                body="Old thread mentioned personal touch, cohort, 6 weeks, and opt out concerns.",
                source="queue",
                occurred_at=utcnow() - timedelta(days=3),
            )
        )
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="inbound",
                subject="Re: AI support that keeps your career coaching personal",
                body="Old inbound about personal touch and cohort timing.",
                source="imap",
                occurred_at=utcnow() - timedelta(days=3, minutes=-5),
            )
        )
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="outbound",
                subject="Fresh coaching assistant scope",
                body="Fresh note about pricing and scope for a controlled assistant.",
                source="conversation",
                occurred_at=utcnow() - timedelta(minutes=2),
            )
        )
        db.commit()

    reply_id = _reply(
        contact["id"],
        intent="question",
        raw="You emailed again. Actually I'm reconsidering. What exactly would the pricing structure look like?",
    )
    monkeypatch.setattr("app.conversations.auto_reply_service._generate_next_reply", _capture_reply)

    with SessionLocal() as db:
        result = asyncio.run(AutoReplyService().generate_and_maybe_send(contact["id"], reply_id, "propose", db))
        db.commit()
        draft = db.get(Draft, result.draft_id)

    scoped_text = "\n".join(captured["bodies"]).lower()
    assert result.action == "proposed"
    assert draft is not None
    assert "pricing structure" in scoped_text
    assert "fresh note about pricing" in scoped_text
    assert "personal touch" not in scoped_text
    assert "cohort" not in scoped_text
    assert "6 weeks" not in scoped_text
    assert "opt out" not in scoped_text


def test_auto_reply_single_character_question_gets_short_clarifier(client, monkeypatch):
    _configure_auto(client, mode="propose", min_gap=0)
    monkeypatch.setattr("app.conversations.auto_reply_service._generate_next_reply", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("minimal question should not call model")))
    contact = _contact(client, email="minimal-question@example.com", name="Career Coach Creator")
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="outbound",
                subject="AI assistant scope",
                body="Fresh note about scope.",
                source="conversation",
                occurred_at=utcnow() - timedelta(minutes=2),
            )
        )
        db.commit()
    reply_id = _reply(
        contact["id"],
        intent="question",
        raw="? On Sun, 24 May 2026 at 21:58, <sender@example.com> wrote: previous message",
    )

    with SessionLocal() as db:
        result = asyncio.run(AutoReplyService().generate_and_maybe_send(contact["id"], reply_id, "propose", db))
        db.commit()
        draft = db.get(Draft, result.draft_id)

    assert result.action == "proposed"
    assert draft.subject == "Re: RAG chatbot"
    assert draft.body.count("?") == 1
    assert len(draft.body.split("\n\n", 1)[0].split()) < 30
    assert len(draft.body.split()) >= 30
    assert "what would you like me to clarify" in draft.body


def test_auto_reply_daily_cap_exceeded(client):
    _configure_auto(client, cap=1, min_gap=0)
    contact = _contact(client, email="cap-auto@example.com")
    reply_id = _reply(contact["id"])
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="outbound",
                subject="Re: already",
                body="Already sent",
                source="auto_reply",
                auto_sent=True,
                external_message_id="cap-auto",
                occurred_at=utcnow(),
            )
        )
        db.commit()
        should, mode = asyncio.run(AutoReplyService().should_auto_reply(db.get(Contact, contact["id"]), db.get(Reply, reply_id), db))
    assert (should, mode) == (False, "")


def test_auto_reply_contact_override_disabled(client):
    _configure_auto(client)
    contact = _contact(client, email="override-off@example.com", auto_reply_override="disabled")
    client.patch(f"/api/contacts/{contact['id']}", json={"auto_reply_override": "disabled"})
    reply_id = _reply(contact["id"])
    with SessionLocal() as db:
        should, mode = asyncio.run(AutoReplyService().should_auto_reply(db.get(Contact, contact["id"]), db.get(Reply, reply_id), db))
    assert (should, mode) == (False, "")


def test_auto_reply_contact_override_propose(client):
    _configure_auto(client, mode="autonomous")
    contact = _contact(client, email="override-propose@example.com")
    client.patch(f"/api/contacts/{contact['id']}", json={"auto_reply_override": "propose"})
    reply_id = _reply(contact["id"])
    with SessionLocal() as db:
        should, mode = asyncio.run(AutoReplyService().should_auto_reply(db.get(Contact, contact["id"]), db.get(Reply, reply_id), db))
    assert (should, mode) == (True, "propose")


def test_auto_reply_propose_mode_stores_draft_not_sent(client, monkeypatch):
    _configure_auto(client, mode="propose")
    monkeypatch.setattr("app.conversations.auto_reply_service._generate_next_reply", _good_reply)
    contact = _contact(client, email="propose-mode@example.com")
    reply_id = _reply(contact["id"])
    with SessionLocal() as db:
        result = asyncio.run(AutoReplyService().generate_and_maybe_send(contact["id"], reply_id, "propose", db))
        db.commit()
        draft = db.get(Draft, result.draft_id)
    assert result.action == "proposed"
    assert draft.approved is False
    assert draft.source == "auto_reply_proposed"
    assert len(client.app.state.transport.sent) == 0


def test_auto_reply_autonomous_mode_sends(client, monkeypatch):
    _configure_auto(client, mode="autonomous", min_gap=0)
    monkeypatch.setattr("app.conversations.auto_reply_service._generate_next_reply", _good_reply)
    contact = _contact(client, email="autonomous-mode@example.com")
    reply_id = _reply(contact["id"])
    with SessionLocal() as db:
        result = asyncio.run(AutoReplyService().generate_and_maybe_send(contact["id"], reply_id, "autonomous", db))
        db.commit()
        message = db.query(ConversationMessage).filter_by(contact_id=contact["id"], auto_sent=True).first()
    assert result.action == "sent"
    assert len(client.app.state.transport.sent) == 1
    assert message is not None
    assert message.source == "auto_reply"
    event_types = [row["event_type"] for row in client.get("/api/audit").json()["items"]]
    assert "auto_reply.sent" in event_types


def test_auto_reply_quality_gate_failure_falls_back_to_propose(client, monkeypatch):
    _configure_auto(client, mode="autonomous", min_gap=0)
    monkeypatch.setattr("app.conversations.auto_reply_service._generate_next_reply", _bad_reply)
    contact = _contact(client, email="quality-fallback@example.com")
    reply_id = _reply(contact["id"])
    with SessionLocal() as db:
        result = asyncio.run(AutoReplyService().generate_and_maybe_send(contact["id"], reply_id, "autonomous", db))
        db.commit()
        draft = db.get(Draft, result.draft_id)
    assert result.action == "proposed"
    assert draft.source == "auto_reply_proposed"
    assert len(client.app.state.transport.sent) == 0


def test_deterministic_fallback_uses_latest_unquoted_ack(client):
    _configure_auto(client, mode="autonomous", min_gap=0)
    contact = _contact(client, email="ack-fallback@example.com", name="Reply Loop Data Science Educator")
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="outbound",
                subject="Re: Python assistant scope",
                body=(
                    "Reply Loop Data Science Educator, yes: the assistant can generate short quiz questions only from your "
                    "approved Python lesson examples and route anything outside that material back to you.\n\n"
                    "That keeps student practice grounded and avoids invented answers.\n\n"
                    "Would you like me to scope the first quiz set from one lesson module?\n\n"
                    "Best regards\nRoss Dmello\nAI Systems Engineer"
                ),
                source="auto_reply_approved",
                auto_sent=True,
                external_message_id="prior-approved-ack-fallback",
                occurred_at=utcnow() - timedelta(minutes=2),
            )
        )
        db.commit()

    reply_id = _reply(
        contact["id"],
        intent="positive_interest",
        raw=(
            "ok got it On Sun, 24 May 2026 at 23:29, <rossdmello869@gmail.com> wrote: "
            "the assistant can generate short quiz questions only from approved Python lesson examples"
        ),
    )
    with SessionLocal() as db:
        service = AutoReplyService()
        db_contact = db.get(Contact, contact["id"])
        reply = db.get(Reply, reply_id)
        history = db.query(ConversationMessage).filter(ConversationMessage.contact_id == contact["id"]).all()
        draft = service._deterministic_safe_reply(db_contact, reply, history, db)
        assert draft is not None
        assert "next clean step" in draft.body
        assert "short quiz questions only from your approved Python lesson examples" not in draft.body
        quality = service.quality_gate(draft, db_contact, reply, db=db)

    assert quality.passed is True
    assert quality.failures == []


def test_deterministic_fallback_handles_gmail_smart_reply_ack(client):
    _configure_auto(client, mode="autonomous", min_gap=0)
    contact = _contact(client, email="smart-reply-fallback@example.com", name="Smart Reply Data Science Educator")
    with SessionLocal() as db:
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="outbound",
                subject="Re: Python assistant scope",
                body=(
                    "Smart Reply Data Science Educator, yes: the assistant can generate short quiz questions only from your "
                    "approved Python lesson examples and route anything outside that material back to you.\n\n"
                    "Would you like me to scope the first quiz set from one lesson module?\n\n"
                    "Best regards\nRoss Dmello\nAI Systems Engineer"
                ),
                source="auto_reply_approved",
                auto_sent=True,
                external_message_id="prior-approved-smart-reply",
                occurred_at=utcnow() - timedelta(minutes=2),
            )
        )
        db.commit()

    reply_id = _reply(
        contact["id"],
        intent="positive_interest",
        raw="Yes, I would like that. On Sun, 24 May 2026 at 23:46, <rossdmello869@gmail.com> wrote: previous quiz scope",
    )
    with SessionLocal() as db:
        service = AutoReplyService()
        db_contact = db.get(Contact, contact["id"])
        reply = db.get(Reply, reply_id)
        history = db.query(ConversationMessage).filter(ConversationMessage.contact_id == contact["id"]).all()
        draft = service._deterministic_safe_reply(db_contact, reply, history, db)
        assert draft is not None
        assert "next clean step" in draft.body
        quality = service.quality_gate(draft, db_contact, reply, db=db)

    assert quality.passed is True
    assert quality.failures == []


def test_quality_gate_ignores_sender_signature_as_other_contact(client):
    _configure_auto(client)
    _contact(client, email="ross-other@example.com", name="Ross Dmello", business_name="Ross Dmello")
    contact = _contact(client, email="signature-safe@example.com", name="Signature Safe")
    reply_id = _reply(contact["id"], raw="I run a Udemy course and want student Q&A help.")
    body = _quality_body(contact["creator_name"])
    draft = DraftSuggestion(subject="Re: RAG", body=body, warnings=[])
    with SessionLocal() as db:
        result = AutoReplyService().quality_gate(draft, db.get(Contact, contact["id"]), db.get(Reply, reply_id), db=db)
    assert "cross_contact_detail" not in result.failures
    assert result.passed is True


def test_quality_gate_allows_overlapping_current_contact_identifier(client):
    _configure_auto(client)
    _contact(client, email="generic-data-science@example.com", name="Data Science Educator", business_name="Data Science Educator")
    contact = _contact(client, email="reply-loop-overlap@example.com", name="Reply Loop Data Science Educator")
    reply_id = _reply(contact["id"], raw="Yes, I would like that.")
    body = (
        "Reply Loop Data Science Educator, understood. The next clean step is to pick one narrow Python lesson and define what the assistant should answer versus route back to you.\n\n"
        "That gives you a controlled pilot without expanding beyond trusted course material.\n\n"
        "Would you like to use one quiz-heavy lesson for that pilot?\n\n"
        "Best regards\nRoss Dmello\nAI Systems Engineer"
    )
    draft = DraftSuggestion(subject="Re: RAG", body=body, warnings=[])
    with SessionLocal() as db:
        result = AutoReplyService().quality_gate(draft, db.get(Contact, contact["id"]), db.get(Reply, reply_id), db=db)
    assert "cross_contact_detail" not in result.failures
    assert result.passed is True


def test_auto_reply_approve_endpoint(client, monkeypatch):
    _configure_auto(client, mode="propose", min_gap=0)
    monkeypatch.setattr("app.conversations.auto_reply_service._generate_next_reply", _good_reply)
    contact = _contact(client, email="approve-auto@example.com")
    reply_id = _reply(contact["id"])
    with SessionLocal() as db:
        result = asyncio.run(AutoReplyService().generate_and_maybe_send(contact["id"], reply_id, "propose", db))
        db.commit()

    response = _approve_auto_reply(client, result.draft_id)

    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    assert len(client.app.state.transport.sent) == 1
    with SessionLocal() as db:
        messages = db.query(ConversationMessage).filter(ConversationMessage.contact_id == contact["id"]).all()
        assert sum(1 for message in messages if message.direction == "outbound" and message.auto_sent) == 1
        assert not any(message.source == "auto_reply_proposed" for message in messages)


def test_auto_reply_approval_allows_reset_contact_with_old_unsubscribe(client, monkeypatch):
    _configure_auto(client, mode="propose", min_gap=0)
    monkeypatch.setattr("app.conversations.auto_reply_service._generate_next_reply", _good_reply)
    contact = _contact(client, email="reset-old-unsubscribe@example.com")

    with SessionLocal() as db:
        db_contact = db.get(Contact, contact["id"])
        create_reply_record(
            db,
            db_contact,
            "unsubscribe",
            "Please remove me from your outreach.",
            subject="Re: old thread",
            external_message_id="<old-reset-unsubscribe@example>",
            intent="unsubscribe",
        )
        db.query(Suppression).filter(Suppression.email == contact["email"]).delete()
        db_contact.status = "imported"
        db.add(
            ConversationMessage(
                contact_id=contact["id"],
                direction="outbound",
                subject="Fresh reset note",
                body="Fresh note after a deliberate reset.",
                source="conversation",
                occurred_at=utcnow() - timedelta(minutes=2),
            )
        )
        db.commit()

    reply_id = _reply(
        contact["id"],
        intent="question",
        raw="You emailed again. Actually I'm reconsidering. What exactly would the pricing structure look like?",
    )
    with SessionLocal() as db:
        result = asyncio.run(AutoReplyService().generate_and_maybe_send(contact["id"], reply_id, "propose", db))
        db.commit()

    response = _approve_auto_reply(client, result.draft_id)

    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    assert len(client.app.state.transport.sent) == 1


def test_auto_reply_reject_endpoint(client, monkeypatch):
    _configure_auto(client, mode="propose")
    monkeypatch.setattr("app.conversations.auto_reply_service._generate_next_reply", _good_reply)
    contact = _contact(client, email="reject-auto@example.com")
    reply_id = _reply(contact["id"])
    with SessionLocal() as db:
        result = asyncio.run(AutoReplyService().generate_and_maybe_send(contact["id"], reply_id, "propose", db))
        db.commit()

    response = client.post(f"/api/auto-reply/reject/{result.draft_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    with SessionLocal() as db:
        messages = db.query(ConversationMessage).filter(ConversationMessage.contact_id == contact["id"]).all()
        assert not any(message.source == "auto_reply_proposed" for message in messages)
    event_types = [row["event_type"] for row in client.get("/api/audit").json()["items"]]
    assert "auto_reply.rejected" in event_types


def test_auto_reply_pending_list(client, monkeypatch):
    _configure_auto(client, mode="propose")
    monkeypatch.setattr("app.conversations.auto_reply_service._generate_next_reply", _good_reply)
    contact = _contact(client, email="pending-auto@example.com")
    reply_id = _reply(contact["id"], raw="That sounds interesting, tell me more about the chatbot.")
    with SessionLocal() as db:
        asyncio.run(AutoReplyService().generate_and_maybe_send(contact["id"], reply_id, "propose", db))
        db.add(Draft(contact_id=contact["id"], subject="Manual", body="Manual", ai_provider="manual", warnings="[]", source="manual", approved=False))
        db.commit()

    response = client.get("/api/auto-reply/pending")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["contact_email"] == "pending-auto@example.com"
    assert "tell me more" in body["items"][0]["their_reply"]


def test_imap_concurrency_lock(client):
    acquired = imap_fetcher._FETCH_LOCK.acquire(blocking=False)
    assert acquired is True
    try:
        with SessionLocal() as db:
            result = imap_fetcher.run_imap_fetch_with_lock(db)
    finally:
        imap_fetcher._FETCH_LOCK.release()
    assert result["skipped"] is True
    assert result["error_code"] == "imap_fetch_in_progress"


def test_quality_gate_banned_opener(client):
    service, contact, reply = _quality_objects(client)
    draft = DraftSuggestion(subject="Re: RAG", body="I hope this finds you well. " + _quality_body(contact["creator_name"]), warnings=[])
    with SessionLocal() as db:
        result = service.quality_gate(draft, db.get(Contact, contact["id"]), db.get(Reply, reply), db=db)
    assert "banned_opener" in result.failures


def test_quality_gate_fabricated_stat(client):
    service, contact, reply = _quality_objects(client)
    draft = DraftSuggestion(subject="Re: RAG", body=_quality_body(contact["creator_name"]).replace("A practical first version", "A 40% better first version"), warnings=[])
    with SessionLocal() as db:
        result = service.quality_gate(draft, db.get(Contact, contact["id"]), db.get(Reply, reply), db=db)
    assert "fabricated_stat" in result.failures


def test_quality_gate_no_cta(client):
    service, contact, reply = _quality_objects(client)
    body = _quality_body(contact["creator_name"]).replace("Would you be open to sharing two suitable times for a short call?", "This gives us a focused and realistic path forward.")
    draft = DraftSuggestion(subject="Re: RAG", body=body, warnings=[])
    with SessionLocal() as db:
        result = service.quality_gate(draft, db.get(Contact, contact["id"]), db.get(Reply, reply), db=db)
    assert "cta_count" in result.failures


def test_quality_gate_rejects_duplicate_cta_phrase_occurrences(client):
    service, contact, reply = _quality_objects(client)
    body = _quality_body(contact["creator_name"]).replace(
        "Would you be open to sharing two suitable times for a short call?",
        "Please share two suitable times, and please share the top student questions.",
    )
    draft = DraftSuggestion(subject="Re: RAG", body=body, warnings=[])
    with SessionLocal() as db:
        result = service.quality_gate(draft, db.get(Contact, contact["id"]), db.get(Reply, reply), db=db)
    assert "cta_count" in result.failures


def test_quality_gate_rejects_cross_contact_detail_bleed(client):
    _configure_auto(client)
    _contact(client, email="technical-alice@example.com", name="Technical Alice", business_name="Technical Academy")
    contact = _contact(client, email="personal-bob@example.com", name="Personal Bob")
    reply_id = _reply(contact["id"], raw="I run a Udemy course and want student Q&A help.")
    body = (
        "Hi Personal Bob,\n\n"
        "Your Udemy course Q&A point makes sense. Technical Alice's API workshop needs a separate backend workflow, "
        "so I would keep this focused on the student question path for your course.\n\n"
        "Would you be open to sharing two suitable times for a short call?\n\n"
        "Best regards\nRoss Dmello\nAI Systems Engineer"
    )
    draft = DraftSuggestion(subject="Re: RAG", body=body, warnings=[])
    with SessionLocal() as db:
        result = AutoReplyService().quality_gate(draft, db.get(Contact, contact["id"]), db.get(Reply, reply_id), db=db)
    assert "cross_contact_detail" in result.failures


def test_quality_gate_rejects_full_banned_phrase_set(client):
    service, contact, reply = _quality_objects(client)
    body = _quality_body(contact["creator_name"]).replace("A practical first version", "A value-add first version")
    draft = DraftSuggestion(subject="Re: RAG", body=body, warnings=[])
    with SessionLocal() as db:
        result = service.quality_gate(draft, db.get(Contact, contact["id"]), db.get(Reply, reply), db=db)
    assert "banned_phrase" in result.failures


def test_quality_gate_pass(client):
    service, contact, reply = _quality_objects(client)
    draft = DraftSuggestion(subject="Re: RAG", body=_quality_body(contact["creator_name"]), warnings=[])
    with SessionLocal() as db:
        result = service.quality_gate(draft, db.get(Contact, contact["id"]), db.get(Reply, reply), db=db)
    assert result.passed is True
    assert result.failures == []


def _quality_objects(client):
    _configure_auto(client)
    contact = _contact(client, email="quality-pass@example.com", name="Quality Pass")
    reply_id = _reply(contact["id"], raw="I run a Udemy course and want student Q&A help.")
    return AutoReplyService(), contact, reply_id


def _quality_body(name: str) -> str:
    return (
        f"Hi {name},\n\n"
        "Your Udemy course Q&A point makes sense. A practical first version can focus on the repeated student questions, "
        "the source material you already trust, and the gaps where learners get stuck. That keeps the project grounded and avoids guessing beyond your course content.\n\n"
        "Would you be open to sharing two suitable times for a short call?\n\n"
        "Best regards\nRoss Dmello\nAI Systems Engineer"
    )
