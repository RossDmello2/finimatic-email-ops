from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.contacts.utils import next_send_window_open_at, resolve_tokens
from app.core.crypto import redacted_error
from app.core.idempotency import sha256_key
from app.core.time import utcnow
from app.db.models import Contact, ConversationMessage, Draft, FollowUpSequence, SendAttempt, SendQueue
from app.send.policy import evaluate_policy, policy_trace_to_dict, store_policy_result
from app.send.outcomes import simulated_outcome, validate_send_outcome
from app.send.governance import begin_provider_attempt, provider_attempt_stop_fence_changed
from app.send.sequence import (
    provider_acceptance_evidence_present,
    schedule_next_sequence_after_acceptance,
    sequence_prerequisite_met,
)
from app.send.smtp_adapter import GmailAdapter
from app.send.truth import apply_outcome_to_attempt, attempt_from_outcome, outcome_audit_payload
from app.settings.service import get_bool, get_int, get_secret, get_value

TEMPORARY_BLOCK_REASONS = {"CANARY_NOT_VERIFIED", "SEND_WINDOW_NOT_ELAPSED"}
STALE_ATTEMPT_AFTER = timedelta(minutes=5)
POLICY_DEFERRED_SCHEDULE_SOURCE = "policy_deferral"
POLICY_RELEASED_SCHEDULE_SOURCE = "policy_released"
APPROVAL_DELAY_SCHEDULE_SOURCE = "approval_delay"
EXPLICIT_SEND_NOW_SCHEDULE_SOURCE = "explicit_send_now"
OPERATOR_CLEARED_QUEUE_REASON = "OPERATOR_CLEARED_QUEUE"


def _empty_latest_attempt_fields() -> dict:
    return {"last_attempt_status": None, "last_attempt_error_code": None, "last_attempt_error_detail": None}


def _attempt_to_dict(attempt: SendAttempt | None) -> dict | None:
    if not attempt:
        return None
    return {
        "attempt_id": attempt.id,
        "queue_id": attempt.queue_id,
        "contact_id": attempt.contact_id,
        "draft_id": attempt.draft_id,
        "attempt_status": attempt.status,
        "configured_transport": attempt.configured_transport,
        "effective_transport": attempt.effective_transport,
        "transport_source": attempt.transport_source,
        "simulated": attempt.simulated,
        "provider_contacted": attempt.provider_contacted,
        "provider_accepted": attempt.provider_accepted,
        "provider_message_id": attempt.provider_msg_id,
        "tracking_message_id": attempt.tracking_message_id,
        "provider_response_classification": attempt.provider_response_classification,
        "error_code": attempt.error_code,
        "error_detail_redacted": attempt.error_detail,
        "idempotency_key": attempt.idempotency_key,
    }


def _latest_attempt_dict(db: Session, entry_id: str) -> dict | None:
    attempt = (
        db.query(SendAttempt)
        .filter(SendAttempt.queue_id == entry_id)
        .order_by(SendAttempt.created_at.desc().nullslast(), SendAttempt.id.desc())
        .first()
    )
    return _attempt_to_dict(attempt)


def _latest_attempt(db: Session, entry: SendQueue) -> SendAttempt | None:
    return (
        db.query(SendAttempt)
        .filter(SendAttempt.queue_id == entry.id)
        .order_by(SendAttempt.created_at.desc().nullslast(), SendAttempt.id.desc())
        .first()
    )


def _current_attempt_is_safe_to_retry(attempt: SendAttempt | None) -> bool:
    if attempt is None:
        return True
    if attempt.provider_accepted is True or attempt.status == "reconciliation_required":
        return False
    return attempt.provider_contacted is False


def latest_attempts_by_queue(db: Session, entry_ids: list[str]) -> dict[str, dict]:
    if not entry_ids:
        return {}
    attempts = (
        db.query(SendAttempt)
        .filter(SendAttempt.queue_id.in_(entry_ids))
        .order_by(SendAttempt.queue_id.asc(), SendAttempt.created_at.desc().nullslast(), SendAttempt.id.desc())
        .all()
    )
    latest: dict[str, dict] = {}
    for attempt in attempts:
        if attempt.queue_id not in latest:
            latest_attempt = _attempt_to_dict(attempt)
            if latest_attempt is not None:
                latest[attempt.queue_id] = latest_attempt
    return latest


def queue_to_dict(entry: SendQueue, db: Session | None = None, latest_attempt: dict | None = None) -> dict:
    contact = entry.contact
    draft = entry.draft
    trace = []
    if db is not None and entry.status in {"pending", "processing", "blocked", "skipped", "failed"}:
        trace = policy_trace_to_dict(evaluate_policy(entry, db))
    if db is not None and latest_attempt is None:
        latest_attempt = _latest_attempt_dict(db, entry.id)
    latest_attempt = latest_attempt or None
    if latest_attempt and (
        latest_attempt.get("contact_id") != entry.contact_id
        or latest_attempt.get("draft_id") != entry.draft_id
    ):
        latest_attempt = None
    stored_status = entry.status
    status = stored_status
    classification_note = None
    if stored_status in {"sent", "provider_accepted"} and not (
        latest_attempt and latest_attempt["provider_accepted"] is True
    ):
        status = "reconciliation_required"
        classification_note = "Historical success lacks durable provider acceptance evidence."
    payload = {
        "id": entry.id,
        "contact_id": entry.contact_id,
        "contact_email": contact.email if contact else None,
        "contact_name": (contact.creator_name or contact.business_name) if contact else None,
        "draft_id": entry.draft_id,
        "draft_subject": draft.subject if draft else None,
        "sequence_num": entry.sequence_num,
        "scheduled_at": _aware_utc(entry.scheduled_at).isoformat() if entry.scheduled_at else None,
        "schedule_source": entry.schedule_source,
        "status": status,
        "stored_status": stored_status if stored_status != status else None,
        "classification_note": classification_note,
        "idempotency_key": entry.idempotency_key,
        "policy_block_reasons": json.loads(entry.policy_block_reasons or "[]"),
        "policy_trace": trace,
        "latest_attempt": latest_attempt,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }
    if latest_attempt:
        payload.update(
            {
                "last_attempt_status": latest_attempt["attempt_status"],
                "last_attempt_error_code": latest_attempt["error_code"],
                "last_attempt_error_detail": latest_attempt["error_detail_redacted"],
            }
        )
    else:
        payload.update(_empty_latest_attempt_fields())
    return payload


