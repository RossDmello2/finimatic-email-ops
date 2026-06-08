from app.core.crypto import fingerprint
from app.db.models import Setting
from app.db.session import SessionLocal
from app.settings.service import set_value

from conftest import configure_sender


def test_settings_reports_effective_auto_processing_runtime_state(client, monkeypatch):
    disabled = client.get("/api/settings").json()

    assert disabled["auto_process_enabled"] is False
    assert disabled["auto_process_effective"] is False
    assert disabled["scheduler_enabled"] is False
    assert disabled["auto_process_block_reason"] == "runtime_scheduler_disabled"

    monkeypatch.delenv("FINIMATIC_DISABLE_SCHEDULER", raising=False)
    manual = client.get("/api/settings").json()

    assert manual["auto_process_enabled"] is False
    assert manual["auto_process_effective"] is False
    assert manual["scheduler_enabled"] is True
    assert manual["auto_process_block_reason"] == "setting_disabled"

    enabled = client.post("/api/settings", json={"auto_process_enabled": True}).json()

    assert enabled["auto_process_enabled"] is True
    assert enabled["auto_process_effective"] is True
    assert enabled["scheduler_enabled"] is True
    assert enabled["auto_process_block_reason"] is None


def test_render_free_defaults_to_gmail_api_and_rejects_smtp(client, monkeypatch):
    monkeypatch.setenv("FINIMATIC_HOSTING_MODE", "render_free")
    with SessionLocal() as db:
        db.query(Setting).filter(Setting.key.in_(["email_transport", "auto_process_enabled"])).delete(
            synchronize_session=False
        )
        db.commit()

    settings = client.get("/api/settings").json()

    assert settings["email_transport"] == "gmail_api"
    assert settings["auto_process_enabled"] is False
    assert settings["hosting_mode"] == "render_free"
    assert settings["automation_reliability"] == "best_effort_free_tier"
    assert settings["smtp_available"] is False

    rejected = client.post("/api/settings", json={"email_transport": "smtp"})

    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "hosted_transport_requires_gmail_api"


def test_unchanged_sender_settings_preserve_canary_verification(client):
    with SessionLocal() as db:
        set_value(db, "gmail_user", "sender@finimatic.test")
        set_value(db, "report_recipient", "report@recipient.test")
        set_value(db, "email_transport", "gmail_api")
        set_value(db, "canary_verified", "true")
        set_value(db, "sender_readiness", "canary_verified")
        db.commit()

    response = client.post(
        "/api/settings",
        json={
            "gmail_user": "sender@finimatic.test",
            "report_recipient": "report@recipient.test",
            "email_transport": "gmail_api",
            "daily_send_cap": 25,
        },
    )

    assert response.status_code == 200
    assert response.json()["canary_verified"] is True
    assert response.json()["sender_readiness"] == "canary_verified"


def test_changed_sender_identity_invalidates_canary_verification(client):
    with SessionLocal() as db:
        set_value(db, "gmail_user", "sender@finimatic.test")
        set_value(db, "canary_verified", "true")
        set_value(db, "sender_readiness", "canary_verified")
        db.commit()

    response = client.post(
        "/api/settings",
        json={"gmail_user": "different-sender@finimatic.test"},
    )

    assert response.status_code == 200
    assert response.json()["canary_verified"] is False
    assert response.json()["sender_readiness"] == "not_configured"


