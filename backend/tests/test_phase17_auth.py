from __future__ import annotations

import importlib
import importlib.util
import base64
import hashlib
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.security.authorization import AUTH_INVALID, OIDCAuthorizationChecker, OIDCSettings


_provider_spec = importlib.util.spec_from_file_location(
    "phase17_loopback_oidc_provider",
    Path(__file__).with_name("loopback_oidc_provider.py"),
)
assert _provider_spec and _provider_spec.loader
_provider_module = importlib.util.module_from_spec(_provider_spec)
_provider_spec.loader.exec_module(_provider_module)
TEST_CLIENT_ID = _provider_module.TEST_CLIENT_ID
create_loopback_provider = _provider_module.create_loopback_provider


def _bare_app(monkeypatch, tmp_path):
    for name in (
        "SUPABASE_AUTH_ISSUER",
        "SUPABASE_AUTH_JWKS_URL",
        "FINIMATIC_AUTH_OPERATOR_SUBJECTS",
        "FINIMATIC_AUTH_ADMIN_SUBJECTS",
        "FINIMATIC_AUTH_AUTHORIZATION_URL",
        "FINIMATIC_AUTH_TOKEN_URL",
        "FINIMATIC_AUTH_CLIENT_ID",
        "FINIMATIC_AUTH_CLIENT_SECRET",
        "FINIMATIC_AUTH_REDIRECT_URI",
        "FRONTEND_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'phase17-auth.db'}")
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FINIMATIC_DISABLE_SCHEDULER", "1")
    monkeypatch.setenv("FINIMATIC_TEST_SCHEMA_CREATE", "1")
    monkeypatch.setenv("FINIMATIC_AUTH_ENABLED", "1")
    import app.main

    importlib.reload(app.main)
    return app.main.create_app()


