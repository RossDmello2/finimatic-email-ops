from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.core.idempotency import sha256_key
from app.db.session import get_db
from app.send.governance import begin_provider_attempt, persist_provider_outcome
from app.send.queue_worker import release_recoverable_queue_entries
from app.send.smtp_adapter import GmailAdapter
from app.send.truth import outcome_audit_payload
from app.settings.service import get_secret, get_value, set_value

router = APIRouter(prefix="/api/canary", tags=["canary"])


@router.post("/send")
async def send_canary(db: Session = Depends(get_db)):
    user = get_value(db, "gmail_user")
    report_recipient = get_value(db, "report_recipient")
    idempotency_key = sha256_key(
        "canary",
        user,
        report_recipient,
        get_value(db, "email_transport", "smtp"),
        get_value(db, "gmail_app_password"),
        get_value(db, "gmail_api_client_id"),
        get_value(db, "gmail_api_client_secret"),
        get_value(db, "gmail_api_refresh_token"),
    )
    emit_event(db, "canary.attempt", payload={"sender": user, "recipient": report_recipient})

    adapter = GmailAdapter.from_settings(db)
    attempt, dispatch_state = begin_provider_attempt(
        db,
        queue_id="canary",
        contact_id="canary",
        draft_id="canary",
        idempotency_key=idempotency_key,
        sender_identity=user,
        configured_transport=adapter.resolution.configured_transport,
        effective_transport=adapter.resolution.effective_transport,
        transport_source=adapter.resolution.transport_source,
        entity_type="canary",
        entity_id=idempotency_key,
        audit_source="canary",
    )
    if dispatch_state != "ready":
        released_queue_entries = 0
        if dispatch_state == "provider_accepted":
            set_value(db, "canary_verified", "true")
            set_value(db, "sender_readiness", "canary_verified")
            released_queue_entries = release_recoverable_queue_entries(db)
        emit_event(
            db,
            "canary.duplicate_blocked",
            payload={"attempt_id": attempt.id, "dispatch_state": dispatch_state},
        )
        db.commit()
        return {
            "status": "duplicate_blocked",
            "previous_attempt_id": attempt.id,
            "provider_accepted": dispatch_state == "provider_accepted",
            "released_queue_entries": released_queue_entries,
        }

    password = get_secret(db, "gmail_app_password")
    result = await adapter.canary_send(user, password, report_recipient, idempotency_key)
    outcome = result.outcome

    released_queue_entries = 0

    def project_canary(session: Session, projected_attempt) -> None:
        nonlocal released_queue_entries
        contextual = outcome.with_context(attempt_id=projected_attempt.id)
        if contextual.provider_accepted:
            set_value(session, "canary_verified", "true")
            set_value(session, "sender_readiness", "canary_verified")
            released_queue_entries = release_recoverable_queue_entries(session)
            emit_event(
                session,
                "canary.success",
                payload={
                    **outcome_audit_payload(contextual),
                    "nonce": result.nonce,
                    "sender": user,
                    "recipient": report_recipient,
                },
            )
        else:
            event_type = "canary.simulated" if contextual.simulated else "canary.failed"
            emit_event(session, event_type, payload=outcome_audit_payload(contextual))

    attempt, persisted = persist_provider_outcome(
        db,
        attempt_id=attempt.id,
        outcome=outcome,
        entity_type="canary",
        entity_id=idempotency_key,
        audit_source="canary",
        project=project_canary,
    )
    outcome = outcome.with_context(attempt_id=attempt.id)
    if not persisted:
        return {
            **outcome.to_dict(),
            "status": "reconciliation_required",
            "attempt_id": attempt.id,
        }
    if not outcome.provider_accepted:
        return outcome.to_dict()

    return {
        "status": "provider_accepted",
        "provider_accepted": True,
        "nonce": result.nonce,
        "sent_at": result.timestamp,
        "sender_identity": user,
        "message_id": outcome.provider_message_id,
        "idempotency_key": idempotency_key,
        "attempt_id": attempt.id,
        "configured_transport": outcome.configured_transport,
        "effective_transport": outcome.effective_transport,
        "released_queue_entries": released_queue_entries,
    }
