from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.audit.service import emit_event
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.time import utcnow
from app.db.models import AuthLoginTransaction, OperatorSession, new_id
from app.db.session import SessionLocal
from app.security.authorization import (
    AUTH_FORBIDDEN,
    AUTH_HEADER_MISSING,
    AUTH_INTERACTIVE_NOT_CONFIGURED,
    AUTH_INVALID,
    OIDCAuthorizationChecker,
    _validate_production_identity_url,
)


SESSION_COOKIE = "finimatic_session"
PRODUCTION_SESSION_COOKIE = "__Host-finimatic_session"
FLOW_COOKIE = "finimatic_auth_flow"
CSRF_HEADER = "x-finimatic-csrf"
CSRF_HEADER_VALUE = "1"
FLOW_TTL_MINUTES = 10

router = APIRouter(prefix="/api/auth", tags=["authentication"])


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _origin(value: str) -> str:
    return value.strip().rstrip("/")


@dataclass(frozen=True)
class InteractiveOIDCSettings:
    authorization_url: str
    token_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    frontend_url: str
    cookie_secure: bool
    session_ttl_seconds: int
    allowed_origins: frozenset[str]

    @classmethod
    def from_environment(cls) -> "InteractiveOIDCSettings | None":
        values = {
            "authorization_url": os.getenv("FINIMATIC_AUTH_AUTHORIZATION_URL", "").strip(),
            "token_url": os.getenv("FINIMATIC_AUTH_TOKEN_URL", "").strip(),
            "client_id": os.getenv("FINIMATIC_AUTH_CLIENT_ID", "").strip(),
            "redirect_uri": os.getenv("FINIMATIC_AUTH_REDIRECT_URI", "").strip(),
            "frontend_url": os.getenv("FRONTEND_URL", "").strip(),
        }
        configured = any(values.values()) or bool(os.getenv("FINIMATIC_AUTH_CLIENT_SECRET"))
        if not configured:
            return None
        if any(not value for value in values.values()):
            raise RuntimeError("incomplete interactive authentication configuration")

        environment = os.getenv("FINIMATIC_ENVIRONMENT", "production").strip().lower()
        cookie_secure = _env_bool("FINIMATIC_AUTH_COOKIE_SECURE", True)
        local_frontend = values["frontend_url"].startswith(("http://127.0.0.1", "http://localhost"))
        test_frontend = environment == "test" and values["frontend_url"].startswith("http://testserver")
        if not cookie_secure and (
            environment not in {"development", "test"} or not (local_frontend or test_frontend)
        ):
            raise RuntimeError("insecure authentication cookies are restricted to loopback development")

        ttl = int(os.getenv("FINIMATIC_AUTH_SESSION_TTL_SECONDS", "3600"))
        if ttl < 60 or ttl > 86400:
            raise RuntimeError("invalid authentication session ttl")
        if environment not in {"development", "test"}:
            for name in ("authorization_url", "token_url", "redirect_uri", "frontend_url"):
                _validate_production_identity_url(values[name], name=name.replace("_", " "))
            if not os.getenv("FINIMATIC_AUTH_CLIENT_SECRET", "").strip():
                raise RuntimeError("production interactive authentication requires a client secret")
            redirect = urlparse(values["redirect_uri"])
            frontend = urlparse(values["frontend_url"])
            if (redirect.scheme, redirect.netloc) != (frontend.scheme, frontend.netloc):
                raise RuntimeError("authentication redirect must use the frontend origin")
        origins = {
            _origin(values["frontend_url"]),
            *{
                _origin(value)
                for value in os.getenv("ALLOWED_ORIGINS", "").split(",")
                if value.strip()
            },
        }
        return cls(
            **values,
            client_secret=os.getenv("FINIMATIC_AUTH_CLIENT_SECRET", "").strip(),
            cookie_secure=cookie_secure,
            session_ttl_seconds=ttl,
            allowed_origins=frozenset(origins),
        )


def configured_interactive_oidc_settings() -> InteractiveOIDCSettings | None:
    return InteractiveOIDCSettings.from_environment()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raise TypeError("expected datetime")


