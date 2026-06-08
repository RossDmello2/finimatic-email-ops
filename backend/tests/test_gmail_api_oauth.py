from urllib.parse import parse_qs, urlparse

from app.settings.service import set_value


def _configure_gmail_api_client(client):
    response = client.post(
        "/api/settings",
        json={
            "gmail_user": "sender@example.com",
            "email_transport": "gmail_api",
            "gmail_api_client_id": "oauth-client-id",
            "gmail_api_client_secret": "oauth-client-secret",
        },
    )
    assert response.status_code == 200


def _oauth_state_from_start(client):
    response = client.post(
        "/api/settings/gmail-api/oauth/start",
        json={"return_url": "http://localhost:5173/"},
    )
    assert response.status_code == 200
    body = response.json()
    parsed = urlparse(body["authorization_url"])
    params = parse_qs(parsed.query)
    return body, params["state"][0]


def test_gmail_api_oauth_start_builds_offline_consent_url(client):
    _configure_gmail_api_client(client)

    body, state = _oauth_state_from_start(client)
    parsed = urlparse(body["authorization_url"])
    params = parse_qs(parsed.query)

    assert parsed.netloc == "accounts.google.com"
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["oauth-client-id"]
    assert params["redirect_uri"][0].endswith("/api/settings/gmail-api/oauth/callback")
    assert "https://www.googleapis.com/auth/gmail.send" in params["scope"][0]
    assert "https://www.googleapis.com/auth/gmail.metadata" in params["scope"][0]
    assert "oauth-client-secret" not in str(body)
    assert "test-admin" not in state
    assert "test-session" not in state

    from app.db.models import Setting
    from app.db.session import SessionLocal
    from app.security.oauth_state import OAUTH_STATE_PREFIX

    with SessionLocal() as db:
        stored = db.query(Setting).filter(Setting.key.like(f"{OAUTH_STATE_PREFIX}%")).one()
        assert "test-admin" not in (stored.value or "")
        assert "test-session" not in (stored.value or "")


