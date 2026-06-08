from __future__ import annotations

import os
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.core.crypto import redacted_error
from app.db.session import get_db
from app.security.authorization import security_status
from app.security.oauth_state import consume_oauth_state, issue_oauth_state
from app.send.smtp_adapter import GmailAdapter
from app.settings.service import get_bool, get_secret, get_value, set_settings, set_value, settings_read

router = APIRouter(prefix="/api/settings", tags=["settings"])

GMAIL_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_SCOPES = (
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.metadata",
)


class SettingsWrite(BaseModel):
    model_config = ConfigDict(extra="allow")


class GmailApiOAuthStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    return_url: str | None = None


@router.get("")
def read_settings(request: Request, db: Session = Depends(get_db)):
    return _with_security_status(request, settings_read(db))


@router.post("")
def update_settings(payload: SettingsWrite, request: Request, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True)
    if os.getenv("FINIMATIC_HOSTING_MODE", "").strip().lower() == "render_free" and data.get("email_transport") == "smtp":
        raise HTTPException(status_code=422, detail="hosted_transport_requires_gmail_api")
    enabled = bool(data.get("auto_reply_enabled", get_bool(db, "auto_reply_enabled")))
    mode = str(data.get("auto_reply_mode", get_value(db, "auto_reply_mode", "propose")))
    if enabled and mode == "autonomous":
        if get_bool(db, "auto_reply_kill_switch") or not get_bool(db, "auto_reply_autonomous_authorized"):
            raise HTTPException(status_code=409, detail="autonomous_confirmation_required")
    if data.get("auto_reply_enabled") is False:
        set_value(db, "auto_reply_autonomous_authorized", "false")
        set_value(db, "auto_reply_kill_switch", "true")
    if data.get("auto_reply_mode") == "propose":
        set_value(db, "auto_reply_autonomous_authorized", "false")
        set_value(db, "auto_reply_kill_switch", "true")
    return _with_security_status(request, set_settings(db, data))


def _with_security_status(request: Request, settings: dict[str, Any]) -> dict[str, Any]:
    status = security_status(request)
    return {
        **settings,
        "api_security_mode": status["mode"],
        "api_security_enforced": status["authentication_enforced"] and status["authorization_enforced"],
        "release_blocked": status["release_blocked"],
        "release_block_reason": status["release_block_reason"],
    }


def _callback_url(request: Request) -> str:
    configured = os.getenv("GMAIL_OAUTH_REDIRECT_URI", "").strip()
    if configured:
        return configured
    return str(request.url_for("gmail_api_oauth_callback"))


def _allowed_return_origins() -> set[str]:
    origins = {
        origin.strip().rstrip("/")
        for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    }
    frontend_url = os.getenv("FRONTEND_URL", "").strip().rstrip("/")
    if frontend_url:
        parsed = urlparse(frontend_url)
        if parsed.scheme and parsed.netloc:
            origins.add(f"{parsed.scheme}://{parsed.netloc}")
    origins.update(
        {
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "https://finimatic-frontend.vercel.app",
        }
    )
    return origins


def _default_return_url() -> str:
    frontend_url = os.getenv("FRONTEND_URL", "").strip()
    if frontend_url:
        return frontend_url
    allowed = sorted(_allowed_return_origins())
    return allowed[0] if allowed else "https://finimatic-frontend.vercel.app"


def _safe_return_url(candidate: str | None) -> str:
    if not candidate:
        return _default_return_url()
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _default_return_url()
    origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    if origin not in _allowed_return_origins():
        return _default_return_url()
    return candidate


def _append_query(url: str, values: dict[str, str]) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(values)
    return urlunparse(parsed._replace(query=urlencode(query)))