def queue_list_to_dict(entries: list[SendQueue], db: Session) -> list[dict]:
    latest = latest_attempts_by_queue(db, [entry.id for entry in entries])
    return [queue_to_dict(entry, db, latest_attempt=latest.get(entry.id, {})) for entry in entries]


def create_queue_entry(
    db: Session,
    contact_id: str,
    draft_id: str,
    sequence_num: int = 1,
    *,
    schedule_source: str = APPROVAL_DELAY_SCHEDULE_SOURCE,
) -> SendQueue:
    if not sequence_prerequisite_met(db, contact_id, sequence_num):
        raise ValueError("prior_sequence_not_provider_accepted")
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
        schedule_source=schedule_source,
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


def _next_retry_at(db: Session, now, reasons: list[str] | None = None) -> object:
    retry_delay = 60 if reasons and "CANARY_NOT_VERIFIED" in reasons else 5
    retry_at = now + timedelta(seconds=retry_delay)
    window_at = next_send_window_open_at(db, now)
    if window_at > retry_at:
        retry_at = window_at

    send_delay_s = get_int(db, "send_delay_s")
    if send_delay_s > 0:
        last_success = (
            db.query(SendAttempt)
            .filter(SendAttempt.provider_accepted.is_(True), SendAttempt.sent_at.is_not(None))
            .order_by(SendAttempt.sent_at.desc())
            .first()
        )
        last_sent_at = _aware_utc(last_success.sent_at) if last_success else None
        if last_sent_at:
            delay_at = last_sent_at + timedelta(seconds=send_delay_s)
            if delay_at > retry_at:
                retry_at = delay_at
    return retry_at


def _queue_has_temporary_policy_reasons(entry: SendQueue) -> bool:
    try:
        reasons = json.loads(entry.policy_block_reasons or "[]")
    except json.JSONDecodeError:
        return False
    return _temporary_block_only(reasons)


def _scheduler_effective(db: Session) -> bool:
    return (
        get_bool(db, "auto_process_enabled")
        and os.getenv("FINIMATIC_DISABLE_SCHEDULER") != "1"
        and os.getenv("FINIMATIC_DISABLE_AUTO_PROCESS") != "1"
    )


def _future_queue_summary(db: Session, now, queue_ids: list[str] | None = None) -> dict:
    eligible_statuses = ["pending", "skipped"]
    if not get_bool(db, "dry_run"):
        eligible_statuses.append("simulated")
    query = db.query(SendQueue).filter(
        SendQueue.status.in_(eligible_statuses),
        SendQueue.scheduled_at > now,
    )
    if queue_ids is not None:
        query = query.filter(SendQueue.id.in_(queue_ids))
    rows = query.order_by(SendQueue.scheduled_at.asc()).all()
    reasons: dict[str, int] = {}
    for row in rows:
        try:
            row_reasons = json.loads(row.policy_block_reasons or "[]")
        except json.JSONDecodeError:
            row_reasons = []
        for reason in row_reasons:
            reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "future_scheduled_count": len(rows),
        "next_due_at": _aware_utc(rows[0].scheduled_at).isoformat() if rows else None,
        "blocked_reasons": reasons,
    }


def reschedule_policy_deferred_queue_entries(
    db: Session,
    *,
    changed_keys: set[str] | None = None,
) -> int:
    now = utcnow()
    sources = {POLICY_DEFERRED_SCHEDULE_SOURCE}
    if changed_keys and "send_delay_s" in changed_keys:
        sources.add(APPROVAL_DELAY_SCHEDULE_SOURCE)
    entries = (
        db.query(SendQueue)
        .filter(
            SendQueue.status == "pending",
            SendQueue.schedule_source.in_(sources),
        )
        .all()
    )
    changed = 0
    for entry in entries:
        if not _current_attempt_is_safe_to_retry(_latest_attempt(db, entry)):
            continue
        decision = evaluate_policy(entry, db)
        previous_scheduled_at = entry.scheduled_at
        previous_source = entry.schedule_source
        store_policy_result(entry, decision)
        if decision.all_passed:
            entry.scheduled_at = now
            entry.schedule_source = POLICY_RELEASED_SCHEDULE_SOURCE
        elif _temporary_block_only(decision.block_reason_codes):
            entry.scheduled_at = _next_retry_at(db, now, decision.block_reason_codes)
            entry.schedule_source = POLICY_DEFERRED_SCHEDULE_SOURCE
        else:
            entry.scheduled_at = now
            entry.schedule_source = POLICY_RELEASED_SCHEDULE_SOURCE
        if previous_scheduled_at != entry.scheduled_at or previous_source != entry.schedule_source:
            emit_event(
                db,
                "queue.schedule_recalculated",
                entity_type="send_queue",
                entity_id=entry.id,
                payload={
                    "previous_source": previous_source,
                    "schedule_source": entry.schedule_source,
                    "previous_scheduled_at": _aware_utc(previous_scheduled_at).isoformat()
                    if previous_scheduled_at
                    else None,
                    "scheduled_at": _aware_utc(entry.scheduled_at).isoformat() if entry.scheduled_at else None,
                    "reasons": decision.block_reason_codes,
                    "changed_keys": sorted(changed_keys or []),
                },
            )
            changed += 1
    if changed:
        db.commit()
    return changed


