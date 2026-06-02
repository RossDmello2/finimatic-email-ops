from __future__ import annotations

import json
from datetime import timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.contacts.utils import next_send_window_open_at, resolve_tokens
from app.core.idempotency import sha256_key
from app.core.time import utcnow
from app.db.models import Contact, ConversationMessage, Draft, FollowUpSequence, SendAttempt, SendQueue
from app.send.policy import evaluate_policy, store_policy_result
from app.send.smtp_adapter import GmailAdapter
from app.settings.service import get_bool, get_int, get_secret, get_value

TEMPORARY_BLOCK_REASONS = {"SEND_WINDOW_NOT_ELAPSED"}


def queue_to_dict(entry: SendQueue) -> dict:
    contact = entry.contact
    draft = entry.draft
    return {
        "id": entry.id,
        "contact_id": entry.contact_id,
        "contact_email": contact.email if contact else None,
        "contact_name": (contact.creator_name or contact.business_name) if contact else None,
        "draft_id": entry.draft_id,
        "draft_subject": draft.subject if draft else None,
        "sequence_num": entry.sequence_num,
        "scheduled_at": entry.scheduled_at.isoformat() if entry.scheduled_at else None,
        "status": entry.status,
        "idempotency_key": entry.idempotency_key,
        "policy_block_reasons": json.loads(entry.policy_block_reasons or "[]"),
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def create_queue_entry(db: Session, contact_id: str, draft_id: str, sequence_num: int = 1) -> SendQueue:
    key = sha256_key(contact_id, sequence_num, draft_id)
    existing = db.query(SendQueue).filter_by(idempotency_key=key).first()
    if existing:
        return existing
    existing_sequence = db.query(SendQueue).filter_by(contact_id=contact_id, sequence_num=sequence_num).first()
    if existing_sequence:
        return existing_sequence
    scheduled_at = utcnow() + timedelta(seconds=get_int(db, "send_delay_s"))
    if get_int(db, "send_delay_s") == 0:
        scheduled_at = utcnow()
    entry = SendQueue(
        contact_id=contact_id,
        draft_id=draft_id,
        sequence_num=sequence_num,
        scheduled_at=scheduled_at,
        idempotency_key=key,
        status="pending",
    )
    db.add(entry)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return (
            db.query(SendQueue).filter_by(idempotency_key=key).first()
            or db.query(SendQueue).filter_by(contact_id=contact_id, sequence_num=sequence_num).one()
        )
    emit_event(db, "queue.entry_created", entity_type="send_queue", entity_id=entry.id)
    return entry


def _temporary_block_only(reasons: list[str]) -> bool:
    return bool(reasons) and set(reasons).issubset(TEMPORARY_BLOCK_REASONS)


def _aware_utc(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _next_retry_at(db: Session, now) -> object:
    retry_at = now + timedelta(seconds=5)
    window_at = next_send_window_open_at(db, now)
    if window_at > retry_at:
        retry_at = window_at

    send_delay_s = get_int(db, "send_delay_s")
    if send_delay_s > 0:
        last_success = (
            db.query(SendAttempt)
            .filter(SendAttempt.status == "success", SendAttempt.sent_at.is_not(None))
            .order_by(SendAttempt.sent_at.desc())
            .first()
        )
        last_sent_at = _aware_utc(last_success.sent_at) if last_success else None
        if last_sent_at:
            delay_at = last_sent_at + timedelta(seconds=send_delay_s)
            if delay_at > retry_at:
                retry_at = delay_at
    return retry_at


async def process_pending_queue(db: Session, transport=None) -> dict:
    now = utcnow()
    entries = (
        db.query(SendQueue)
        .filter(SendQueue.status.in_(["pending", "skipped"]), SendQueue.scheduled_at <= now)
        .order_by(SendQueue.created_at.asc())
        .all()
    )
    processed = 0
    sent = 0
    blocked = 0
    skipped = 0
    deferred = 0
    adapter = GmailAdapter.from_settings(db, transport=transport)

    for entry in entries:
        claimed = (
            db.query(SendQueue)
            .filter(SendQueue.id == entry.id, SendQueue.status.in_(["pending", "skipped"]))
            .update({"status": "processing"}, synchronize_session=False)
        )
        db.commit()
        if claimed != 1:
            continue
        db.expire_all()
        entry = db.get(SendQueue, entry.id)
        if entry is None:
            continue
        processed += 1
        decision = evaluate_policy(entry, db)
        store_policy_result(entry, decision)
        emit_event(db, "queue.policy_evaluated", entity_type="send_queue", entity_id=entry.id, payload={"reasons": decision.block_reason_codes})
        if not decision.all_passed:
            if _temporary_block_only(decision.block_reason_codes):
                entry.status = "pending"
                entry.scheduled_at = _next_retry_at(db, now)
                emit_event(
                    db,
                    "queue.temporarily_deferred",
                    entity_type="send_queue",
                    entity_id=entry.id,
                    payload={"reasons": decision.block_reason_codes, "retry_at": entry.scheduled_at.isoformat()},
                )
                deferred += 1
                db.commit()
                continue
            entry.status = "blocked"
            entry.contact.status = "blocked_by_policy"
            emit_event(db, "queue.gate_blocked", entity_type="send_queue", entity_id=entry.id, payload={"reasons": decision.block_reason_codes})
            blocked += 1
            db.commit()
            continue

        if get_bool(db, "dry_run"):
            entry.status = "skipped"
            db.add(
                SendAttempt(
                    queue_id=entry.id,
                    contact_id=entry.contact_id,
                    draft_id=entry.draft_id,
                    idempotency_key=entry.idempotency_key,
                    status="blocked_dry_run",
                    sender_identity=get_value(db, "gmail_user"),
                    error_code="dry_run",
                    error_detail="Dry run mode prevented SMTP send",
                )
            )
            emit_event(db, "send.dry_run_blocked", entity_type="send_queue", entity_id=entry.id)
            skipped += 1
            db.commit()
            continue

        user = get_value(db, "gmail_user")
        password = get_secret(db, "gmail_app_password")
        draft = db.get(Draft, entry.draft_id)
        contact = db.get(Contact, entry.contact_id)
        emit_event(db, "send.attempt", entity_type="send_queue", entity_id=entry.id)
        subject = resolve_tokens(draft.subject, contact)
        body = resolve_tokens(draft.body, contact)
        result = await adapter.send_message(contact.email, subject, body, user, password)
        if result.status == "success":
            entry.status = "sent"
            contact.status = "sent"
            sent_at = utcnow()
            db.add(
                SendAttempt(
                    queue_id=entry.id,
                    contact_id=entry.contact_id,
                    draft_id=entry.draft_id,
                    idempotency_key=entry.idempotency_key,
                    provider_msg_id=result.provider_msg_id,
                    smtp_response=result.smtp_response,
                    status="success",
                    sender_identity=user,
                    sent_at=sent_at,
                )
            )
            db.add(
                ConversationMessage(
                    contact_id=contact.id,
                    direction="outbound",
                    subject=subject,
                    body=body,
                    source="queue",
                    external_message_id=result.provider_msg_id,
                    occurred_at=sent_at,
                )
            )
            emit_event(db, "send.success", entity_type="send_queue", entity_id=entry.id)
            _schedule_followup(db, contact.id, draft.id, sent_at, entry.sequence_num + 1)
            sent += 1
        else:
            entry.status = "failed"
            db.add(
                SendAttempt(
                    queue_id=entry.id,
                    contact_id=entry.contact_id,
                    draft_id=entry.draft_id,
                    idempotency_key=entry.idempotency_key,
                    status="failed",
                    sender_identity=user,
                    error_code=result.error_code,
                    error_detail=result.error_detail,
                )
            )
            emit_event(db, "send.failed", entity_type="send_queue", entity_id=entry.id, payload={"error_code": result.error_code})
        db.commit()
    return {"processed": processed, "sent": sent, "blocked": blocked, "skipped": skipped, "deferred": deferred}


def _schedule_followup(db: Session, contact_id: str, draft_id: str, sent_at, sequence_num: int = 2) -> None:
    max_followups = get_int(db, "max_followups_per_lead")
    if max_followups <= 0:
        return
    followup_index = sequence_num - 1
    if followup_index > max_followups:
        return
    existing = db.query(FollowUpSequence).filter_by(contact_id=contact_id, sequence_num=sequence_num).first()
    if existing:
        return
    due_at = sent_at + timedelta(days=get_int(db, "followup_interval_days"))
    sequence = FollowUpSequence(contact_id=contact_id, draft_id=draft_id, sequence_num=sequence_num, due_at=due_at, status="due")
    db.add(sequence)
    emit_event(db, "followup.due_calculated", entity_type="contact", entity_id=contact_id, payload={"sequence_num": sequence_num})
