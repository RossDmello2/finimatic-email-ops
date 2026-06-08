from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.ai.gateway import GROQ_MODEL_DEFAULT
from app.ai.groq_pool import GroqKeyPool
from app.core.idempotency import sha256_key
from app.core.time import utcnow
from app.db.models import Contact, ConversationMessage, Draft, FollowUpSequence, Reply, SendAttempt, Suppression
from app.send.stop_service import stop_contact_send_work
from app.send.sequence import provider_acceptance_evidence_present
from app.settings.service import get_key_list, get_value


REPLY_INTENTS = {
    "positive_interest",
    "objection",
    "question",
    "negative_no",
    "unsubscribe",
    "auto_reply",
    "bounce",
    "unknown",
}


STATUS_BY_CLASSIFICATION = {
    "reply": "replied",
    "unsubscribe": "unsubscribed",
    "bounce": "bounced",
}

STOP_REASON_BY_CLASSIFICATION = {
    "reply": "RECIPIENT_REPLIED",
    "unsubscribe": "RECIPIENT_UNSUBSCRIBED",
    "bounce": "RECIPIENT_BOUNCED",
}


def _reply_dedupe_key(contact_id: str, external_message_id: str | None) -> str | None:
    normalized = (external_message_id or "").strip().lower()
    if not normalized:
        return None
    return sha256_key("reply", contact_id, normalized)


def reply_to_dict(row: Reply, contact_email: str | None = None) -> dict:
    return {
        "id": row.id,
        "contact_id": row.contact_id,
        "contact_email": contact_email,
        "received_at": row.received_at.isoformat() if row.received_at else None,
        "classified_as": row.classified_as,
        "intent": row.intent,
        "raw_summary": row.raw_summary,
        "external_message_id": row.external_message_id,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
    }


def stop_followups_for_contact(db: Session, contact_id: str, reason: str) -> int:
    affected = stop_contact_send_work(db, contact_id, reason)
    return len(affected["followup_ids"])


