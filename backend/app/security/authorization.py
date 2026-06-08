from __future__ import annotations

import inspect
import os
import secrets
from ipaddress import ip_address
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

import jwt
from fastapi import HTTPException, Request
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError


AUTH_NOT_CONFIGURED = "production_identity_architecture_not_configured"
AUTH_HEADER_MISSING = "authentication_required"
AUTH_INVALID = "authentication_invalid"
AUTH_FORBIDDEN = "authorization_required"
AUTH_INTERACTIVE_NOT_CONFIGURED = "interactive_authentication_not_configured"
AUTH_LOCAL_ONLY = "direct_use_loopback_required"


def _csv_set(name: str) -> set[str]:
    return {value.strip() for value in os.getenv(name, "").split(",") if value.strip()}


def authentication_enabled() -> bool:
    environment = os.getenv("FINIMATIC_ENVIRONMENT", "").strip().lower()
    if environment != "development":
        return True
    configured = os.getenv("FINIMATIC_AUTH_ENABLED")
    return configured is None or configured.strip().lower() not in {"0", "false", "no", "off"}


def _is_loopback_request(request: Request) -> bool:
    host = (request.client.host if request.client else "").strip().lower()
    if host == "testclient":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _validate_production_identity_url(value: str, *, name: str) -> None:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise RuntimeError(f"invalid production {name}")
    if host in {"localhost"} or host.endswith((".localhost", ".local", ".test")):
        raise RuntimeError(f"invalid production {name}")
    try:
        address = ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise RuntimeError(f"invalid production {name}")


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: tuple[str, ...]
    session_id: str
    issuer: str
    audience: str
    authenticated: bool = True
    authorized: bool = True
    principal_type: str = "user"
    auth_method: str = "bearer"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OIDCSettings:
    issuer: str
    audience: str
    jwks_url: str
    operator_subjects: frozenset[str]
    admin_subjects: frozenset[str]
    algorithms: tuple[str, ...] = ("ES256", "RS256")

    @classmethod
    def from_environment(cls) -> "OIDCSettings | None":
        issuer = os.getenv("SUPABASE_AUTH_ISSUER", "").strip().rstrip("/")
        audience = os.getenv("SUPABASE_AUTH_AUDIENCE", "authenticated").strip()
        jwks_url = os.getenv("SUPABASE_AUTH_JWKS_URL", "").strip()
        operators = _csv_set("FINIMATIC_AUTH_OPERATOR_SUBJECTS")
        admins = _csv_set("FINIMATIC_AUTH_ADMIN_SUBJECTS")
        configured = any((issuer, jwks_url, operators, admins))
        if not configured:
            return None
        if not issuer or not audience or not jwks_url or not (operators or admins):
            raise RuntimeError("incomplete production authentication configuration")
        environment = os.getenv("FINIMATIC_ENVIRONMENT", "production").strip().lower()
        if environment not in {"development", "test"}:
            _validate_production_identity_url(issuer, name="issuer")
            _validate_production_identity_url(jwks_url, name="jwks url")
        algorithms = tuple(
            algorithm.strip()
            for algorithm in os.getenv("FINIMATIC_AUTH_ALGORITHMS", "ES256,RS256").split(",")
            if algorithm.strip()
        )
        if not algorithms or not set(algorithms).issubset({"ES256", "RS256"}):
            raise RuntimeError("unsupported production authentication algorithm")
        return cls(
            issuer=issuer,
            audience=audience,
            jwks_url=jwks_url,
            operator_subjects=frozenset(operators | admins),
            admin_subjects=frozenset(admins),
            algorithms=algorithms,
        )


class OIDCAuthorizationChecker:
    def __init__(
        self,
        settings: OIDCSettings,
        *,
        jwks_client: PyJWKClient | None = None,
    ):
        self.settings = settings
        self.jwks_client = jwks_client or PyJWKClient(
            settings.jwks_url,
            cache_keys=True,
            lifespan=300,
        )

    def __call__(self, request: Request) -> dict[str, Any]:
        authorization = request.headers.get("authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(status_code=401, detail=AUTH_HEADER_MISSING)
        claims = self.verify_token(token, require_api_claims=True)
        return self.principal_from_claims(
            claims,
            session_id=str(claims.get("session_id") or "").strip(),
            auth_method="bearer",
        )

    def verify_token(
        self,
        token: str,
        *,
        expected_nonce: str | None = None,
        expected_audience: str | None = None,
        require_api_claims: bool,
    ) -> dict[str, Any]:
        audience = expected_audience or self.settings.audience
        try:
            signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            required = ["exp", "iat", "iss", "aud", "sub"]
            if require_api_claims:
                required.extend(["role", "session_id"])
            if expected_nonce is not None:
                required.append("nonce")
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self.settings.algorithms),
                audience=audience,
                issuer=self.settings.issuer,
                options={"require": required},
            )
        except (PyJWTError, ValueError) as exc:
            raise HTTPException(status_code=401, detail=AUTH_INVALID) from exc

        if expected_nonce is not None and not secrets.compare_digest(
            str(claims.get("nonce") or ""),
            expected_nonce,
        ):
            raise HTTPException(status_code=401, detail=AUTH_INVALID)
        token_audience = claims.get("aud")
        authorized_party = claims.get("azp")
        if isinstance(token_audience, list) and len(token_audience) > 1 and authorized_party is None:
            raise HTTPException(status_code=401, detail=AUTH_INVALID)
        if authorized_party is not None and not secrets.compare_digest(
            str(authorized_party),
            audience,
        ):
            raise HTTPException(status_code=401, detail=AUTH_INVALID)
        if require_api_claims:
            session_id = str(claims.get("session_id") or "").strip()
            token_role = str(claims.get("role") or "").strip()
            if not session_id or token_role != "authenticated":
                raise HTTPException(status_code=401, detail=AUTH_INVALID)
        return claims

    def principal_from_claims(
        self,
        claims: Mapping[str, Any],
        *,
        session_id: str,
        auth_method: str,
    ) -> dict[str, Any]:
        subject = str(claims.get("sub") or "").strip()
        if not subject or not session_id:
            raise HTTPException(status_code=401, detail=AUTH_INVALID)
        if claims.get("is_anonymous") is True:
            raise HTTPException(status_code=403, detail=AUTH_FORBIDDEN)

        roles: list[str] = []
        if subject in self.settings.operator_subjects:
            roles.append("operator")
        if subject in self.settings.admin_subjects:
            roles.append("admin")
        if not roles:
            raise HTTPException(status_code=403, detail=AUTH_FORBIDDEN)
        return Principal(
            subject=subject,
            roles=tuple(roles),
            session_id=session_id,
            issuer=self.settings.issuer,
            audience=self.settings.audience,
            auth_method=auth_method,
        ).to_dict()