def test_settings_encrypts_secrets_and_returns_only_fingerprints(client):
    secret = "valid-app-password"
    key = "groq-test-one"
    gmail_api_secret = "gmail-api-client-secret"
    gmail_api_refresh_token = "gmail-api-refresh-token"

    response = client.post(
        "/api/settings",
        json={
            "gmail_user": "sender@example.com",
            "gmail_app_password": secret,
            "email_transport": "gmail_api",
            "gmail_api_client_id": "gmail-api-client-id",
            "gmail_api_client_secret": gmail_api_secret,
            "gmail_api_refresh_token": gmail_api_refresh_token,
            "report_recipient": "report@example.com",
            "campaign_context": "Plain text campaign context",
            "sender_name": "Ross Dmello",
            "sender_role": "AI Engineer",
            "sender_offer": "I build RAG chatbots",
            "sender_tone": "Friendly",
            "sender_signature": "Best regards\nRoss Dmello",
            "groq_model": "llama-3.1-8b-instant",
            "gemini_model": "gemini-1.5-pro",
            "blocked_domains": "example.com",
            "send_window_start": "09:00",
            "send_window_end": "17:00",
            "send_timezone": "Asia/Kolkata",
            "warm_up_mode": True,
            "imap_fetch_interval_minutes": 7,
            "auto_process_enabled": False,
            "auto_process_queue_interval_seconds": 9,
            "auto_process_followup_interval_seconds": 90,
            "groq_keys": f"{key}\ngroq-test-two",
            "gemini_keys": "gemini-test-one",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert secret not in str(body)
    assert key not in str(body)
    assert gmail_api_secret not in str(body)
    assert gmail_api_refresh_token not in str(body)
    assert body["groq_keys_count"] == 2
    assert body["groq_keys_fingerprints"][0] == fingerprint(key)
    assert body["campaign_context"] == "Plain text campaign context"
    assert body["sender_name"] == "Ross Dmello"
    assert body["sender_tone"] == "Friendly"
    assert body["groq_model"] == "llama-3.1-8b-instant"
    assert body["gemini_model"] == "gemini-2.5-flash"
    assert body["blocked_domains"] == "example.com"
    assert body["warm_up_mode"] is True
    assert body["warm_up_current_limit"] == 5
    assert body["imap_fetch_interval_minutes"] == 7
    assert body["auto_process_enabled"] is False
    assert body["auto_process_queue_interval_seconds"] == 9
    assert body["auto_process_followup_interval_seconds"] == 90
    assert body["email_transport"] == "gmail_api"
    assert body["gmail_api_configured"] is True

    get_body = client.get("/api/settings").json()
    assert secret not in str(get_body)
    assert key not in str(get_body)
    assert gmail_api_secret not in str(get_body)
    assert gmail_api_refresh_token not in str(get_body)
    assert get_body["gmail_app_password_configured"] is True

    with SessionLocal() as db:
        stored_password = db.query(Setting).filter_by(key="gmail_app_password").one().value
        stored_api_secret = db.query(Setting).filter_by(key="gmail_api_client_secret").one().value
        stored_refresh_token = db.query(Setting).filter_by(key="gmail_api_refresh_token").one().value
        stored_keys = db.query(Setting).filter_by(key="groq_keys").one().value
    assert secret not in stored_password
    assert gmail_api_secret not in stored_api_secret
    assert gmail_api_refresh_token not in stored_refresh_token
    assert key not in stored_keys


def test_smtp_verify_uses_fake_transport_and_redacts_failures(client):
    configure_sender(client)

    ok = client.post("/api/settings/verify-smtp").json()
    assert ok["readiness"] == "smtp_verified"
    assert ok.get("error_detail") is None

    ok_alias = client.post("/api/settings/verify-email").json()
    assert ok_alias["readiness"] == "smtp_verified"

    response = client.post(
        "/api/settings",
        json={"gmail_user": "sender@example.com", "gmail_app_password": "wrong-password"},
    )
    assert response.status_code == 200

    failed = client.post("/api/settings/verify-smtp").json()
    assert failed["readiness"] == "failed"
    assert "wrong-password" not in str(failed)


def test_gmail_api_settings_configure_sender_without_app_password(client):
    response = client.post(
        "/api/settings",
        json={
            "gmail_user": "sender@example.com",
            "email_transport": "gmail_api",
            "gmail_api_client_id": "client-id",
            "gmail_api_client_secret": "client-secret",
            "gmail_api_refresh_token": "refresh-token",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["email_transport"] == "gmail_api"
    assert body["gmail_app_password_configured"] is False
    assert body["gmail_api_configured"] is True
    assert body["sender_readiness"] == "configured"
    assert "client-secret" not in str(body)
    assert "refresh-token" not in str(body)


def test_canary_send_success_and_duplicate_block(client):
    configure_sender(client)
    client.post("/api/settings/verify-smtp")

    first = client.post("/api/canary/send").json()
    assert first["status"] == "provider_accepted"
    assert first["provider_accepted"] is True
    assert first["nonce"]
    assert first["sender_identity"] == "sender@example.com"
    assert client.get("/api/settings").json()["canary_verified"] is True
    assert len(client.app.state.transport.sent) == 1

    second = client.post("/api/canary/send").json()
    assert second["status"] == "duplicate_blocked"
    assert len(client.app.state.transport.sent) == 1

    event_types = [row["event_type"] for row in client.get("/api/audit").json()["items"]]
    assert "canary.attempt" in event_types
    assert "canary.success" in event_types
    assert "canary.duplicate_blocked" in event_types