def _safe_return_path(value: str | None) -> str:
    path = (value or "/").strip()
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        return "/"
    return path


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _settings(request: Request) -> InteractiveOIDCSettings:
    settings = getattr(request.app.state, "interactive_oidc_settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail=AUTH_INTERACTIVE_NOT_CONFIGURED)
    return settings


def _session_cookie_name(settings: InteractiveOIDCSettings) -> str:
    return PRODUCTION_SESSION_COOKIE if settings.cookie_secure else SESSION_COOKIE


def resolve_browser_session(request: Request) -> dict[str, Any] | None:
    settings = getattr(request.app.state, "interactive_oidc_settings", None)
    cookie_name = _session_cookie_name(settings) if settings is not None else SESSION_COOKIE
    raw_token = request.cookies.get(cookie_name, "").strip()
    if not raw_token:
        return None
    now = utcnow()
    with SessionLocal() as db:
        row = (
            db.query(OperatorSession)
            .filter(OperatorSession.session_token_hash == _digest(raw_token))
            .first()
        )
        if row is None or row.revoked_at is not None or _aware(row.expires_at) <= now:
            return None
        checker = getattr(request.app.state, "oidc_authorization_checker", None)
        if not isinstance(checker, OIDCAuthorizationChecker) or row.issuer != checker.settings.issuer:
            return None
        roles: list[str] = []
        if row.subject in checker.settings.operator_subjects:
            roles.append("operator")
        if row.subject in checker.settings.admin_subjects:
            roles.append("admin")
        if not roles:
            row.revoked_at = now
            db.commit()
            return None
        roles_tuple = tuple(roles)
        serialized_roles = json.dumps(roles)
        if row.roles_json != serialized_roles:
            row.roles_json = serialized_roles
        row.last_seen_at = now
        db.commit()
        return {
            "subject": row.subject,
            "roles": roles_tuple,
            "session_id": row.id,
            "issuer": row.issuer,
            "audience": row.audience,
            "authenticated": True,
            "authorized": True,
            "principal_type": "user",
            "auth_method": "session",
        }


def enforce_session_csrf(request: Request) -> None:
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return
    settings = _settings(request)
    origin = _origin(request.headers.get("origin", ""))
    if origin not in settings.allowed_origins:
        raise HTTPException(status_code=403, detail="csrf_origin_rejected")
    if request.headers.get(CSRF_HEADER, "") != CSRF_HEADER_VALUE:
        raise HTTPException(status_code=403, detail="csrf_header_required")


def session_status_payload(request: Request) -> dict[str, Any]:
    principal = resolve_browser_session(request)
    settings = getattr(request.app.state, "interactive_oidc_settings", None)
    if principal is None:
        return {
            "authenticated": False,
            "authorized": False,
            "interactive_login_configured": settings is not None,
            "subject": None,
            "roles": [],
        }
    return {
        "authenticated": True,
        "authorized": True,
        "interactive_login_configured": settings is not None,
        "subject": principal["subject"],
        "roles": list(principal["roles"]),
    }


async def exchange_authorization_code(
    settings: InteractiveOIDCSettings,
    *,
    code: str,
    code_verifier: str,
) -> dict[str, Any]:
    form = {
        "grant_type": "authorization_code",
        "client_id": settings.client_id,
        "code": code,
        "redirect_uri": settings.redirect_uri,
        "code_verifier": code_verifier,
    }
    if settings.client_secret:
        form["client_secret"] = settings.client_secret
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(settings.token_url, data=form)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=401, detail=AUTH_INVALID) from exc
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail=AUTH_INVALID)
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=AUTH_INVALID) from exc
    if not isinstance(payload, dict) or not str(payload.get("id_token") or "").strip():
        raise HTTPException(status_code=401, detail=AUTH_INVALID)
    return payload


