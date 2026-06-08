from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping

from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.models import Setting


OAUTH_STATE_TTL_SECONDS = 10 * 60
OAUTH_STATE_PREFIX = "security.oauth_state."


@dataclass(frozen=True)
class OAuthStateRecord:
    subject: str
    session_id: str
    issuer: str
    return_url: str
    issued_at: int
    expires_at: int
    status: str = "issued"
    consumed_at: int | None = None


def _state_key(state: str) -> str:
    digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
    return f"{OAUTH_STATE_PREFIX}{digest}"


def _encode_record(record: OAuthStateRecord) -> str:
    payload = json.dumps(asdict(record), separators=(",", ":"), sort_keys=True)
    return encrypt_secret(payload)


def _decode_record(value: str | None) -> OAuthStateRecord:
    try:
        payload = json.loads(decrypt_secret(value))
        return OAuthStateRecord(
            subject=str(payload["subject"]),
            session_id=str(payload["session_id"]),
            issuer=str(payload["issuer"]),
            return_url=str(payload["return_url"]),
            issued_at=int(payload["issued_at"]),
            expires_at=int(payload["expires_at"]),
            status=str(payload["status"]),
            consumed_at=int(payload["consumed_at"]) if payload.get("consumed_at") is not None else None,
        )
    except (InvalidToken, KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid_oauth_state") from exc


def _principal_value(principal: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = str(principal.get(name) or "").strip()
        if value:
            return value
    return ""


def issue_oauth_state(
    db: Session,
    *,
    principal: Mapping[str, Any],
    return_url: str,
    now: int | None = None,
) -> str:
    subject = _principal_value(principal, "subject", "sub")
    session_id = _principal_value(principal, "session_id")
    issuer = _principal_value(principal, "issuer") or "configured_oidc"
    if not subject or not session_id:
        raise HTTPException(status_code=401, detail="authentication_invalid")

    issued_at = int(time.time()) if now is None else int(now)
    state = secrets.token_urlsafe(48)
    record = OAuthStateRecord(
        subject=subject,
        session_id=session_id,
        issuer=issuer,
        return_url=return_url,
        issued_at=issued_at,
        expires_at=issued_at + OAUTH_STATE_TTL_SECONDS,
    )
    db.add(Setting(key=_state_key(state), value=_encode_record(record)))
    db.flush()
    return state


def _assert_binding(record: OAuthStateRecord, principal: Mapping[str, Any] | None) -> None:
    if principal is None:
        return
    subject = _principal_value(principal, "subject", "sub")
    session_id = _principal_value(principal, "session_id")
    issuer = _principal_value(principal, "issuer") or record.issuer
    if (
        not subject
        or not session_id
        or subject != record.subject
        or session_id != record.session_id
        or issuer != record.issuer
    ):
        raise HTTPException(status_code=400, detail="oauth_state_session_mismatch")


def consume_oauth_state(
    db: Session,
    *,
    state: str,
    principal: Mapping[str, Any] | None = None,
    now: int | None = None,
) -> OAuthStateRecord:
    if not state or len(state) < 32 or len(state) > 512:
        raise HTTPException(status_code=400, detail="invalid_oauth_state")

    key = _state_key(state)
    row = db.query(Setting).filter(Setting.key == key).first()
    if row is None:
        raise HTTPException(status_code=400, detail="invalid_oauth_state")

    original_value = row.value or ""
    record = _decode_record(original_value)
    if record.status != "issued":
        raise HTTPException(status_code=400, detail="replayed_oauth_state")

    current_time = int(time.time()) if now is None else int(now)
    if current_time > record.expires_at:
        expired = replace(record, status="expired", consumed_at=current_time)
        db.execute(
            update(Setting)
            .where(Setting.key == key, Setting.value == original_value)
            .values(value=_encode_record(expired))
        )
        db.commit()
        raise HTTPException(status_code=400, detail="expired_oauth_state")

    _assert_binding(record, principal)
    consumed = replace(record, status="consumed", consumed_at=current_time)
    result = db.execute(
        update(Setting)
        .where(Setting.key == key, Setting.value == original_value)
        .values(value=_encode_record(consumed))
    )
    if result.rowcount != 1:
        db.rollback()
        current = db.query(Setting).filter(Setting.key == key).first()
        if current is not None and _decode_record(current.value).status != "issued":
            raise HTTPException(status_code=400, detail="replayed_oauth_state")
        raise HTTPException(status_code=400, detail="invalid_oauth_state")
    db.commit()
    return record
