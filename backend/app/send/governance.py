from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.memory import get_or_create_session
from app.agent.pending import create_generic_pending_action
from app.audit.service import emit_event
from app.core.crypto import redacted_error
from app.core.time import utcnow
from app.db.models import Contact, PendingAgentAction, SendAttempt
from app.send.outcomes import SendOutcome
from app.send.truth import apply_outcome_to_attempt, outcome_audit_payload


def prepare_governed_action(
    db: Session,
    *,
    session_token: str,
    capability: str,
    entity_type: str,
    entity_id: str | None,
    params: dict[str, Any],
    source_label: str,
    goal: str,
    evidence_summary: str,
    policy_result: str,
    proposed_side_effect: str,
    confirmation_prompt: str,
) -> PendingAgentAction:
    session = get_or_create_session(session_token, db)
    action = create_generic_pending_action(
        session.id,
        capability=capability,
        entity_type=entity_type,
        entity_id=entity_id,
        params=params,
        source_label=source_label,
        goal=goal,
        evidence_summary=evidence_summary,
        policy_result=policy_result,
        proposed_side_effect=proposed_side_effect,
        confirmation_prompt=confirmation_prompt,
        db=db,
    )
    return action


def governed_action_to_dict(action: PendingAgentAction) -> dict[str, Any]:
    snapshot = json.loads(action.action_snapshot_redacted or "{}")
    params = snapshot.get("params") if isinstance(snapshot.get("params"), dict) else {}
    payload = {
        "action_id": action.id,
        "capability": action.capability,
        "entity_type": action.entity_type,
        "entity_id": action.entity_id,
        "contact_id": params.get("contact_id"),
        "draft_id": params.get("draft_id"),
        "to": params.get("to"),
        "subject": params.get("subject"),
        "body": params.get("body"),
        "goal": snapshot.get("goal"),
        "evidence_summary": snapshot.get("evidence_summary"),
        "policy_result": snapshot.get("policy_result"),
        "proposed_side_effect": snapshot.get("proposed_side_effect"),
        "confirmation_prompt": action.confirmation_prompt,
        "expires_at": action.expires_at.isoformat() if action.expires_at else None,
        "consumed": action.consumed,
    }
    if action.capability == "auto_reply_enable_autonomous":
        payload["activation_details"] = {
            "mailbox": params.get("mailbox"),
            "daily_cap": params.get("daily_cap"),
            "minimum_gap_minutes": params.get("minimum_gap_minutes"),
            "safe_intents": params.get("safe_intents"),
            "stop_conditions": params.get("stop_conditions"),
        }
    return payload


def begin_provider_attempt(
    db: Session,
    *,
    queue_id: str,
    contact_id: str,
    draft_id: str,
    idempotency_key: str,
    sender_identity: str,
    configured_transport: str,
    effective_transport: str,
    transport_source: str,
    entity_type: str,
    entity_id: str,
    audit_source: str,
) -> tuple[SendAttempt, str]:
    contact = db.get(Contact, contact_id)
    stop_generation = contact.send_stop_generation if contact is not None else 0
    latest = (
        db.query(SendAttempt)
        .filter(SendAttempt.queue_id == queue_id, SendAttempt.idempotency_key == idempotency_key)
        .order_by(SendAttempt.created_at.desc(), SendAttempt.id.desc())
        .first()
    )
    if latest and latest.provider_accepted is True:
        return latest, "provider_accepted"
    if latest and (
        latest.status in {"attempting", "reconciliation_required"}
        or latest.provider_contacted is True
    ):
        return latest, "reconciliation_required"

    attempt = SendAttempt(
        queue_id=queue_id,
        contact_id=contact_id,
        draft_id=draft_id,
        idempotency_key=idempotency_key,
        dispatch_lock_key=idempotency_key,
        stop_generation=stop_generation,
        status="attempting",
        sender_identity=sender_identity,
        configured_transport=configured_transport,
        effective_transport=effective_transport,
        transport_source=transport_source,
        simulated=False,
        provider_contacted=False,
        provider_accepted=False,
        created_at=utcnow(),
    )
    db.add(attempt)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        locked = (
            db.query(SendAttempt)
            .filter(SendAttempt.dispatch_lock_key == idempotency_key)
            .order_by(SendAttempt.created_at.desc(), SendAttempt.id.desc())
            .first()
        )
        if locked is None:
            raise
        if locked.provider_accepted is True:
            return locked, "provider_accepted"
        return locked, "reconciliation_required"
    emit_event(
        db,
        "send.attempt",
        entity_type=entity_type,
        entity_id=entity_id,
        payload={
            "source": audit_source,
            "attempt_id": attempt.id,
            "configured_transport": configured_transport,
            "effective_transport": effective_transport,
            "transport_source": transport_source,
            "simulated": False,
            "provider_contacted": False,
            "provider_accepted": False,
        },
    )
    db.commit()
    return attempt, "ready"