def test_gmail_api_oauth_callback_stores_refresh_token_and_verifies(client, monkeypatch):
    _configure_gmail_api_client(client)
    _body, state = _oauth_state_from_start(client)

    async def fake_exchange_oauth_code(**kwargs):
        assert kwargs["client_id"] == "oauth-client-id"
        assert kwargs["client_secret"] == "oauth-client-secret"
        assert kwargs["code"] == "authorization-code"
        return {
            "access_token": "access-token",
            "refresh_token": "oauth-refresh-value",
            "scope": "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.metadata",
        }

    async def fake_verify_email_provider(db):
        set_value(db, "sender_readiness", "provider_verified")
        db.commit()
        return {"readiness": "provider_verified"}

    import app.settings.router as settings_router

    monkeypatch.setattr(settings_router, "_exchange_oauth_code", fake_exchange_oauth_code)
    monkeypatch.setattr(settings_router, "_verify_email_provider", fake_verify_email_provider)

    response = client.get(
        f"/api/settings/gmail-api/oauth/callback?code=authorization-code&state={state}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "gmail_api_oauth=connected" in response.headers["location"]
    settings = client.get("/api/settings").json()
    assert settings["email_transport"] == "gmail_api"
    assert settings["gmail_api_configured"] is True
    assert settings["sender_readiness"] == "provider_verified"
    assert "oauth-refresh-value" not in str(settings)
    assert "oauth-refresh-value" not in str(client.get("/api/audit").json())


def test_gmail_api_oauth_callback_rejects_invalid_state_before_exchange(client, monkeypatch):
    called = False

    async def fake_exchange_oauth_code(**kwargs):
        nonlocal called
        called = True
        return {}

    import app.settings.router as settings_router

    monkeypatch.setattr(settings_router, "_exchange_oauth_code", fake_exchange_oauth_code)

    response = client.get(
        "/api/settings/gmail-api/oauth/callback?code=authorization-code&state=invalid",
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_oauth_state"
    assert called is False


def test_gmail_api_oauth_callback_rejects_expired_state_before_exchange(client, monkeypatch):
    _configure_gmail_api_client(client)
    _body, state = _oauth_state_from_start(client)
    called = False

    async def fake_exchange_oauth_code(**kwargs):
        nonlocal called
        called = True
        return {}

    import app.security.oauth_state as oauth_state
    import app.settings.router as settings_router

    monkeypatch.setattr(oauth_state.time, "time", lambda: 10**12)
    monkeypatch.setattr(settings_router, "_exchange_oauth_code", fake_exchange_oauth_code)

    response = client.get(
        f"/api/settings/gmail-api/oauth/callback?code=authorization-code&state={state}",
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "expired_oauth_state"
    assert called is False


def test_gmail_api_oauth_callback_rejects_replayed_state(client, monkeypatch):
    _configure_gmail_api_client(client)
    _body, state = _oauth_state_from_start(client)
    exchanges = 0

    async def fake_exchange_without_refresh_token(**kwargs):
        nonlocal exchanges
        exchanges += 1
        return {"access_token": "access-token"}

    import app.settings.router as settings_router

    monkeypatch.setattr(settings_router, "_exchange_oauth_code", fake_exchange_without_refresh_token)

    first = client.get(
        f"/api/settings/gmail-api/oauth/callback?code=authorization-code&state={state}",
        follow_redirects=False,
    )
    replay = client.get(
        f"/api/settings/gmail-api/oauth/callback?code=authorization-code&state={state}",
        follow_redirects=False,
    )

    assert first.status_code == 303
    assert replay.status_code == 400
    assert replay.json()["detail"] == "replayed_oauth_state"
    assert exchanges == 1


def test_gmail_api_oauth_callback_rejects_cross_session_bearer_without_consuming_state(client, monkeypatch):
    _configure_gmail_api_client(client)
    _body, state = _oauth_state_from_start(client)
    exchanges = 0

    async def fake_exchange_without_refresh_token(**kwargs):
        nonlocal exchanges
        exchanges += 1
        return {"access_token": "access-token"}

    import app.settings.router as settings_router

    monkeypatch.setattr(settings_router, "_exchange_oauth_code", fake_exchange_without_refresh_token)
    original_checker = client.app.state.authorization_checker
    client.app.state.authorization_checker = lambda request: {
        "subject": "test-admin",
        "session_id": "different-session",
        "roles": ("operator", "admin"),
        "authenticated": True,
        "authorized": True,
    }

    mismatch = client.get(
        f"/api/settings/gmail-api/oauth/callback?code=authorization-code&state={state}",
        headers={"Authorization": "Bearer cross-session"},
        follow_redirects=False,
    )
    client.app.state.authorization_checker = original_checker
    callback_without_bearer = client.get(
        f"/api/settings/gmail-api/oauth/callback?code=authorization-code&state={state}",
        follow_redirects=False,
    )

    assert mismatch.status_code == 400
    assert mismatch.json()["detail"] == "oauth_state_session_mismatch"
    assert callback_without_bearer.status_code == 303
    assert exchanges == 1


def test_gmail_api_oauth_callback_rejects_operator_bearer(client, monkeypatch):
    _configure_gmail_api_client(client)
    _body, state = _oauth_state_from_start(client)
    called = False

    async def fake_exchange_oauth_code(**kwargs):
        nonlocal called
        called = True
        return {}

    import app.settings.router as settings_router

    monkeypatch.setattr(settings_router, "_exchange_oauth_code", fake_exchange_oauth_code)
    client.app.state.authorization_checker = lambda request: {
        "subject": "test-operator",
        "session_id": "test-session",
        "roles": ("operator",),
        "authenticated": True,
        "authorized": True,
    }

    response = client.get(
        f"/api/settings/gmail-api/oauth/callback?code=authorization-code&state={state}",
        headers={"Authorization": "Bearer operator"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "authorization_required"
    assert called is False


def test_gmail_api_oauth_callback_requires_refresh_token(client, monkeypatch):
    _configure_gmail_api_client(client)
    _body, state = _oauth_state_from_start(client)

    async def fake_exchange_without_refresh_token(**kwargs):
        return {"access_token": "access-token"}

    import app.settings.router as settings_router

    monkeypatch.setattr(settings_router, "_exchange_oauth_code", fake_exchange_without_refresh_token)

    response = client.get(
        f"/api/settings/gmail-api/oauth/callback?code=authorization-code&state={state}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "gmail_api_oauth=refresh_token_missing" in response.headers["location"]
    assert client.get("/api/settings").json()["gmail_api_configured"] is False