def create_reply_record(
    db: Session,
    contact: Contact,
    classified_as: str,
    raw_summary: str | None = None,
    *,
    subject: str | None = None,
    external_message_id: str | None = None,
    stop_followups: bool = False,
    intent: str | None = None,
    received_at=None,
) -> tuple[Reply, bool]:
    if external_message_id:
        normalized_message_id = external_message_id.strip().lower()
        existing = (
            db.query(Reply)
            .filter(
                Reply.contact_id == contact.id,
                func.lower(func.trim(Reply.external_message_id)) == normalized_message_id,
            )
            .order_by(
                Reply.dedupe_key.is_not(None).desc(),
                Reply.created_at.asc(),
                Reply.id.asc(),
            )
            .first()
        )
    else:
        existing = (
            db.query(Reply)
            .filter(Reply.contact_id == contact.id, Reply.classified_as == classified_as, Reply.archived_at.is_(None))
            .first()
        )
    if existing:
        if existing.dedupe_key is None:
            existing.dedupe_key = _reply_dedupe_key(contact.id, external_message_id)
        if received_at is not None:
            existing.received_at = received_at
        refined_intent = _normalize_intent(intent)
        if refined_intent and refined_intent != existing.intent and _should_replace_intent(existing.intent, refined_intent):
            existing.intent = refined_intent
            _apply_intent_routing(db, contact, existing, refined_intent)
            emit_event(db, "reply.classified", entity_type="reply", entity_id=existing.id, payload={"classified_as": existing.classified_as, "intent": refined_intent})
        _refresh_reply_body(db, existing, raw_summary, subject)
        should_suppress = _should_suppress_reply(existing.classified_as, existing.intent, existing.raw_summary)
        suppression_exists = db.query(Suppression).filter_by(email=contact.email.strip().lower()).first()
        if should_suppress and suppression_exists is None:
            _ensure_suppression(
                db,
                contact,
                "unsubscribe"
                if existing.intent == "unsubscribe" or existing.classified_as == "unsubscribe"
                else "hostile_or_stop_request",
            )
            stop_contact_send_work(db, contact.id, "RECIPIENT_SUPPRESSED")
        return existing, False

    resolved_intent = _normalize_intent(intent) or _intent_from_classification(classified_as)
    if resolved_intent is None:
        resolved_intent = classify_intent(subject or "", raw_summary or "", db)

    row = Reply(
        contact_id=contact.id,
        received_at=received_at or utcnow(),
        classified_as=classified_as,
        intent=resolved_intent,
        raw_summary=raw_summary,
        external_message_id=external_message_id,
        dedupe_key=_reply_dedupe_key(contact.id, external_message_id),
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = (
            db.query(Reply)
            .filter(Reply.dedupe_key == _reply_dedupe_key(contact.id, external_message_id))
            .first()
        )
        if existing is None:
            raise
        _refresh_reply_body(db, existing, raw_summary, subject)
        return existing, False
    _add_inbound_conversation_message(db, contact, row, subject)
    db.flush()

    next_status = STATUS_BY_CLASSIFICATION.get(classified_as)
    if next_status:
        contact.status = next_status
        if stop_followups or classified_as in {"unsubscribe", "bounce"}:
            reason = STOP_REASON_BY_CLASSIFICATION[classified_as]
            stop_contact_send_work(db, contact.id, reason)
    _apply_intent_routing(db, contact, row, resolved_intent)
    if _should_suppress_reply(classified_as, resolved_intent, raw_summary):
        _ensure_suppression(db, contact, "unsubscribe" if resolved_intent == "unsubscribe" or classified_as == "unsubscribe" else "hostile_or_stop_request")
        stop_contact_send_work(db, contact.id, "RECIPIENT_SUPPRESSED")

    emit_event(db, "reply.received", entity_type="contact", entity_id=contact.id, payload={"classified_as": classified_as, "intent": resolved_intent})
    emit_event(db, "reply.classified", entity_type="reply", entity_id=row.id, payload={"classified_as": classified_as, "intent": resolved_intent})
    return row, True


def classify_intent(subject: str, snippet: str, db: Session) -> str:
    key = GroqKeyPool(get_key_list(db, "groq_keys")).acquire()
    if not key:
        return "unknown"
    prompt = (
        "Classify this email reply intent as exactly one word:\n"
        "positive_interest - they are open or interested\n"
        "objection - they have concerns or doubts\n"
        "question - they asked a specific question\n"
        "negative_no - clear refusal or not interested\n"
        "unsubscribe - wants to be removed\n"
        "auto_reply - out of office or automated\n"
        "unknown - unclear\n"
        f"Subject: {(subject or '')[:150]}\n"
        f"Snippet: {(snippet or '')[:150]}\n"
        "Reply with only the classification word."
    )
    try:
        raw = _call_groq_intent(db, key, prompt).strip().lower()
    except Exception as exc:
        emit_event(db, "provider.health_changed", entity_type="provider", entity_id="groq", payload={"provider": "groq", "status": "degraded", "error_code": exc.__class__.__name__})
        return "unknown"
    return _refine_intent(_normalize_intent(raw) or "unknown", subject, snippet)


def _call_groq_intent(db: Session, api_key: str, prompt: str) -> str:
    from groq import Groq

    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=get_value(db, "groq_model", GROQ_MODEL_DEFAULT),
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=10,
        timeout=20,
    )
    return response.choices[0].message.content or ""


def _normalize_intent(value: str | None) -> str | None:
    raw = (value or "").strip().lower()
    if raw in REPLY_INTENTS:
        return raw
    for item in REPLY_INTENTS:
        if raw.startswith(item):
            return item
    return None


def _refine_intent(intent: str, subject: str, snippet: str) -> str:
    subject_text = (subject or "").lower()
    body_text = (snippet or "").lower()
    text = f"{subject_text} {body_text}"
    actual_auto_cue = any(cue in body_text for cue in ("out of office", "automatic reply", "vacation responder", "auto-generated"))
    if intent == "auto_reply" and not actual_auto_cue and not subject_text.startswith(("automatic reply", "out of office")):
        intent = "unknown"
    if _contains_unsubscribe_cue(text):
        return "unsubscribe"
    if any(cue in text for cue in ("stop spamming", "report spam", "file a complaint", "do not contact", "don't contact")):
        return "negative_no"
    objection_cues = (
        "just chatgpt",
        "different name",
        "what makes",
        "already use",
        "why should i",
        "why should we",
        "too expensive",
        "can't afford",
        "cannot afford",
        "concern",
        "doubt",
        "skeptical",
    )
    if intent in {"question", "unknown"} and any(cue in text for cue in objection_cues):
        return "objection"
    positive_cues = ("interested", "sounds interesting", "tell me more", "useful", "open to", "next steps")
    if intent == "unknown" and any(cue in body_text for cue in positive_cues):
        return "positive_interest"
    if intent == "unknown" and "?" in body_text:
        return "question"
    return intent