def provider_attempt_stop_fence_changed(db: Session, attempt_id: str) -> bool:
    attempt = db.get(SendAttempt, attempt_id)
    if attempt is None:
        return True
    contact = db.get(Contact, attempt.contact_id)
    if contact is None:
        return True
    return (contact.send_stop_generation or 0) != (attempt.stop_generation or 0)


def persist_provider_outcome(
    db: Session,
    *,
    attempt_id: str,
    outcome: SendOutcome,
    entity_type: str,
    entity_id: str,
    audit_source: str,
    project: Callable[[Session, SendAttempt], None] | None = None,
) -> tuple[SendAttempt, bool]:
    attempt = db.get(SendAttempt, attempt_id)
    if attempt is None:
        raise RuntimeError("provider_attempt_missing")
    apply_outcome_to_attempt(
        attempt,
        outcome,
        sent_at=utcnow() if outcome.provider_accepted else None,
    )
    if (
        outcome.provider_accepted is not True
        and outcome.provider_contacted is False
        and outcome.attempt_status != "reconciliation_required"
    ):
        attempt.dispatch_lock_key = None
    if project is not None:
        project(db, attempt)
    try:
        db.commit()
        return attempt, True
    except Exception as exc:
        db.rollback()
        attempt = db.get(SendAttempt, attempt_id)
        if attempt is None:
            raise RuntimeError("provider_attempt_missing_after_commit_failure") from exc
        apply_outcome_to_attempt(
            attempt,
            outcome,
            sent_at=utcnow() if outcome.provider_accepted else None,
        )
        attempt.status = "reconciliation_required"
        attempt.error_code = "post_provider_commit_failed"
        attempt.error_detail = redacted_error(exc)
        emit_event(
            db,
            "send.reconciliation_required",
            entity_type=entity_type,
            entity_id=entity_id,
            payload={
                "source": audit_source,
                **outcome_audit_payload(outcome.with_context(attempt_id=attempt.id)),
                "error_code": attempt.error_code,
            },
        )
        db.commit()
        return attempt, False


def persist_provider_outcome_as_reconciliation(
    db: Session,
    *,
    attempt_id: str,
    outcome: SendOutcome,
    entity_type: str,
    entity_id: str,
    audit_source: str,
    error_code: str,
    error_detail: str,
) -> SendAttempt:
    attempt = db.get(SendAttempt, attempt_id)
    if attempt is None:
        raise RuntimeError("provider_attempt_missing")
    apply_outcome_to_attempt(
        attempt,
        outcome,
        sent_at=utcnow() if outcome.provider_accepted else None,
    )
    attempt.status = "reconciliation_required"
    attempt.error_code = error_code
    attempt.error_detail = error_detail
    emit_event(
        db,
        "send.reconciliation_required",
        entity_type=entity_type,
        entity_id=entity_id,
        payload={
            "source": audit_source,
            **outcome_audit_payload(outcome.with_context(attempt_id=attempt.id)),
            "attempt_status": "reconciliation_required",
            "error_code": error_code,
        },
    )
    db.commit()
    return attempt