def configured_authorization_checker() -> Callable[[Request], Any] | None:
    if not authentication_enabled():
        return None
    settings = OIDCSettings.from_environment()
    return OIDCAuthorizationChecker(settings) if settings else None


def system_principal(capability: str) -> Principal:
    return Principal(
        subject=f"system:{capability}",
        roles=("system",),
        session_id="in-process",
        issuer="finimatic",
        audience="internal",
        principal_type="system",
    )


async def _resolve_principal(request: Request) -> Mapping[str, Any]:
    if not authentication_enabled():
        if not _is_loopback_request(request):
            raise HTTPException(status_code=403, detail=AUTH_LOCAL_ONLY)
        if request.headers.get("authorization", "").strip():
            raise HTTPException(status_code=401, detail=AUTH_INVALID)
        return Principal(
            subject="local-user",
            roles=("operator", "admin"),
            session_id="direct-use",
            issuer="finimatic",
            audience="local",
            auth_method="disabled",
        ).to_dict()
    checker = getattr(request.app.state, "authorization_checker", None)
    if checker is None:
        raise HTTPException(status_code=503, detail=AUTH_NOT_CONFIGURED)
    result: Mapping[str, Any] | None = None
    if not request.headers.get("authorization", "").strip():
        from app.security.browser_session import resolve_browser_session

        result = resolve_browser_session(request)
    if result is None:
        result = checker(request)
        if inspect.isawaitable(result):
            result = await result
    if not isinstance(result, Mapping) or result.get("authenticated") is not True:
        raise HTTPException(status_code=401, detail=AUTH_HEADER_MISSING)
    if result.get("authorized") is not True:
        raise HTTPException(status_code=403, detail=AUTH_FORBIDDEN)
    if not str(result.get("subject") or result.get("sub") or "").strip():
        raise HTTPException(status_code=401, detail=AUTH_INVALID)
    if result.get("auth_method") == "session":
        from app.security.browser_session import enforce_session_csrf

        enforce_session_csrf(request)
    return result


async def require_operational_access(request: Request) -> Mapping[str, Any]:
    principal = await _resolve_principal(request)
    roles = {str(role) for role in principal.get("roles", ())}
    if not roles.intersection({"operator", "admin", "system"}):
        raise HTTPException(status_code=403, detail=AUTH_FORBIDDEN)
    request.state.principal = principal
    return principal


async def require_admin_access(request: Request) -> Mapping[str, Any]:
    principal = await _resolve_principal(request)
    roles = {str(role) for role in principal.get("roles", ())}
    if "admin" not in roles:
        raise HTTPException(status_code=403, detail=AUTH_FORBIDDEN)
    request.state.principal = principal
    return principal


def security_status(request: Request) -> dict[str, Any]:
    if not authentication_enabled():
        return {
            "checker_configured": False,
            "authentication_enforced": False,
            "authorization_enforced": False,
            "interactive_login_configured": False,
            "session_authentication_enabled": False,
            "mode": "single_user_local",
            "release_blocked": False,
            "release_block_reason": None,
        }
    checker_configured = getattr(request.app.state, "authorization_checker", None) is not None
    interactive_configured = getattr(request.app.state, "interactive_oidc_settings", None) is not None
    release_blocked = not checker_configured or not interactive_configured
    if not checker_configured:
        release_block_reason = AUTH_NOT_CONFIGURED
    elif not interactive_configured:
        release_block_reason = AUTH_INTERACTIVE_NOT_CONFIGURED
    else:
        release_block_reason = None
    return {
        "checker_configured": checker_configured,
        "authentication_enforced": checker_configured,
        "authorization_enforced": checker_configured,
        "interactive_login_configured": interactive_configured,
        "session_authentication_enabled": interactive_configured,
        "mode": (
            "oidc_bff_session_and_jwks_bearer"
            if checker_configured and interactive_configured
            else "oidc_jwks_bearer"
            if checker_configured
            else "fail_closed_unconfigured"
        ),
        "release_blocked": release_blocked,
        "release_block_reason": release_block_reason,
    }