def release_recoverable_queue_entries(db: Session) -> int:
    now = utcnow()
    released = 0
    entries = db.query(SendQueue).filter(SendQueue.status.in_(["blocked", "pending"])).all()
    for entry in entries:
        if not _queue_has_temporary_policy_reasons(entry):
            continue
        stored_reasons = json.loads(entry.policy_block_reasons or "[]")
        if not _current_attempt_is_safe_to_retry(_latest_attempt(db, entry)):
            continue
        if entry.contact is None or db.get(Draft, entry.draft_id) is None:
            continue
        decision = evaluate_policy(entry, db)
        store_policy_result(entry, decision)
        entry.status = "pending"
        if decision.all_passed:
            entry.scheduled_at = now
            entry.schedule_source = POLICY_RELEASED_SCHEDULE_SOURCE
        elif _temporary_block_only(decision.block_reason_codes):
            entry.scheduled_at = _next_retry_at(db, now, decision.block_reason_codes)
            entry.schedule_source = POLICY_DEFERRED_SCHEDULE_SOURCE
        else:
            entry.scheduled_at = now
            entry.schedule_source = POLICY_RELEASED_SCHEDULE_SOURCE
        entry.processing_started_at = None
        entry.processing_token = None
        if entry.contact and entry.contact.status == "blocked_by_policy":
            entry.contact.status = "approved"
        _update_followup_status(db, entry, "queued")
        emit_event(
            db,
            "queue.prerequisite_released",
            entity_type="send_queue",
            entity_id=entry.id,
            payload={
                "previous_reasons": stored_reasons,
                "remaining_reasons": decision.block_reason_codes,
                "retry_at": entry.scheduled_at.isoformat(),
            },
        )
        released += 1
    return released


