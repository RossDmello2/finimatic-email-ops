from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.core.crypto import redacted_error
from app.db.session import get_db
from app.send.smtp_adapter import GmailAdapter
from app.settings.service import get_secret, get_value, set_settings, set_value, settings_read

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsWrite(BaseModel):
    model_config = ConfigDict(extra="allow")


@router.get("")
def read_settings(db: Session = Depends(get_db)):
    return settings_read(db)


@router.post("")
def update_settings(payload: SettingsWrite, db: Session = Depends(get_db)):
    return set_settings(db, payload.model_dump(exclude_unset=True))


async def _verify_email_provider(db: Session):
    user = get_value(db, "gmail_user")
    password = get_secret(db, "gmail_app_password")
    adapter = GmailAdapter.from_settings(db)
    transport = get_value(db, "email_transport", "smtp")
    try:
        readiness = await adapter.verify(user, password)
    except Exception as exc:
        readiness = "failed"
        emit_event(db, "sender.smtp_failed", payload={"transport": transport, "error_detail": redacted_error(exc)})
    if readiness in {"smtp_verified", "provider_verified"}:
        set_value(db, "sender_readiness", readiness)
        emit_event(db, "sender.smtp_verified", payload={"gmail_user": user, "transport": transport})
        db.commit()
        return {"readiness": readiness}
    set_value(db, "sender_readiness", "failed")
    emit_event(db, "sender.smtp_failed", payload={"transport": transport, "error_detail": "Email provider verification failed"})
    db.commit()
    return {
        "readiness": "failed",
        "error_code": "email_provider_verification_failed",
        "error_detail": "Email provider verification failed",
    }


@router.post("/verify-email")
async def verify_email(db: Session = Depends(get_db)):
    return await _verify_email_provider(db)


@router.post("/verify-smtp")
async def verify_smtp(db: Session = Depends(get_db)):
    return await _verify_email_provider(db)