async def _exchange_oauth_code(*, client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            GMAIL_OAUTH_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        response.raise_for_status()
        return response.json()


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
    if readiness in {"configured", "smtp_verified", "provider_verified"}:
        set_value(db, "sender_readiness", readiness)
        event_type = "sender.transport_simulated" if readiness == "configured" else "sender.smtp_verified"
        emit_event(db, event_type, payload={"gmail_user": user, "transport": transport})
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


@router.post("/gmail-api/oauth/start")
def start_gmail_api_oauth(payload: GmailApiOAuthStart, request: Request, db: Session = Depends(get_db)):
    client_id = get_secret(db, "gmail_api_client_id")
    client_secret = get_secret(db, "gmail_api_client_secret")
    gmail_user = get_value(db, "gmail_user")
    if not gmail_user:
        raise HTTPException(status_code=400, detail="gmail_user_required")
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="gmail_api_client_credentials_required")

    redirect_uri = _callback_url(request)
    return_url = _safe_return_url(payload.return_url)
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, dict):
        raise HTTPException(status_code=401, detail="authentication_invalid")
    state = issue_oauth_state(db, principal=principal, return_url=return_url)
    authorization_params = {
        "access_type": "offline",
        "client_id": client_id,
        "include_granted_scopes": "true",
        "prompt": "consent",
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GMAIL_API_SCOPES),
        "state": state,
    }
    authorization_url = f"{GMAIL_OAUTH_AUTHORIZE_URL}?{urlencode(authorization_params)}"
    emit_event(
        db,
        "gmail_api.oauth_start",
        payload={"redirect_uri": redirect_uri, "scopes": list(GMAIL_API_SCOPES), "gmail_user": gmail_user},
    )
    db.commit()
    return {"authorization_url": authorization_url, "redirect_uri": redirect_uri, "scopes": list(GMAIL_API_SCOPES)}


@router.get("/gmail-api/oauth/callback", name="gmail_api_oauth_callback")
async def gmail_api_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if not state:
        raise HTTPException(status_code=400, detail="missing_oauth_state")
    callback_principal = getattr(request.state, "principal", None)
    if not isinstance(callback_principal, dict):
        raise HTTPException(status_code=401, detail="authentication_invalid")
    state_record = consume_oauth_state(db, state=state, principal=callback_principal)
    return_url = _safe_return_url(state_record.return_url)
    actor = state_record.subject
    if error:
        emit_event(db, "gmail_api.oauth_failed", actor=actor, payload={"error": error})
        db.commit()
        return RedirectResponse(_append_query(return_url, {"gmail_api_oauth": "failed"}), status_code=303)
    if not code:
        emit_event(db, "gmail_api.oauth_failed", actor=actor, payload={"error": "missing_code"})
        db.commit()
        raise HTTPException(status_code=400, detail="missing_oauth_code")

    client_id = get_secret(db, "gmail_api_client_id")
    client_secret = get_secret(db, "gmail_api_client_secret")
    if not client_id or not client_secret:
        emit_event(db, "gmail_api.oauth_failed", actor=actor, payload={"error": "missing_client_credentials"})
        db.commit()
        raise HTTPException(status_code=400, detail="gmail_api_client_credentials_required")

    try:
        token_payload = await _exchange_oauth_code(
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=_callback_url(request),
        )
    except Exception as exc:
        emit_event(db, "gmail_api.oauth_failed", actor=actor, payload={"error_detail": redacted_error(exc)})
        db.commit()
        return RedirectResponse(_append_query(return_url, {"gmail_api_oauth": "failed"}), status_code=303)

    refresh_token = str(token_payload.get("refresh_token") or "")
    if not refresh_token:
        emit_event(db, "gmail_api.oauth_failed", actor=actor, payload={"error": "refresh_token_missing"})
        db.commit()
        return RedirectResponse(_append_query(return_url, {"gmail_api_oauth": "refresh_token_missing"}), status_code=303)

    set_settings(db, {"email_transport": "gmail_api", "gmail_api_refresh_token": refresh_token})
    verify_result = await _verify_email_provider(db)
    emit_event(
        db,
        "gmail_api.oauth_connected",
        actor=actor,
        payload={"readiness": verify_result.get("readiness"), "scope": token_payload.get("scope", "")},
    )
    db.commit()
    status = "connected" if verify_result.get("readiness") == "provider_verified" else "connected_unverified"
    return RedirectResponse(_append_query(return_url, {"gmail_api_oauth": status}), status_code=303)