async def process_pending_queue(db: Session, transport=None, queue_ids: list[str] | None = None) -> dict:
    now = utcnow()
    _reconcile_stale_processing(db, now)
    rescheduled = reschedule_policy_deferred_queue_entries(db)
    now = utcnow()
    dry_run_enabled = get_bool(db, "dry_run")
    eligible_statuses = ["pending", "skipped"]
    if not dry_run_enabled:
        eligible_statuses.append("simulated")
    query = db.query(SendQueue).filter(
        SendQueue.status.in_(eligible_statuses),
        SendQueue.scheduled_at <= now,
    )
    if queue_ids is not None:
        query = query.filter(SendQueue.id.in_(queue_ids))
    eligible_count = query.count()
    future_summary = _future_queue_summary(db, now, queue_ids=queue_ids)
    entries = query.order_by(SendQueue.created_at.asc()).all()
    processed = 0
    provider_accepted = 0
    blocked = 0
    simulated = 0
    deferred = 0
    failed = 0
    reconciliation_required = 0
    adapter = GmailAdapter.from_settings(db, transport=transport)

    for entry in entries:
        claim_time = utcnow()
        claim_token = uuid.uuid4().hex
        claimed = (
            db.query(SendQueue)
            .filter(
                SendQueue.id == entry.id,
                SendQueue.status.in_(eligible_statuses),
                SendQueue.scheduled_at <= claim_time,
            )
            .update(
                {
                    "status": "processing",
                    "processing_started_at": claim_time,
                    "processing_token": claim_token,
                },
                synchronize_session=False,
            )
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
                entry.scheduled_at = _next_retry_at(db, now, decision.block_reason_codes)
                entry.schedule_source = POLICY_DEFERRED_SCHEDULE_SOURCE
                entry.processing_started_at = None
                entry.processing_token = None
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
            entry.processing_started_at = None
            entry.processing_token = None
            entry.contact.status = "blocked_by_policy"
            _update_followup_status(db, entry, "blocked", ",".join(decision.block_reason_codes))
            emit_event(db, "queue.gate_blocked", entity_type="send_queue", entity_id=entry.id, payload={"reasons": decision.block_reason_codes})
            blocked += 1
            db.commit()
            continue

        user = get_value(db, "gmail_user")
        draft = db.get(Draft, entry.draft_id)
        contact = db.get(Contact, entry.contact_id)
        subject = resolve_tokens(draft.subject, contact)
        body = resolve_tokens(draft.body, contact)

        prior_acceptance_candidates = (
            db.query(SendAttempt)
            .filter(
                SendAttempt.idempotency_key == entry.idempotency_key,
                SendAttempt.status == "provider_accepted",
                SendAttempt.provider_accepted.is_(True),
            )
            .order_by(SendAttempt.created_at.desc(), SendAttempt.id.desc())
            .all()
        )
        valid_prior_acceptances = [
            attempt
            for attempt in prior_acceptance_candidates
            if attempt.queue_id == entry.id
            and attempt.contact_id == entry.contact_id
            and attempt.draft_id == entry.draft_id
            and provider_acceptance_evidence_present(attempt)
        ]
        prior_acceptance = valid_prior_acceptances[0] if valid_prior_acceptances else None
        if prior_acceptance_candidates and len(valid_prior_acceptances) != len(prior_acceptance_candidates):
            entry.status = "reconciliation_required"
            entry.processing_started_at = None
            entry.processing_token = None
            _update_followup_status(
                db,
                entry,
                "reconciliation_required",
                "INVALID_PROVIDER_ACCEPTANCE_EVIDENCE",
            )
            emit_event(
                db,
                "send.reconciliation_required",
                entity_type="send_queue",
                entity_id=entry.id,
                payload={
                    "attempt_status": "reconciliation_required",
                    "error_code": "invalid_provider_acceptance_evidence",
                    "attempt_ids": [attempt.id for attempt in prior_acceptance_candidates],
                },
            )
            reconciliation_required += 1
            db.commit()
            continue
        if prior_acceptance:
            entry.status = "provider_accepted"
            entry.processing_started_at = None
            entry.processing_token = None
            _update_followup_status(db, entry, "provider_accepted")
            handled, _ = schedule_next_sequence_after_acceptance(db, entry, prior_acceptance)
            if not handled:
                _schedule_followup(
                    db,
                    entry.contact_id,
                    entry.draft_id,
                    prior_acceptance.sent_at or now,
                    entry.sequence_num + 1,
                )
            emit_event(
                db,
                "send.duplicate_blocked",
                entity_type="send_queue",
                entity_id=entry.id,
                payload={"attempt_id": prior_acceptance.id},
            )
            db.commit()
            continue

        if dry_run_enabled:
            outcome = simulated_outcome(
                adapter.resolution,
                error_code="dry_run",
                detail="Dry-run mode simulated the send; no provider was contacted",
            ).with_context(idempotency_key=entry.idempotency_key)
            attempt = attempt_from_outcome(
                outcome,
                queue_id=entry.id,
                contact_id=entry.contact_id,
                draft_id=entry.draft_id,
                idempotency_key=entry.idempotency_key,
                sender_identity=user,
            )
            db.add(attempt)
            db.flush()
            outcome = outcome.with_context(attempt_id=attempt.id)
            entry.status = "simulated"
            entry.processing_started_at = None
            entry.processing_token = None
            _update_followup_status(db, entry, "simulated", "DRY_RUN_ENABLED")
            emit_event(
                db,
                "send.simulated",
                entity_type="send_queue",
                entity_id=entry.id,
                payload=outcome_audit_payload(outcome),
            )
            simulated += 1
            db.commit()
            continue

        if adapter.resolution.simulated:
            outcome = simulated_outcome(
                adapter.resolution,
                error_code="effective_transport_simulated",
                detail="Live mode was blocked because the effective transport is simulated",
                blocked=True,
            ).with_context(idempotency_key=entry.idempotency_key)
            attempt = attempt_from_outcome(
                outcome,
                queue_id=entry.id,
                contact_id=entry.contact_id,
                draft_id=entry.draft_id,
                idempotency_key=entry.idempotency_key,
                sender_identity=user,
            )
            db.add(attempt)
            db.flush()
            outcome = outcome.with_context(attempt_id=attempt.id)
            entry.status = "blocked"
            entry.processing_started_at = None
            entry.processing_token = None
            _update_followup_status(db, entry, "blocked", "EFFECTIVE_TRANSPORT_SIMULATED")
            emit_event(
                db,
                "send.transport_blocked",
                entity_type="send_queue",
                entity_id=entry.id,
                payload=outcome_audit_payload(outcome),
            )
            blocked += 1
            db.commit()
            continue

        attempt, dispatch_state = begin_provider_attempt(
            db,
            queue_id=entry.id,
            contact_id=entry.contact_id,
            draft_id=entry.draft_id,
            idempotency_key=entry.idempotency_key,
            sender_identity=user,
            configured_transport=adapter.resolution.configured_transport,
            effective_transport=adapter.resolution.effective_transport,
            transport_source=adapter.resolution.transport_source,
            entity_type="send_queue",
            entity_id=entry.id,
            audit_source="queue",
        )
        if dispatch_state != "ready":
            db.expire_all()
            entry = db.get(SendQueue, entry.id)
            if entry is not None and entry.processing_token == claim_token:
                entry.status = "reconciliation_required"
                entry.processing_started_at = None
                entry.processing_token = None
                _update_followup_status(
                    db,
                    entry,
                    "reconciliation_required",
                    "DUPLICATE_DISPATCH_PERMIT",
                )
                emit_event(
                    db,
                    "send.reconciliation_required",
                    entity_type="send_queue",
                    entity_id=entry.id,
                    payload={
                        "attempt_id": attempt.id,
                        "attempt_status": "reconciliation_required",
                        "error_code": "duplicate_dispatch_permit",
                        "dispatch_state": dispatch_state,
                    },
                )
                db.commit()
            reconciliation_required += 1
            continue

        db.expire_all()
        entry = db.get(SendQueue, entry.id)
        attempt = db.get(SendAttempt, attempt.id)
        if (
            entry is None
            or entry.status != "processing"
            or entry.processing_token != claim_token
        ):
            outcome = simulated_outcome(
                adapter.resolution,
                error_code="claim_revoked_before_provider_call",
                detail="Queue claim was revoked before provider execution",
                blocked=True,
            ).with_context(idempotency_key=attempt.idempotency_key, attempt_id=attempt.id)
            apply_outcome_to_attempt(attempt, outcome)
            emit_event(
                db,
                "send.pre_provider_blocked",
                entity_type="send_queue",
                entity_id=attempt.queue_id,
                payload=outcome_audit_payload(outcome),
            )
            blocked += 1
            db.commit()
            continue
        decision = evaluate_policy(entry, db)
        store_policy_result(entry, decision)
        if not decision.all_passed:
            outcome = simulated_outcome(
                adapter.resolution,
                error_code="policy_changed_before_provider_call",
                detail="Policy changed after claim; provider execution was blocked",
                blocked=True,
            ).with_context(idempotency_key=entry.idempotency_key, attempt_id=attempt.id)
            apply_outcome_to_attempt(attempt, outcome)
            entry.status = "blocked"
            entry.processing_started_at = None
            entry.processing_token = None
            _update_followup_status(db, entry, "blocked", ",".join(decision.block_reason_codes))
            emit_event(
                db,
                "send.pre_provider_blocked",
                entity_type="send_queue",
                entity_id=entry.id,
                payload={**outcome_audit_payload(outcome), "reasons": decision.block_reason_codes},
            )
            blocked += 1
            db.commit()
            continue

        password = get_secret(db, "gmail_app_password")
        db.expire_all()
        entry = db.get(SendQueue, entry.id)
        attempt = db.get(SendAttempt, attempt.id)
        if (
            entry is None
            or entry.status != "processing"
            or entry.processing_token != claim_token
        ):
            outcome = simulated_outcome(
                adapter.resolution,
                error_code="claim_revoked_before_provider_call",
                detail="Queue claim was revoked immediately before provider execution",
                blocked=True,
            ).with_context(idempotency_key=attempt.idempotency_key, attempt_id=attempt.id)
            apply_outcome_to_attempt(attempt, outcome)
            emit_event(
                db,
                "send.pre_provider_blocked",
                entity_type="send_queue",
                entity_id=attempt.queue_id,
                payload=outcome_audit_payload(outcome),
            )
            blocked += 1
            db.commit()
            continue
        decision = evaluate_policy(entry, db)
        store_policy_result(entry, decision)
        if not decision.all_passed:
            outcome = simulated_outcome(
                adapter.resolution,
                error_code="policy_changed_before_provider_call",
                detail="Policy changed immediately before provider execution",
                blocked=True,
            ).with_context(idempotency_key=entry.idempotency_key, attempt_id=attempt.id)
            apply_outcome_to_attempt(attempt, outcome)
            entry.status = "blocked"
            entry.processing_started_at = None
            entry.processing_token = None
            _update_followup_status(db, entry, "blocked", ",".join(decision.block_reason_codes))
            emit_event(
                db,
                "send.pre_provider_blocked",
                entity_type="send_queue",
                entity_id=entry.id,
                payload={**outcome_audit_payload(outcome), "reasons": decision.block_reason_codes},
            )
            blocked += 1
            db.commit()
            continue
        outcome = (
            await adapter.send_message(contact.email, subject, body, user, password)
        ).with_context(idempotency_key=entry.idempotency_key, attempt_id=attempt.id)
        outcome = validate_send_outcome(
            outcome,
            adapter.resolution,
            trusted_provider_adapter=True,
        )
        db.expire_all()
        if outcome.provider_accepted and provider_attempt_stop_fence_changed(db, attempt.id):
            _record_late_stop_outcome(db, entry.id, attempt.id, outcome)
            reconciliation_required += 1
            continue
        sent_at = utcnow() if outcome.provider_accepted else None
        apply_outcome_to_attempt(attempt, outcome, sent_at=sent_at)
        if (
            outcome.provider_accepted is not True
            and outcome.provider_contacted is False
            and outcome.attempt_status != "reconciliation_required"
        ):
            attempt.dispatch_lock_key = None
        db.flush()
        target_status = (
            "provider_accepted"
            if outcome.provider_accepted
            else "reconciliation_required"
            if outcome.attempt_status == "reconciliation_required"
            else "simulated"
            if outcome.attempt_status == "simulated"
            else "failed"
        )
        fenced = (
            db.query(SendQueue)
            .filter(
                SendQueue.id == entry.id,
                SendQueue.status == "processing",
                SendQueue.processing_token == claim_token,
            )
            .update(
                {
                    "status": target_status,
                    "processing_started_at": None,
                    "processing_token": None,
                },
                synchronize_session=False,
            )
        )
        if fenced != 1:
            db.rollback()
            _record_lost_claim_outcome(db, entry.id, attempt.id, outcome)
            reconciliation_required += 1
            continue
        db.expire_all()
        entry = db.get(SendQueue, entry.id)
        attempt = db.get(SendAttempt, attempt.id)
        contact = db.get(Contact, entry.contact_id)
        draft = db.get(Draft, entry.draft_id)
        if outcome.provider_accepted:
            contact.status = "sent"
            db.add(
                ConversationMessage(
                    contact_id=contact.id,
                    direction="outbound",
                    subject=subject,
                    body=body,
                    source="queue",
                    external_message_id=outcome.provider_message_id or outcome.tracking_message_id,
                    occurred_at=sent_at,
                )
            )
            emit_event(
                db,
                "send.success",
                entity_type="send_queue",
                entity_id=entry.id,
                payload=outcome_audit_payload(outcome),
            )
            _update_followup_status(db, entry, "provider_accepted")
            handled, _ = schedule_next_sequence_after_acceptance(db, entry, attempt)
            if not handled:
                _schedule_followup(db, contact.id, draft.id, sent_at, entry.sequence_num + 1)
            provider_accepted += 1
        elif outcome.attempt_status == "reconciliation_required":
            emit_event(
                db,
                "send.reconciliation_required",
                entity_type="send_queue",
                entity_id=entry.id,
                payload=outcome_audit_payload(outcome),
            )
            _update_followup_status(db, entry, "reconciliation_required", outcome.error_code)
            reconciliation_required += 1
        elif outcome.attempt_status == "simulated":
            emit_event(
                db,
                "send.simulated",
                entity_type="send_queue",
                entity_id=entry.id,
                payload=outcome_audit_payload(outcome),
            )
            _update_followup_status(db, entry, "simulated", outcome.error_code)
            simulated += 1
        else:
            emit_event(
                db,
                "send.failed",
                entity_type="send_queue",
                entity_id=entry.id,
                payload=outcome_audit_payload(outcome),
            )
            _update_followup_status(db, entry, "failed", outcome.error_code)
            failed += 1
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            _record_post_provider_commit_failure(db, entry.id, attempt.id, outcome, exc)
            if outcome.provider_accepted:
                provider_accepted -= 1
            elif outcome.attempt_status == "reconciliation_required":
                reconciliation_required -= 1
            elif outcome.attempt_status == "simulated":
                simulated -= 1
            else:
                failed -= 1
            reconciliation_required += 1
            continue
    return {
        "processed": processed,
        "eligible_count": eligible_count,
        "provider_accepted": provider_accepted,
        "sent": provider_accepted,
        "blocked": blocked,
        "simulated": simulated,
        "skipped": simulated,
        "failed": failed,
        "reconciliation_required": reconciliation_required,
        "deferred": deferred,
        "policy_rescheduled": rescheduled,
        "future_scheduled_count": future_summary["future_scheduled_count"],
        "next_due_at": future_summary["next_due_at"],
        "blocked_reasons": future_summary["blocked_reasons"],
        "scheduler_effective": _scheduler_effective(db),
        "zero_work_reason": (
            "future_scheduled"
            if processed == 0 and future_summary["future_scheduled_count"]
            else "no_eligible_rows"
            if processed == 0
            else None
        ),
    }


def _reconcile_stale_processing(db: Session, now) -> None:
    cutoff = now - STALE_ATTEMPT_AFTER
    stale_queues = db.query(SendQueue).filter(SendQueue.status == "processing").all()
    changed = False
    for queue in stale_queues:
        lease_started_at = _aware_utc(queue.processing_started_at or queue.created_at)
        if lease_started_at is None or lease_started_at > cutoff:
            continue
        attempt = (
            db.query(SendAttempt)
            .filter(SendAttempt.queue_id == queue.id, SendAttempt.status == "attempting")
            .order_by(SendAttempt.created_at.desc(), SendAttempt.id.desc())
            .first()
        )
        if attempt is None:
            attempt = SendAttempt(
                queue_id=queue.id,
                contact_id=queue.contact_id,
                draft_id=queue.draft_id,
                idempotency_key=queue.idempotency_key,
                status="reconciliation_required",
                sender_identity=get_value(db, "gmail_user"),
                configured_transport=get_value(db, "email_transport", "smtp"),
                effective_transport=None,
                transport_source="legacy_orphaned_processing_claim",
                simulated=False,
                provider_contacted=False,
                provider_accepted=False,
                provider_response_classification="processing_claim_without_attempt",
                error_code="orphaned_processing_claim",
                error_detail="Processing claim became stale before durable attempt evidence was recorded",
                created_at=now,
            )
            db.add(attempt)
        attempt.status = "reconciliation_required"
        attempt.error_code = attempt.error_code or "stale_processing_attempt"
        attempt.error_detail = attempt.error_detail or "Processing claim became stale; reconcile provider state before retry"
        attempt.provider_accepted = False
        queue.status = "reconciliation_required"
        queue.processing_started_at = None
        queue.processing_token = None
        _update_followup_status(db, queue, "reconciliation_required", attempt.error_code)
        emit_event(
            db,
            "send.reconciliation_required",
            entity_type="send_queue",
            entity_id=queue.id,
            payload={
                "attempt_id": attempt.id,
                "attempt_status": "reconciliation_required",
                "configured_transport": attempt.configured_transport,
                "effective_transport": attempt.effective_transport,
                "transport_source": attempt.transport_source,
                "simulated": False,
                "provider_contacted": attempt.provider_contacted,
                "provider_accepted": False,
                "error_code": attempt.error_code,
            },
        )
        changed = True

    stale_attempts = (
        db.query(SendAttempt)
        .filter(
            SendAttempt.status == "attempting",
            SendAttempt.created_at.is_not(None),
            SendAttempt.created_at <= cutoff,
        )
        .all()
    )
    for attempt in stale_attempts:
        queue = db.get(SendQueue, attempt.queue_id)
        if queue is None or queue.status != "processing":
            continue
        attempt.status = "reconciliation_required"
        attempt.error_code = "stale_processing_attempt"
        attempt.error_detail = "Processing claim became stale; reconcile provider state before retry"
        attempt.provider_accepted = False
        queue.status = "reconciliation_required"
        queue.processing_started_at = None
        queue.processing_token = None
        _update_followup_status(db, queue, "reconciliation_required", attempt.error_code)
        emit_event(
            db,
            "send.reconciliation_required",
            entity_type="send_queue",
            entity_id=queue.id,
            payload={
                "attempt_id": attempt.id,
                "attempt_status": "reconciliation_required",
                "configured_transport": attempt.configured_transport,
                "effective_transport": attempt.effective_transport,
                "transport_source": attempt.transport_source,
                "simulated": False,
                "provider_contacted": attempt.provider_contacted,
                "provider_accepted": False,
                "error_code": attempt.error_code,
            },
        )
        changed = True
    if changed:
        db.commit()
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


def _update_followup_status(db: Session, entry: SendQueue, status: str, reason: str | None = None) -> None:
    if entry.sequence_num <= 1:
        return
    sequence = db.query(FollowUpSequence).filter_by(contact_id=entry.contact_id, sequence_num=entry.sequence_num).first()
    if sequence is None:
        return
    sequence.status = status
    sequence.stop_reason = reason
    emit_event(
        db,
        "followup.status_changed",
        entity_type="follow_up_sequence",
        entity_id=sequence.id,
        payload={"queue_id": entry.id, "sequence_num": entry.sequence_num, "status": status, "reason": reason},
    )


def _record_post_provider_commit_failure(db: Session, queue_id: str, attempt_id: str, outcome, exc: Exception) -> None:
    queue = db.get(SendQueue, queue_id)
    attempt = db.get(SendAttempt, attempt_id)
    if queue is None or attempt is None:
        raise RuntimeError("post_provider_reconciliation_target_missing") from exc
    apply_outcome_to_attempt(attempt, outcome, sent_at=utcnow() if outcome.provider_accepted else None)
    attempt.status = "reconciliation_required"
    attempt.error_code = "post_provider_commit_failed"
    attempt.error_detail = redacted_error(exc)
    queue.status = "reconciliation_required"
    queue.processing_started_at = None
    queue.processing_token = None
    _update_followup_status(db, queue, "reconciliation_required", attempt.error_code)
    emit_event(
        db,
        "send.reconciliation_required",
        entity_type="send_queue",
        entity_id=queue.id,
        payload={
            **outcome_audit_payload(outcome.with_context(attempt_id=attempt.id)),
            "error_code": attempt.error_code,
        },
    )
    db.commit()


def _record_lost_claim_outcome(db: Session, queue_id: str, attempt_id: str, outcome) -> None:
    queue = db.get(SendQueue, queue_id)
    attempt = db.get(SendAttempt, attempt_id)
    if queue is None or attempt is None:
        raise RuntimeError("lost_claim_reconciliation_target_missing")
    prior_queue_status = queue.status
    apply_outcome_to_attempt(
        attempt,
        outcome,
        sent_at=utcnow() if outcome.provider_accepted else None,
    )
    attempt.status = "reconciliation_required"
    attempt.error_code = "claim_revoked_after_provider_call"
    attempt.error_detail = "Provider outcome arrived after Queue claim ownership was revoked"
    queue.status = "reconciliation_required"
    queue.processing_started_at = None
    queue.processing_token = None
    emit_event(
        db,
        "send.reconciliation_required",
        entity_type="send_queue",
        entity_id=queue.id,
        payload={
            **outcome_audit_payload(outcome.with_context(attempt_id=attempt.id)),
            "error_code": attempt.error_code,
            "prior_queue_status": prior_queue_status,
        },
    )
    db.commit()


def reconcile_queue_entry(db: Session, queue_id: str, action: str) -> SendQueue:
    queue = db.get(SendQueue, queue_id)
    if queue is None:
        raise ValueError("queue_not_found")
    if queue.status != "reconciliation_required":
        raise ValueError("queue_not_reconciliation_required")
    attempt = (
        db.query(SendAttempt)
        .filter(SendAttempt.queue_id == queue.id)
        .order_by(SendAttempt.created_at.desc(), SendAttempt.id.desc())
        .first()
    )
    if action == "cancel":
        if provider_acceptance_evidence_present(attempt):
            raise ValueError("provider_acceptance_reconciliation_requires_finalize")
        queue.status = "cancelled"
        queue.processing_started_at = None
        queue.processing_token = None
        _update_followup_status(db, queue, "cancelled", "RECONCILIATION_CANCELLED")
        emit_event(
            db,
            "send.reconciliation_cancelled",
            entity_type="send_queue",
            entity_id=queue.id,
            payload={"attempt_id": attempt.id if attempt else None},
        )
        db.commit()
        return queue
    if action != "finalize_provider_accepted":
        raise ValueError("invalid_reconciliation_action")
    if not provider_acceptance_evidence_present(attempt):
        raise ValueError("provider_acceptance_evidence_missing")
    contact_claimed = (
        db.query(Contact)
        .filter(
            Contact.id == queue.contact_id,
            Contact.send_stop_generation == (attempt.stop_generation or 0),
        )
        .update(
            {Contact.send_stop_generation: Contact.send_stop_generation},
            synchronize_session=False,
        )
    )
    if contact_claimed != 1:
        db.expire_all()
        current_queue = db.get(SendQueue, queue_id)
        if current_queue is not None and current_queue.status == "reconciliation_required":
            current_queue.status = "cancelled"
            current_queue.processing_started_at = None
            current_queue.processing_token = None
            _update_followup_status(db, current_queue, "cancelled", "CONTACT_SEND_STOPPED")
        emit_event(
            db,
            "send.reconciliation_blocked",
            entity_type="send_queue",
            entity_id=queue_id,
            payload={"attempt_id": attempt.id, "reason": "CONTACT_SEND_STOPPED"},
        )
        db.commit()
        raise ValueError("contact_send_stopped")
    reconciliation_token = uuid.uuid4().hex
    queue_claimed = (
        db.query(SendQueue)
        .filter(
            SendQueue.id == queue_id,
            SendQueue.status == "reconciliation_required",
        )
        .update(
            {
                "status": "processing",
                "processing_started_at": utcnow(),
                "processing_token": reconciliation_token,
            },
            synchronize_session=False,
        )
    )
    if queue_claimed != 1:
        db.rollback()
        raise ValueError("queue_not_reconciliation_required")
    db.expire_all()
    queue = db.get(SendQueue, queue_id)
    attempt = db.get(SendAttempt, attempt.id)
    contact = db.get(Contact, queue.contact_id)
    queue.status = "provider_accepted"
    queue.processing_started_at = None
    queue.processing_token = None
    draft = db.get(Draft, queue.draft_id)
    accepted_at = attempt.sent_at or utcnow()
    attempt.status = "provider_accepted"
    attempt.sent_at = accepted_at
    if contact:
        contact.status = "sent"
    external_message_id = attempt.provider_msg_id or attempt.tracking_message_id
    existing_message = None
    if contact and external_message_id:
        existing_message = (
            db.query(ConversationMessage)
            .filter_by(
                contact_id=contact.id,
                direction="outbound",
                external_message_id=external_message_id,
            )
            .first()
        )
    if contact and draft and existing_message is None:
        db.add(
            ConversationMessage(
                contact_id=contact.id,
                direction="outbound",
                subject=draft.subject,
                body=draft.body,
                source="queue_reconciled",
                external_message_id=external_message_id,
                occurred_at=accepted_at,
            )
        )
    _update_followup_status(db, queue, "provider_accepted")
    handled, _ = schedule_next_sequence_after_acceptance(db, queue, attempt)
    if not handled:
        _schedule_followup(db, queue.contact_id, queue.draft_id, accepted_at, queue.sequence_num + 1)
    emit_event(
        db,
        "send.reconciliation_finalized",
        entity_type="send_queue",
        entity_id=queue.id,
        payload={"attempt_id": attempt.id, "provider_accepted": True},
    )
    db.commit()
    return queue


def retry_queue_entry(db: Session, queue_id: str) -> SendQueue:
    queue = db.get(SendQueue, queue_id)
    if queue is None:
        raise ValueError("queue_not_found")
    latest = (
        db.query(SendAttempt)
        .filter(SendAttempt.queue_id == queue.id)
        .order_by(SendAttempt.created_at.desc(), SendAttempt.id.desc())
        .first()
    )
    if latest and (latest.provider_accepted is True or latest.status == "reconciliation_required"):
        raise ValueError("queue_retry_requires_reconciliation")
    if latest and latest.provider_contacted is not False:
        raise ValueError("queue_retry_provider_contact_uncertain")
    if queue.status not in {"failed", "blocked"}:
        raise ValueError("queue_not_retryable")
    queue.status = "pending"
    queue.scheduled_at = utcnow()
    queue.processing_started_at = None
    queue.processing_token = None
    queue.policy_block_reasons = json.dumps([])
    _update_followup_status(db, queue, "queued")
    emit_event(
        db,
        "send.retry_scheduled",
        entity_type="send_queue",
        entity_id=queue.id,
        payload={"attempt_id": latest.id if latest else None, "provider_contacted": latest.provider_contacted if latest else False},
    )
    db.commit()
    return queue


def mark_queue_entry_for_send_now(db: Session, queue_id: str) -> tuple[SendQueue, list[str]]:
    now = utcnow()
    _reconcile_stale_processing(db, now)
    queue = db.get(SendQueue, queue_id)
    if queue is None:
        raise ValueError("queue_not_found")
    if queue.status in {"sent", "provider_accepted"}:
        raise ValueError("queue_already_provider_accepted")
    if queue.status == "processing":
        raise ValueError("queue_processing")
    latest = _latest_attempt(db, queue)
    if not _current_attempt_is_safe_to_retry(latest):
        raise ValueError("queue_retry_requires_reconciliation")
    if queue.status not in {"pending", "skipped", "simulated", "failed", "blocked", "cancelled"}:
        raise ValueError("queue_not_send_now_eligible")
    decision = evaluate_policy(queue, db)
    store_policy_result(queue, decision)
    if not decision.all_passed:
        if _temporary_block_only(decision.block_reason_codes):
            queue.status = "pending"
            queue.scheduled_at = _next_retry_at(db, now, decision.block_reason_codes)
            queue.schedule_source = POLICY_DEFERRED_SCHEDULE_SOURCE
        else:
            queue.status = "blocked"
            queue.scheduled_at = now
            queue.schedule_source = POLICY_RELEASED_SCHEDULE_SOURCE
            if queue.contact:
                queue.contact.status = "blocked_by_policy"
            _update_followup_status(db, queue, "blocked", ",".join(decision.block_reason_codes))
        queue.processing_started_at = None
        queue.processing_token = None
        emit_event(
            db,
            "queue.send_now_blocked",
            entity_type="send_queue",
            entity_id=queue.id,
            payload={"reasons": decision.block_reason_codes},
        )
        db.commit()
        return queue, decision.block_reason_codes
    queue.status = "pending"
    queue.scheduled_at = now
    queue.schedule_source = EXPLICIT_SEND_NOW_SCHEDULE_SOURCE
    queue.policy_block_reasons = json.dumps([])
    queue.processing_started_at = None
    queue.processing_token = None
    if queue.contact and queue.contact.status == "blocked_by_policy":
        queue.contact.status = "approved"
    _update_followup_status(db, queue, "queued")
    emit_event(
        db,
        "queue.send_now_scheduled",
        entity_type="send_queue",
        entity_id=queue.id,
        payload={"scheduled_at": _aware_utc(now).isoformat()},
    )
    db.commit()
    return queue, []


async def send_queue_entry_now(db: Session, queue_id: str, transport=None) -> dict:
    queue, reasons = mark_queue_entry_for_send_now(db, queue_id)
    if reasons:
        return {
            "eligible": False,
            "reasons": reasons,
            "queue": queue_to_dict(queue, db),
            "result": {
                "processed": 0,
                "eligible_count": 0,
                "provider_accepted": 0,
                "sent": 0,
                "blocked": 0,
                "simulated": 0,
                "skipped": 0,
                "failed": 0,
                "reconciliation_required": 0,
                "deferred": 1 if _temporary_block_only(reasons) else 0,
                "policy_rescheduled": 0,
                "future_scheduled_count": 1 if _temporary_block_only(reasons) else 0,
                "next_due_at": _aware_utc(queue.scheduled_at).isoformat() if queue.scheduled_at else None,
                "blocked_reasons": {reason: 1 for reason in reasons},
                "scheduler_effective": _scheduler_effective(db),
                "zero_work_reason": "policy_blocked",
            },
            "delivery_status": "deferred" if _temporary_block_only(reasons) else "blocked",
        }
    result = await process_pending_queue(db, transport=transport, queue_ids=[queue.id])
    db.expire_all()
    queue = db.get(SendQueue, queue_id)
    return {
        "eligible": True,
        "reasons": [],
        "queue": queue_to_dict(queue, db) if queue else None,
        "result": result,
        "delivery_status": _delivery_status_from_result(result, queue),
    }


def _delivery_status_from_result(result: dict, queue: SendQueue | None) -> str:
    if result.get("provider_accepted"):
        return "provider_accepted"
    if result.get("simulated"):
        return "simulated"
    if result.get("reconciliation_required"):
        return "reconciliation_required"
    if result.get("deferred"):
        return "deferred"
    if result.get("blocked"):
        return "blocked"
    if result.get("failed"):
        return "failed"
    if queue is None:
        return "queued"
    if queue.status == "provider_accepted":
        return "provider_accepted"
    if queue.status == "pending":
        return "deferred"
    return queue.status or "queued"


def _record_late_stop_outcome(db: Session, queue_id: str, attempt_id: str, outcome) -> None:
    queue = db.get(SendQueue, queue_id)
    attempt = db.get(SendAttempt, attempt_id)
    if queue is None or attempt is None:
        raise RuntimeError("late_stop_reconciliation_target_missing")
    prior_queue_status = queue.status
    apply_outcome_to_attempt(attempt, outcome, sent_at=utcnow())
    attempt.status = "reconciliation_required"
    attempt.error_code = "contact_stopped_after_provider_call"
    attempt.error_detail = "Contact send authority changed while the provider call was in flight"
    queue.status = "reconciliation_required"
    queue.processing_started_at = None
    queue.processing_token = None
    emit_event(
        db,
        "send.reconciliation_required",
        entity_type="send_queue",
        entity_id=queue.id,
        payload={
            **outcome_audit_payload(outcome.with_context(attempt_id=attempt.id)),
            "error_code": attempt.error_code,
            "prior_queue_status": prior_queue_status,
        },
    )
    db.commit()


def cancel_queue_entry(db: Session, queue_id: str) -> SendQueue:
    queue = db.get(SendQueue, queue_id)
    if queue is None:
        raise ValueError("queue_not_found")
    if queue.status in {"processing", "reconciliation_required"}:
        raise ValueError("queue_inflight_or_ambiguous")
    if queue.status in {"provider_accepted", "sent"}:
        raise ValueError("queue_already_provider_accepted")
    if queue.status == "cancelled":
        return queue
    if queue.status not in {"pending", "skipped", "simulated", "blocked", "failed"}:
        raise ValueError("queue_not_cancellable")
    queue.status = "cancelled"
    queue.processing_started_at = None
    queue.processing_token = None
    _update_followup_status(db, queue, "cancelled", "OPERATOR_CANCELLED")
    emit_event(
        db,
        "queue.cancelled",
        entity_type="send_queue",
        entity_id=queue.id,
        payload={"reason": "OPERATOR_CANCELLED"},
    )
    db.commit()
    return queue


def clear_queue_entries(db: Session) -> dict[str, int]:
    rows = db.query(SendQueue).order_by(SendQueue.created_at.asc()).all()
    cancelled = 0
    already_cancelled = 0
    preserved_accepted = 0
    preserved_uncertain = 0
    skipped = 0

    for queue in rows:
        attempt = _latest_attempt(db, queue)
        if queue.status in {"provider_accepted", "sent"} or (
            attempt is not None and provider_acceptance_evidence_present(attempt)
        ):
            preserved_accepted += 1
            continue
        if queue.status in {"processing", "reconciliation_required"} or (
            attempt is not None and attempt.provider_contacted is not False
        ):
            preserved_uncertain += 1
            continue
        if queue.status == "cancelled":
            already_cancelled += 1
            continue
        if queue.status not in {"pending", "skipped", "simulated", "blocked", "failed"}:
            skipped += 1
            continue

        queue.status = "cancelled"
        queue.processing_started_at = None
        queue.processing_token = None
        _update_followup_status(db, queue, "cancelled", OPERATOR_CLEARED_QUEUE_REASON)
        cancelled += 1

    emit_event(
        db,
        "queue.cleared",
        entity_type="send_queue",
        payload={
            "cancelled": cancelled,
            "already_cancelled": already_cancelled,
            "preserved_accepted": preserved_accepted,
            "preserved_uncertain": preserved_uncertain,
            "skipped": skipped,
            "reason": OPERATOR_CLEARED_QUEUE_REASON,
        },
    )
    db.commit()
    return {
        "cancelled": cancelled,
        "already_cancelled": already_cancelled,
        "preserved_accepted": preserved_accepted,
        "preserved_uncertain": preserved_uncertain,
        "skipped": skipped,
    }