def test_direct_use_requires_explicit_development_opt_out(monkeypatch, tmp_path):
    monkeypatch.setenv("FINIMATIC_AUTH_ENABLED", "0")
    monkeypatch.setenv("FINIMATIC_ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'phase17-direct-use.db'}")
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FINIMATIC_DISABLE_SCHEDULER", "1")
    monkeypatch.setenv("FINIMATIC_TEST_SCHEMA_CREATE", "1")
    import app.main

    importlib.reload(app.main)
    application = app.main.create_app()
    with TestClient(application) as client:
        status = client.get("/api/security/status")
        assert status.status_code == 200
        assert status.json()["mode"] == "single_user_local"
        assert status.json()["authentication_enforced"] is False
        assert client.get("/api/contacts").status_code == 200
        assert client.get("/api/settings").status_code == 200
        assert client.get("/api/contacts", headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_direct_use_rejects_non_loopback_clients(monkeypatch, tmp_path):
    monkeypatch.setenv("FINIMATIC_AUTH_ENABLED", "0")
    monkeypatch.setenv("FINIMATIC_ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'phase17-direct-use-remote.db'}")
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FINIMATIC_DISABLE_SCHEDULER", "1")
    monkeypatch.setenv("FINIMATIC_TEST_SCHEMA_CREATE", "1")
    import app.main

    importlib.reload(app.main)
    application = app.main.create_app()
    with TestClient(application, client=("203.0.113.10", 50000)) as client:
        assert client.get("/api/security/status").status_code == 200
        response = client.get("/api/contacts")
        assert response.status_code == 403
        assert response.json()["detail"] == "direct_use_loopback_required"


def test_missing_environment_fails_closed(monkeypatch):
    monkeypatch.delenv("FINIMATIC_ENVIRONMENT", raising=False)
    monkeypatch.setenv("FINIMATIC_AUTH_ENABLED", "0")
    from app.security.authorization import authentication_enabled

    assert authentication_enabled() is True


def test_production_keeps_authentication_enabled(monkeypatch):
    monkeypatch.delenv("FINIMATIC_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("FINIMATIC_ENVIRONMENT", "production")
    from app.security.authorization import authentication_enabled

    assert authentication_enabled() is True


def test_unconfigured_auth_fails_closed_and_health_remains_public(monkeypatch, tmp_path):
    app = _bare_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        status = client.get("/api/security/status")
        assert status.status_code == 200
        assert status.json()["mode"] == "fail_closed_unconfigured"
        assert status.json()["release_blocked"] is True
        assert client.get("/api/contacts").status_code == 503
        assert client.get("/api/settings").status_code == 503
        assert client.post("/api/queue/process").status_code == 503
        callback = client.get(
            "/api/settings/gmail-api/oauth/callback?code=test&state=invalid",
            follow_redirects=False,
        )
        assert callback.status_code == 503
        assert callback.json()["detail"] == "production_identity_architecture_not_configured"


def test_operator_cannot_access_admin_settings_or_canary(monkeypatch, tmp_path):
    app = _bare_app(monkeypatch, tmp_path)
    app.state.authorization_checker = lambda request: {
        "subject": "operator",
        "session_id": "session",
        "roles": ("operator",),
        "authenticated": True,
        "authorized": True,
    }
    with TestClient(app) as client:
        assert client.get("/api/contacts").status_code == 200
        assert client.get("/api/settings").status_code == 403
        assert client.post("/api/canary/send").status_code == 403
        response = client.post(
            "/api/auto-reply/autonomous/prepare",
            json={"session_token": "operator-session"},
        )
        assert response.status_code == 403


def test_admin_retains_operational_and_settings_access(monkeypatch, tmp_path):
    app = _bare_app(monkeypatch, tmp_path)
    app.state.authorization_checker = lambda request: {
        "subject": "admin",
        "session_id": "session",
        "roles": ("operator", "admin"),
        "authenticated": True,
        "authorized": True,
    }
    with TestClient(app) as client:
        assert client.get("/api/contacts").status_code == 200
        settings = client.get("/api/settings")
        assert settings.status_code == 200
        assert settings.json()["api_security_mode"] == "oidc_jwks_bearer"
        assert settings.json()["api_security_enforced"] is True
        assert settings.json()["release_blocked"] is True
        assert settings.json()["release_block_reason"] == "interactive_authentication_not_configured"
        status = client.get("/api/security/status").json()
        assert status["authentication_enforced"] is True
        assert status["authorization_enforced"] is True
        assert status["release_blocked"] is True
        assert status["mode"] == "oidc_jwks_bearer"


def test_malformed_checker_result_is_rejected(monkeypatch, tmp_path):
    app = _bare_app(monkeypatch, tmp_path)
    app.state.authorization_checker = lambda request: {
        "authenticated": True,
        "authorized": True,
        "roles": ("admin",),
    }
    with TestClient(app) as client:
        assert client.get("/api/settings").status_code == 401


class _SigningKey:
    key = "test-public-key"


class _JwksClient:
    def get_signing_key_from_jwt(self, token):
        assert token == "test-token"
        return _SigningKey()


def _oidc_checker():
    settings = OIDCSettings(
        issuer="https://identity.example.test/auth/v1",
        audience="authenticated",
        jwks_url="https://identity.example.test/.well-known/jwks.json",
        operator_subjects=frozenset({"operator-subject", "admin-subject"}),
        admin_subjects=frozenset({"admin-subject"}),
    )
    return OIDCAuthorizationChecker(settings, jwks_client=_JwksClient())


def test_configured_oidc_rejects_missing_bearer(monkeypatch, tmp_path):
    app = _bare_app(monkeypatch, tmp_path)
    app.state.authorization_checker = _oidc_checker()
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        response = client.get("/api/contacts")
        assert response.status_code == 401
        assert response.json()["detail"] == "authentication_required"


@pytest.mark.parametrize(
    ("subject", "expected_roles"),
    [
        ("operator-subject", ("operator",)),
        ("admin-subject", ("operator", "admin")),
    ],
)
def test_oidc_subject_allowlists_assign_expected_roles(monkeypatch, subject, expected_roles):
    claims = {
        "sub": subject,
        "session_id": "session-1",
        "role": "authenticated",
        "iss": "https://identity.example.test/auth/v1",
        "aud": "authenticated",
        "iat": 1,
        "exp": 2,
    }
    monkeypatch.setattr("app.security.authorization.jwt.decode", lambda *args, **kwargs: claims)
    request = type(
        "Request",
        (),
        {"headers": {"authorization": "Bearer test-token"}},
    )()

    principal = _oidc_checker()(request)

    assert principal["subject"] == subject
    assert principal["roles"] == expected_roles


def test_oidc_unknown_subject_is_forbidden(monkeypatch):
    claims = {
        "sub": "unknown-subject",
        "session_id": "session-1",
        "role": "authenticated",
        "iss": "https://identity.example.test/auth/v1",
        "aud": "authenticated",
        "iat": 1,
        "exp": 2,
    }
    monkeypatch.setattr("app.security.authorization.jwt.decode", lambda *args, **kwargs: claims)
    request = type(
        "Request",
        (),
        {"headers": {"authorization": "Bearer test-token"}},
    )()

    with pytest.raises(HTTPException) as exc:
        _oidc_checker()(request)

    assert exc.value.status_code == 403
    assert exc.value.detail == "authorization_required"


class _ActualSigningKey:
    def __init__(self, key):
        self.key = key


class _ActualJwksClient:
    def __init__(self, key):
        self.key = key

    def get_signing_key_from_jwt(self, token):
        return _ActualSigningKey(self.key)


def _interactive_app(monkeypatch, tmp_path, *, operators="operator-subject,admin-subject", admins="admin-subject"):
    issuer = "https://identity.example.test"
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'interactive-auth.db'}")
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FINIMATIC_DISABLE_SCHEDULER", "1")
    monkeypatch.setenv("FINIMATIC_TEST_SCHEMA_CREATE", "1")
    monkeypatch.setenv("FINIMATIC_ENVIRONMENT", "test")
    monkeypatch.setenv("FRONTEND_URL", "http://testserver")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("FINIMATIC_AUTH_COOKIE_SECURE", "0")
    monkeypatch.setenv("SUPABASE_AUTH_ISSUER", issuer)
    monkeypatch.setenv("SUPABASE_AUTH_AUDIENCE", "finimatic-test")
    monkeypatch.setenv("SUPABASE_AUTH_JWKS_URL", f"{issuer}/jwks")
    monkeypatch.setenv("FINIMATIC_AUTH_OPERATOR_SUBJECTS", operators)
    monkeypatch.setenv("FINIMATIC_AUTH_ADMIN_SUBJECTS", admins)
    monkeypatch.setenv("FINIMATIC_AUTH_CLIENT_ID", "finimatic-test-browser")
    monkeypatch.setenv("FINIMATIC_AUTH_AUTHORIZATION_URL", f"{issuer}/authorize")
    monkeypatch.setenv("FINIMATIC_AUTH_TOKEN_URL", f"{issuer}/token")
    monkeypatch.setenv("FINIMATIC_AUTH_REDIRECT_URI", "http://testserver/api/auth/callback")
    import app.main

    importlib.reload(app.main)
    application = app.main.create_app()
    checker = OIDCAuthorizationChecker(
        OIDCSettings.from_environment(),
        jwks_client=_ActualJwksClient(public_key),
    )
    application.state.oidc_authorization_checker = checker
    application.state.authorization_checker = checker
    return application, private_key, issuer


def _id_token(
    private_key,
    issuer,
    *,
    subject="admin-subject",
    nonce,
    audience="finimatic-test-browser",
    expires_in=300,
    azp=None,
):
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "iat": now,
        "exp": now + expires_in,
        "nonce": nonce,
    }
    if azp is not None:
        claims["azp"] = azp
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
    )