def _should_replace_intent(existing: str | None, incoming: str) -> bool:
    if existing in {None, "unknown"}:
        return True
    if existing == "question" and incoming in {"objection", "positive_interest", "negative_no", "unsubscribe"}:
        return True
    return False


def _intent_from_classification(classified_as: str) -> str | None:
    if classified_as == "unsubscribe":
        return "unsubscribe"
    if classified_as == "bounce":
        return "bounce"
    if classified_as == "auto_reply":
        return "auto_reply"
    return None


def _apply_intent_routing(db: Session, contact: Contact, row: Reply, intent: str) -> None:
    if intent in {"positive_interest", "objection", "question"}:
        contact.status = "conversation_active"
        emit_event(db, "reply.escalated", entity_type="contact", entity_id=contact.id, payload={"intent": intent, "contact_id": contact.id})
    elif intent == "negative_no":
        contact.status = "follow_up_stopped"
        stop_contact_send_work(db, contact.id, "RECIPIENT_NEGATIVE_NO")


def _contains_unsubscribe_cue(text: str) -> bool:
    return any(
        cue in text
        for cue in (
            "unsubscribe",
            "remove me",
            "remove from your mailing list",
            "do not email",
            "don't email",
            "do not contact me again",
            "don't contact me again",
            "stop emailing",
            "stop sending",
        )
    )


def _should_suppress_reply(classified_as: str, intent: str | None, raw_summary: str | None) -> bool:
    text = (raw_summary or "").lower()
    if classified_as == "unsubscribe" or intent == "unsubscribe":
        return True
    if intent == "negative_no" and any(cue in text for cue in ("stop", "do not contact", "don't contact", "spam", "complaint", "report")):
        return True
    return False


def _ensure_suppression(db: Session, contact: Contact, reason: str) -> None:
    email = contact.email.strip().lower()
    row = db.query(Suppression).filter_by(email=email).first()
    if row is None:
        row = Suppression(email=email, reason=reason, source="reply")
        db.add(row)
        db.flush()
        emit_event(db, "suppression.added", entity_type="suppression", entity_id=row.id, payload={"email": email, "reason": reason})


def _add_inbound_conversation_message(db: Session, contact: Contact, row: Reply, subject: str | None) -> None:
    external_message_id = row.external_message_id or f"reply:{row.id}"
    existing = (
        db.query(ConversationMessage)
        .filter(
            ConversationMessage.contact_id == contact.id,
            ConversationMessage.external_message_id == external_message_id,
        )
        .first()
    )
    if existing:
        return
    body = row.raw_summary or f"{row.classified_as} received"
    db.add(
        ConversationMessage(
            contact_id=contact.id,
            direction="inbound",
            subject=subject,
            body=body,
            source="imap" if row.external_message_id else "manual",
            external_message_id=external_message_id,
            occurred_at=row.received_at,
        )
    )


def _refresh_reply_body(db: Session, row: Reply, raw_summary: str | None, subject: str | None) -> None:
    if not raw_summary or len(raw_summary) <= len(row.raw_summary or ""):
        return
    row.raw_summary = raw_summary
    external_message_id = row.external_message_id or f"reply:{row.id}"
    message = db.query(ConversationMessage).filter(ConversationMessage.external_message_id == external_message_id).first()
    if not message:
        return
    message.body = raw_summary
    if subject and not message.subject:
        message.subject = subject


def refresh_contact_status_after_reply_change(db: Session, contact: Contact) -> None:
    active = (
        db.query(Reply)
        .filter(
            Reply.contact_id == contact.id,
            Reply.archived_at.is_(None),
            Reply.classified_as.in_(list(STATUS_BY_CLASSIFICATION.keys())),
        )
        .order_by(Reply.received_at.desc())
        .first()
    )
    if active:
        contact.status = STATUS_BY_CLASSIFICATION[active.classified_as]
        return

    if contact.status not in {*STATUS_BY_CLASSIFICATION.values(), "suppressed"}:
        return
    accepted_attempts = (
        db.query(SendAttempt)
        .filter(SendAttempt.contact_id == contact.id, SendAttempt.provider_accepted.is_(True))
        .all()
    )
    if any(provider_acceptance_evidence_present(attempt) for attempt in accepted_attempts):
        contact.status = "sent"
    elif db.query(Draft).filter(Draft.contact_id == contact.id).first():
        contact.status = "draft_ready"
    else:
        contact.status = "imported"