@router.get("/session")
def get_session(request: Request):
    return JSONResponse(
        session_status_payload(request),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/login")
def start_login(request: Request, return_path: str = "/"):
    settings = _settings(request)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    flow_token = secrets.token_urlsafe(32)
    with SessionLocal() as db:
        db.add(
            AuthLoginTransaction(
                flow_token_hash=_digest(flow_token),
                state_hash=_digest(state),
                nonce=nonce,
                code_verifier_encrypted=encrypt_secret(verifier),
                return_path=_safe_return_path(return_path),
                expires_at=utcnow() + timedelta(minutes=FLOW_TTL_MINUTES),
            )
        )
        db.commit()

    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.client_id,
            "redirect_uri": settings.redirect_uri,
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,
            "code_challenge": _code_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(f"{settings.authorization_url}?{query}", status_code=302)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        FLOW_COOKIE,
        flow_token,
        max_age=FLOW_TTL_MINUTES * 60,
        path="/api/auth/callback",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/callback")
async def complete_login(request: Request, code: str = "", state: str = "", error: str = ""):
    settings = _settings(request)
    if error or not code.strip() or not state.strip():
        raise HTTPException(status_code=401, detail=AUTH_INVALID)
    flow_token = request.cookies.get(FLOW_COOKIE, "").strip()
    if not flow_token:
        raise HTTPException(status_code=401, detail=AUTH_INVALID)

    with SessionLocal() as db:
        flow = (
            db.query(AuthLoginTransaction)
            .filter(
                AuthLoginTransaction.state_hash == _digest(state),
                AuthLoginTransaction.flow_token_hash == _digest(flow_token),
            )
            .first()
        )
        now = utcnow()
        if flow is None or flow.consumed_at is not None or _aware(flow.expires_at) <= now:
            raise HTTPException(status_code=401, detail=AUTH_INVALID)
        claimed = (
            db.query(AuthLoginTransaction)
            .filter(
                AuthLoginTransaction.id == flow.id,
                AuthLoginTransaction.consumed_at.is_(None),
                AuthLoginTransaction.expires_at > now,
            )
            .update({AuthLoginTransaction.consumed_at: now}, synchronize_session=False)
        )
        if claimed != 1:
            db.rollback()
            raise HTTPException(status_code=401, detail=AUTH_INVALID)
        code_verifier = decrypt_secret(flow.code_verifier_encrypted)
        nonce = flow.nonce
        return_path = flow.return_path
        db.commit()

    token_payload = await exchange_authorization_code(
        settings,
        code=code.strip(),
        code_verifier=code_verifier,
    )
    checker = getattr(request.app.state, "oidc_authorization_checker", None)
    if not isinstance(checker, OIDCAuthorizationChecker):
        raise HTTPException(status_code=503, detail=AUTH_INTERACTIVE_NOT_CONFIGURED)
    claims = checker.verify_token(
        str(token_payload["id_token"]),
        expected_nonce=nonce,
        expected_audience=settings.client_id,
        require_api_claims=False,
    )

    session_id = new_id()
    principal = checker.principal_from_claims(
        claims,
        session_id=session_id,
        auth_method="session",
    )
    now = utcnow()
    token_expiry = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
    expires_at = min(now + timedelta(seconds=settings.session_ttl_seconds), token_expiry)
    if expires_at <= now:
        raise HTTPException(status_code=401, detail=AUTH_INVALID)
    session_token = secrets.token_urlsafe(32)
    with SessionLocal() as db:
        db.add(
            OperatorSession(
                id=session_id,
                session_token_hash=_digest(session_token),
                subject=str(principal["subject"]),
                roles_json=json.dumps(list(principal["roles"])),
                issuer=checker.settings.issuer,
                audience=checker.settings.audience,
                expires_at=expires_at,
                last_seen_at=now,
            )
        )
        emit_event(
            db,
            "auth.login.success",
            entity_type="operator_session",
            entity_id=session_id,
            actor=str(principal["subject"]),
            payload={"roles": list(principal["roles"]), "auth_method": "oidc_code_pkce"},
        )
        db.commit()

    response = RedirectResponse(
        f"{settings.frontend_url.rstrip('/')}{_safe_return_path(return_path)}",
        status_code=302,
    )
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(FLOW_COOKIE, path="/api/auth/callback")
    response.set_cookie(
        _session_cookie_name(settings),
        session_token,
        max_age=max(1, int((expires_at - now).total_seconds())),
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
def logout(request: Request):
    principal = resolve_browser_session(request)
    if principal is None:
        raise HTTPException(status_code=401, detail=AUTH_HEADER_MISSING)
    enforce_session_csrf(request)
    with SessionLocal() as db:
        row = db.get(OperatorSession, str(principal["session_id"]))
        if row is None or row.revoked_at is not None:
            raise HTTPException(status_code=401, detail=AUTH_HEADER_MISSING)
        row.revoked_at = utcnow()
        emit_event(
            db,
            "auth.logout",
            entity_type="operator_session",
            entity_id=row.id,
            actor=row.subject,
            payload={"auth_method": "session"},
        )
        db.commit()
    response = JSONResponse({"authenticated": False})
    response.headers["Cache-Control"] = "no-store"
    settings = _settings(request)
    response.delete_cookie(
        _session_cookie_name(settings),
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response