def _begin_login(client: TestClient):
    response = client.get("/api/auth/login", follow_redirects=False)
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    return {key: values[0] for key, values in query.items()}


def test_interactive_login_creates_authorized_session_restores_and_logs_out(monkeypatch, tmp_path):
    application, private_key, issuer = _interactive_app(monkeypatch, tmp_path)
    captured: dict[str, str] = {}

    async def exchange(settings, *, code, code_verifier):
        captured["code"] = code
        captured["code_verifier"] = code_verifier
        return {"id_token": _id_token(private_key, issuer, nonce=captured["nonce"])}

    monkeypatch.setattr("app.security.browser_session.exchange_authorization_code", exchange)
    with TestClient(application) as client:
        assert client.get("/api/auth/session").json()["authenticated"] is False
        assert client.get("/api/contacts").status_code == 401
        login = _begin_login(client)
        captured["nonce"] = login["nonce"]
        assert login["code_challenge_method"] == "S256"
        callback = client.get(
            f'/api/auth/callback?code=accepted-code&state={login["state"]}',
            follow_redirects=False,
        )
        assert callback.status_code == 302
        assert "HttpOnly" in callback.headers["set-cookie"]
        assert "SameSite=lax" in callback.headers["set-cookie"]
        assert captured["code"] == "accepted-code"
        expected_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(captured["code_verifier"].encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        assert expected_challenge == login["code_challenge"]

        session = client.get("/api/auth/session")
        assert session.status_code == 200
        assert session.headers["cache-control"] == "no-store"
        assert session.json() == {
            "authenticated": True,
            "authorized": True,
            "interactive_login_configured": True,
            "subject": "admin-subject",
            "roles": ["operator", "admin"],
        }
        assert "token" not in session.text.lower()
        assert client.get("/api/contacts").status_code == 200
        invalid_bearer = client.get(
            "/api/contacts",
            headers={"Authorization": "Bearer invalid"},
        )
        assert invalid_bearer.status_code == 401
        assert client.get("/api/settings").status_code == 200
        assert client.post("/api/queue/process").status_code == 403
        assert client.post(
            "/api/queue/process",
            headers={"Origin": "http://evil.example", "X-Finimatic-CSRF": "1"},
        ).status_code == 403
        assert client.post(
            "/api/queue/process",
            headers={"Origin": "http://testserver", "X-Finimatic-CSRF": "1"},
        ).status_code == 200

        logout = client.post(
            "/api/auth/logout",
            headers={"Origin": "http://testserver", "X-Finimatic-CSRF": "1"},
        )
        assert logout.status_code == 200
        assert "SameSite=lax" in logout.headers["set-cookie"]
        assert "Max-Age=0" in logout.headers["set-cookie"]
        assert client.get("/api/auth/session").json()["authenticated"] is False
        assert client.get("/api/contacts").status_code == 401


def test_interactive_login_state_is_single_use(monkeypatch, tmp_path):
    application, private_key, issuer = _interactive_app(monkeypatch, tmp_path)
    captured: dict[str, str] = {}

    async def exchange(settings, *, code, code_verifier):
        return {"id_token": _id_token(private_key, issuer, nonce=captured["nonce"])}

    monkeypatch.setattr("app.security.browser_session.exchange_authorization_code", exchange)
    with TestClient(application) as client:
        login = _begin_login(client)
        captured["nonce"] = login["nonce"]
        first = client.get(
            f'/api/auth/callback?code=accepted-code&state={login["state"]}',
            follow_redirects=False,
        )
        replay = client.get(
            f'/api/auth/callback?code=accepted-code&state={login["state"]}',
            follow_redirects=False,
        )
        assert first.status_code == 302
        assert replay.status_code == 401
        assert replay.json()["detail"] == AUTH_INVALID


def test_expired_operator_session_fails_closed(monkeypatch, tmp_path):
    application, private_key, issuer = _interactive_app(monkeypatch, tmp_path)
    captured: dict[str, str] = {}

    async def exchange(settings, *, code, code_verifier):
        return {"id_token": _id_token(private_key, issuer, nonce=captured["nonce"])}

    monkeypatch.setattr("app.security.browser_session.exchange_authorization_code", exchange)
    with TestClient(application) as client:
        login = _begin_login(client)
        captured["nonce"] = login["nonce"]
        assert client.get(
            f'/api/auth/callback?code=accepted-code&state={login["state"]}',
            follow_redirects=False,
        ).status_code == 302
        from app.core.time import utcnow
        from app.db.models import OperatorSession
        from app.db.session import SessionLocal

        with SessionLocal() as db:
            row = db.query(OperatorSession).one()
            row.expires_at = utcnow()
            db.commit()
        assert client.get("/api/auth/session").json()["authenticated"] is False
        assert client.get("/api/contacts").status_code == 401


@pytest.mark.parametrize(
    ("subject", "operational_status", "settings_status"),
    [
        ("operator-subject", 200, 403),
        ("unknown-subject", 401, 401),
    ],
)
def test_interactive_subject_authorization_is_server_side(
    monkeypatch,
    tmp_path,
    subject,
    operational_status,
    settings_status,
):
    application, private_key, issuer = _interactive_app(monkeypatch, tmp_path)
    captured: dict[str, str] = {}

    async def exchange(settings, *, code, code_verifier):
        return {"id_token": _id_token(private_key, issuer, subject=subject, nonce=captured["nonce"])}

    monkeypatch.setattr("app.security.browser_session.exchange_authorization_code", exchange)
    with TestClient(application) as client:
        login = _begin_login(client)
        captured["nonce"] = login["nonce"]
        callback = client.get(
            f'/api/auth/callback?code=accepted-code&state={login["state"]}',
            follow_redirects=False,
        )
        assert callback.status_code == (302 if subject != "unknown-subject" else 403)
        assert client.get("/api/contacts").status_code == operational_status
        assert client.get("/api/settings").status_code == settings_status


def test_interactive_login_rejects_invalid_state_and_nonce(monkeypatch, tmp_path):
    application, private_key, issuer = _interactive_app(monkeypatch, tmp_path)

    async def exchange(settings, *, code, code_verifier):
        return {"id_token": _id_token(private_key, issuer, nonce="wrong-nonce")}

    monkeypatch.setattr("app.security.browser_session.exchange_authorization_code", exchange)
    with TestClient(application) as client:
        login = _begin_login(client)
        bad_state = client.get(
            "/api/auth/callback?code=accepted-code&state=wrong-state",
            follow_redirects=False,
        )
        assert bad_state.status_code == 401
        bad_nonce = client.get(
            f'/api/auth/callback?code=accepted-code&state={login["state"]}',
            follow_redirects=False,
        )
        assert bad_nonce.status_code == 401
        assert bad_nonce.json()["detail"] == AUTH_INVALID
        assert client.get("/api/auth/session").json()["authenticated"] is False


@pytest.mark.parametrize("failure", ["issuer", "audience", "signature", "expired"])
def test_oidc_id_token_validation_fails_closed(monkeypatch, tmp_path, failure):
    application, private_key, issuer = _interactive_app(monkeypatch, tmp_path)
    checker = application.state.oidc_authorization_checker
    signing_key = private_key
    token_issuer = issuer
    audience = "finimatic-test-browser"
    expires_in = 300
    if failure == "issuer":
        token_issuer = "https://wrong-issuer.example"
    elif failure == "audience":
        audience = "wrong-audience"
    elif failure == "signature":
        signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    elif failure == "expired":
        expires_in = -60
    token = _id_token(
        signing_key,
        token_issuer,
        nonce="nonce",
        audience=audience,
        expires_in=expires_in,
    )
    with pytest.raises(HTTPException) as exc:
        checker.verify_token(
            token,
            expected_nonce="nonce",
            expected_audience="finimatic-test-browser",
            require_api_claims=False,
        )
    assert exc.value.status_code == 401
    assert exc.value.detail == AUTH_INVALID


def test_oidc_id_token_with_multiple_audiences_requires_matching_azp(monkeypatch, tmp_path):
    application, private_key, issuer = _interactive_app(monkeypatch, tmp_path)
    checker = application.state.oidc_authorization_checker
    missing_azp = _id_token(
        private_key,
        issuer,
        nonce="nonce",
        audience=["finimatic-test-browser", "another-client"],
    )
    with pytest.raises(HTTPException) as exc:
        checker.verify_token(
            missing_azp,
            expected_nonce="nonce",
            expected_audience="finimatic-test-browser",
            require_api_claims=False,
        )
    assert exc.value.detail == AUTH_INVALID

    accepted = _id_token(
        private_key,
        issuer,
        nonce="nonce",
        audience=["finimatic-test-browser", "another-client"],
        azp="finimatic-test-browser",
    )
    claims = checker.verify_token(
        accepted,
        expected_nonce="nonce",
        expected_audience="finimatic-test-browser",
        require_api_claims=False,
    )
    assert claims["sub"] == "admin-subject"


def test_oidc_id_token_rejects_conflicting_single_audience_azp(monkeypatch, tmp_path):
    application, private_key, issuer = _interactive_app(monkeypatch, tmp_path)
    checker = application.state.oidc_authorization_checker
    token = _id_token(
        private_key,
        issuer,
        nonce="nonce",
        audience="finimatic-test-browser",
        azp="another-client",
    )
    with pytest.raises(HTTPException) as exc:
        checker.verify_token(
            token,
            expected_nonce="nonce",
            expected_audience="finimatic-test-browser",
            require_api_claims=False,
        )
    assert exc.value.detail == AUTH_INVALID


def test_active_session_uses_current_subject_allowlists(monkeypatch, tmp_path):
    application, private_key, issuer = _interactive_app(monkeypatch, tmp_path)
    captured: dict[str, str] = {}

    async def exchange(settings, *, code, code_verifier):
        return {"id_token": _id_token(private_key, issuer, nonce=captured["nonce"])}

    monkeypatch.setattr("app.security.browser_session.exchange_authorization_code", exchange)
    with TestClient(application) as client:
        login = _begin_login(client)
        captured["nonce"] = login["nonce"]
        assert client.get(
            f'/api/auth/callback?code=accepted-code&state={login["state"]}',
            follow_redirects=False,
        ).status_code == 302
        assert client.get("/api/contacts").status_code == 200
        checker = application.state.oidc_authorization_checker
        checker.settings = OIDCSettings(
            issuer=checker.settings.issuer,
            audience=checker.settings.audience,
            jwks_url=checker.settings.jwks_url,
            operator_subjects=frozenset(),
            admin_subjects=frozenset(),
        )
        assert client.get("/api/contacts").status_code == 401
        assert client.get("/api/auth/session").json()["authenticated"] is False


def test_production_interactive_auth_rejects_loopback_endpoints(monkeypatch):
    monkeypatch.setenv("FINIMATIC_ENVIRONMENT", "production")
    monkeypatch.setenv("FINIMATIC_AUTH_COOKIE_SECURE", "1")
    monkeypatch.setenv("FRONTEND_URL", "https://finimatic.example")
    monkeypatch.setenv("FINIMATIC_AUTH_CLIENT_ID", "browser-client")
    monkeypatch.setenv("FINIMATIC_AUTH_CLIENT_SECRET", "configured")
    monkeypatch.setenv("FINIMATIC_AUTH_AUTHORIZATION_URL", "http://127.0.0.1:8018/oidc/authorize")
    monkeypatch.setenv("FINIMATIC_AUTH_TOKEN_URL", "http://127.0.0.1:8018/oidc/token")
    monkeypatch.setenv("FINIMATIC_AUTH_REDIRECT_URI", "https://finimatic.example/api/auth/callback")
    from app.security.browser_session import InteractiveOIDCSettings

    with pytest.raises(RuntimeError, match="invalid production authorization url"):
        InteractiveOIDCSettings.from_environment()


def test_unrecognized_environment_uses_production_identity_restrictions(monkeypatch):
    monkeypatch.setenv("FINIMATIC_ENVIRONMENT", "prod")
    monkeypatch.setenv("FINIMATIC_AUTH_COOKIE_SECURE", "1")
    monkeypatch.setenv("FRONTEND_URL", "https://finimatic.example")
    monkeypatch.setenv("FINIMATIC_AUTH_CLIENT_ID", "browser-client")
    monkeypatch.setenv("FINIMATIC_AUTH_CLIENT_SECRET", "configured")
    monkeypatch.setenv("FINIMATIC_AUTH_AUTHORIZATION_URL", "http://127.0.0.1:8018/oidc/authorize")
    monkeypatch.setenv("FINIMATIC_AUTH_TOKEN_URL", "http://127.0.0.1:8018/oidc/token")
    monkeypatch.setenv("FINIMATIC_AUTH_REDIRECT_URI", "https://finimatic.example/api/auth/callback")
    from app.security.browser_session import InteractiveOIDCSettings

    with pytest.raises(RuntimeError, match="invalid production authorization url"):
        InteractiveOIDCSettings.from_environment()


def test_unrecognized_environment_hardens_bearer_issuer(monkeypatch):
    monkeypatch.setenv("FINIMATIC_ENVIRONMENT", "staging")
    monkeypatch.setenv("SUPABASE_AUTH_ISSUER", "http://127.0.0.1:8018/oidc")
    monkeypatch.setenv("SUPABASE_AUTH_JWKS_URL", "http://127.0.0.1:8018/oidc/jwks.json")
    monkeypatch.setenv("SUPABASE_AUTH_AUDIENCE", "authenticated")
    monkeypatch.setenv("FINIMATIC_AUTH_ADMIN_SUBJECTS", "admin-subject")

    with pytest.raises(RuntimeError, match="invalid production issuer"):
        OIDCSettings.from_environment()


def test_loopback_provider_factory_requires_explicit_test_enablement(monkeypatch):
    monkeypatch.delenv("FINIMATIC_TEST_OIDC_ENABLED", raising=False)
    monkeypatch.setenv("FINIMATIC_ENVIRONMENT", "test")
    with pytest.raises(RuntimeError, match="FINIMATIC_TEST_OIDC_ENABLED=1 is required"):
        create_loopback_provider(port=8018)


def test_loopback_provider_enforces_authorization_code_pkce(monkeypatch):
    monkeypatch.setenv("FINIMATIC_TEST_OIDC_ENABLED", "1")
    monkeypatch.setenv("FINIMATIC_ENVIRONMENT", "test")
    provider = create_loopback_provider(port=8018)
    with TestClient(provider) as client:
        verifier = "phase17-verifier-" + "x" * 48
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        authorize = client.post(
            "/oidc/authorize",
            data={
                "client_id": TEST_CLIENT_ID,
                "redirect_uri": "http://127.0.0.1:5173/api/auth/callback",
                "state": "state",
                "nonce": "nonce",
                "code_challenge": challenge,
            },
            follow_redirects=False,
        )
        assert authorize.status_code == 302
        code = parse_qs(urlparse(authorize.headers["location"]).query)["code"][0]
        rejected = client.post(
            "/oidc/token",
            data={
                "grant_type": "authorization_code",
                "client_id": TEST_CLIENT_ID,
                "redirect_uri": "http://127.0.0.1:5173/api/auth/callback",
                "code": code,
                "code_verifier": "wrong",
            },
        )
        assert rejected.status_code == 400

        authorize = client.post(
            "/oidc/authorize",
            data={
                "client_id": TEST_CLIENT_ID,
                "redirect_uri": "http://127.0.0.1:5173/api/auth/callback",
                "state": "state-2",
                "nonce": "nonce-2",
                "code_challenge": challenge,
            },
            follow_redirects=False,
        )
        code = parse_qs(urlparse(authorize.headers["location"]).query)["code"][0]
        accepted = client.post(
            "/oidc/token",
            data={
                "grant_type": "authorization_code",
                "client_id": TEST_CLIENT_ID,
                "redirect_uri": "http://127.0.0.1:5173/api/auth/callback",
                "code": code,
                "code_verifier": verifier,
            },
        )
        assert accepted.status_code == 200
        assert accepted.json()["id_token"]
