from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.core.idempotency import sha256_key
from app.core.time import utcnow
from app.db.models import SendAttempt
from app.db.session import get_db
from app.send.smtp_adapter import GmailAdapter
from app.settings.service import get_secret, get_value, set_value

router = APIRouter(prefix="/api/canary", tags=["canary"])


@router.post("/send")
async def send_canary(db: Session = Depends(get_db)):
    user = get_value(db, "gmail_user")
    password = get_secret(db, "gmail_app_password")
    report_recipient = get_value(db, "report_recipient")
    idempotency_key = sha256_key("canary", user, report_recipient)
    emit_event(db, "canary.attempt", payload={"sender": user, "recipient": report_recipient})

    existing = db.query(SendAttempt).filter_by(idempotency_key=idempotency_key, status="success").first()
    if existing:
        emit_event(db, "canary.duplicate_blocked", payload={"attempt_id": existing.id})
        db.commit()
        return {"status": "duplicate_blocked", "previous_attempt_id": existing.id}

    result = await GmailAdapter.from_settings(db).canary_send(user, password, report_recipient, idempotency_key)
    if result.status != "success":
        db.add(
            SendAttempt(
                queue_id="canary",
                contact_id="canary",
                draft_id="canary",
                idempotency_key=idempotency_key,
                status="failed",
                sender_identity=user,
                sent_at=utcnow(),
                error_code="canary_failed",
                error_detail="Canary send failed",
            )
        )
        db.commit()
        return {"status": "failed", "error_code": "canary_failed"}

    attempt = SendAttempt(
        queue_id="canary",
        contact_id="canary",
        draft_id="canary",
        idempotency_key=idempotency_key,
        provider_msg_id=result.provider_msg_id,
        status="success",
        sender_identity=user,
        sent_at=utcnow(),
        smtp_response="canary sent",
    )
    db.add(attempt)
    set_value(db, "canary_verified", "true")
    set_value(db, "sender_readiness", "canary_verified")
    emit_event(db, "canary.success", payload={"nonce": result.nonce, "sender": user, "recipient": report_recipient})
    db.commit()
    return {
        "status": "success",
        "nonce": result.nonce,
        "sent_at": result.timestamp,
        "sender_identity": user,
        "message_id": result.provider_msg_id,
        "idempotency_key": idempotency_key,
    }
