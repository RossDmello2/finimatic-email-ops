import importlib
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.send.outcomes import SendOutcome


class ProviderAcceptedTestTransport:
    transport_name = "test_provider"

    def __init__(self):
        self.sent: list[dict] = []

    def verify(self, user: str, password: str) -> bool:
        return bool(user and password and password != "wrong-password")

    def send(self, *, sender: str, password: str, to: str, subject: str, body: str, resolution) -> SendOutcome:
        if not self.verify(sender, password):
            raise RuntimeError("Provider authentication failed")
        provider_id = f"test-provider-{uuid.uuid4().hex}"
        self.sent.append({"sender": sender, "to": to, "subject": subject, "body": body, "message_id": provider_id})
        return SendOutcome(
            attempt_status="provider_accepted",
            configured_transport=resolution.configured_transport,
            effective_transport=resolution.effective_transport,
            transport_source=resolution.transport_source,
            simulated=False,
            provider_contacted=True,
            provider_accepted=True,
            provider_message_id=provider_id,
            provider_response_classification="test_provider_accepted",
        )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'finimatic.db'}")
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("FINIMATIC_TRANSPORT", raising=False)
    monkeypatch.setenv("FINIMATIC_DISABLE_SCHEDULER", "1")
    monkeypatch.setenv("FINIMATIC_TEST_SCHEMA_CREATE", "1")
    monkeypatch.setenv("FINIMATIC_ENVIRONMENT", "test")
    monkeypatch.setenv("FRONTEND_URL", "http://testserver")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("FINIMATIC_AUTH_COOKIE_SECURE", "0")
    monkeypatch.setenv("FINIMATIC_AUTH_ENABLED", "1")
    monkeypatch.setenv("FINIMATIC_AUTH_CLIENT_ID", "finimatic-test-browser")
    monkeypatch.setenv("FINIMATIC_AUTH_AUTHORIZATION_URL", "http://identity.test/authorize")
    monkeypatch.setenv("FINIMATIC_AUTH_TOKEN_URL", "http://identity.test/token")
    monkeypatch.setenv("FINIMATIC_AUTH_REDIRECT_URI", "http://testserver/api/auth/callback")

    import app.send.smtp_adapter

    transport = ProviderAcceptedTestTransport()
    app.send.smtp_adapter.set_test_transport_factory(lambda: transport)
    import app.main

    importlib.reload(app.main)
    try:
        application = app.main.create_app()
        application.state.authorization_checker = lambda request: {
            "subject": "test-admin",
            "session_id": "test-session",
            "roles": ("operator", "admin"),
            "authenticated": True,
            "authorized": True,
        }
        with TestClient(application) as test_client:
            yield test_client
    finally:
        app.send.smtp_adapter.set_test_transport_factory(None)


def configure_sender(client, *, canary_verified=False, dry_run=True):
    payload = {
        "gmail_user": "sender@example.com",
        "gmail_app_password": "valid-app-password",
        "report_recipient": "report@example.com",
        "groq_keys": "groq-test-one\ngroq-test-two",
        "gemini_keys": "gemini-test-one\ngemini-test-two",
        "daily_send_cap": 50,
        "hourly_send_cap": 10,
        "send_delay_s": 0,
        "followup_interval_days": 1,
        "max_followups_per_lead": 2,
        "campaign_context": "Fake campaign context",
        "send_window_start": "00:00",
        "send_window_end": "23:59",
        "send_timezone": "UTC",
        "imap_fetch_interval_minutes": 10,
        "dry_run": dry_run,
        "sender_name": "Ross Dmello",
        "sender_role": "AI Systems Engineer",
        "sender_offer": "I help course teams automate student Q&A with grounded RAG chatbots",
        "sender_signature": "Best regards\nRoss Dmello\nAI Systems Engineer",
    }
    response = client.post("/api/settings", json=payload)
    assert response.status_code == 200
    if canary_verified:
        from app.db.session import SessionLocal
        from app.settings.service import set_value

        with SessionLocal() as db:
            set_value(db, "canary_verified", "true")
            set_value(db, "sender_readiness", "canary_verified")
            db.commit()
        response = client.get("/api/settings")
    return response.json()
