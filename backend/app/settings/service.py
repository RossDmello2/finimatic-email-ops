from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.ai.key_utils import fingerprints, parse_keys
from app.audit.service import emit_event
from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.models import Setting


DEFAULT_SETTINGS: dict[str, str] = {
    "gmail_user": "",
    "gmail_app_password": "",
    "email_transport": "smtp",
    "gmail_api_client_id": "",
    "gmail_api_client_secret": "",
    "gmail_api_refresh_token": "",
    "groq_keys": "[]",
    "gemini_keys": "[]",
    "daily_send_cap": "50",
    "hourly_send_cap": "10",
    "send_delay_s": "60",
    "followup_interval_days": "3",
    "max_followups_per_lead": "2",
    "campaign_context": "",
    "sender_name": "",
    "sender_role": "",
    "sender_offer": "",
    "sender_tone": "Professional",
    "sender_signature": "",
    "groq_model": "llama-3.3-70b-versatile",
    "gemini_model": "gemini-2.5-flash",
    "follow_up_template_1": (
        "Brief friendly check-in. Reference the first email. Add one new piece of value "
        "- a relevant insight or result. Keep it under 80 words. No hard sell."
    ),
    "follow_up_template_2": (
        "Polite breakup email. Acknowledge they may be busy. Leave the door open. "
        "One sentence offer. Sign off warmly. Under 60 words."
    ),
    "blocked_domains": "",
    "send_window_start": "09:00",
    "send_window_end": "17:00",
    "send_timezone": "Asia/Kolkata",
    "warm_up_mode": "false",
    "warm_up_start_date": "",
    "imap_fetch_interval_minutes": "5",
    "auto_reply_enabled": "false",
    "auto_reply_mode": "propose",
    "auto_reply_daily_cap": "20",
    "auto_reply_min_gap_minutes": "60",
    "auto_reply_safe_intents": "positive_interest,objection,question",
    "dry_run": "true",
    "canary_verified": "false",
    "report_recipient": "",
    "sender_readiness": "not_configured",
}

SECRET_KEYS = {"gmail_app_password", "gmail_api_client_id", "gmail_api_client_secret", "gmail_api_refresh_token", "groq_keys", "gemini_keys"}


def seed_settings(db: Session) -> None:
    changed = False
    for key, value in DEFAULT_SETTINGS.items():
        if not db.query(Setting).filter_by(key=key).first():
            db.add(Setting(key=key, value=value))
            changed = True
    if changed:
        db.commit()


def _setting(db: Session, key: str) -> Setting:
    row = db.query(Setting).filter_by(key=key).first()
    if row is None:
        row = Setting(key=key, value=DEFAULT_SETTINGS.get(key, ""))
        db.add(row)
        db.flush()
    return row


def set_value(db: Session, key: str, value: str) -> None:
    row = _setting(db, key)
    row.value = value


def get_value(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter_by(key=key).first()
    if row is None or row.value is None:
        return default
    return row.value


def get_bool(db: Session, key: str) -> bool:
    return get_value(db, key, "false").lower() == "true"


def get_int(db: Session, key: str) -> int:
    try:
        return int(get_value(db, key, DEFAULT_SETTINGS.get(key, "0")))
    except ValueError:
        return int(DEFAULT_SETTINGS.get(key, "0"))


def get_effective_daily_send_cap(db: Session) -> int:
    configured = get_int(db, "daily_send_cap")
    if not get_bool(db, "warm_up_mode"):
        return configured
    start = get_value(db, "warm_up_start_date")
    try:
        start_date = datetime.fromisoformat(start).date()
    except ValueError:
        start_date = datetime.now(timezone.utc).date()
    day = (datetime.now(timezone.utc).date() - start_date).days + 1
    if day <= 3:
        return min(configured, 5)
    if day <= 7:
        return min(configured, 15)
    if day <= 14:
        return min(configured, 30)
    return configured


def get_secret(db: Session, key: str) -> str:
    return decrypt_secret(get_value(db, key, ""))


def get_key_list(db: Session, key: str) -> list[str]:
    stored = get_value(db, key, "[]")
    try:
        encrypted = json.loads(stored)
    except json.JSONDecodeError:
        return []
    return [decrypt_secret(item) for item in encrypted if item]


def set_settings(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    seed_settings(db)
    changed_keys: list[str] = []
    warm_up_was_enabled = get_bool(db, "warm_up_mode")

    for secret_key in ("gmail_app_password", "gmail_api_client_id", "gmail_api_client_secret", "gmail_api_refresh_token"):
        if secret_key in payload and payload[secret_key] is not None:
            set_value(db, secret_key, encrypt_secret(str(payload[secret_key])))
            changed_keys.append(secret_key)

    for key_name in ("groq_keys", "gemini_keys"):
        if key_name in payload and payload[key_name] is not None:
            keys = parse_keys(payload[key_name])
            encrypted = [encrypt_secret(key) for key in keys]
            set_value(db, key_name, json.dumps(encrypted))
            changed_keys.append(key_name)

    for key, value in payload.items():
        if key in SECRET_KEYS or value is None:
            continue
        if key in DEFAULT_SETTINGS:
            if isinstance(value, bool):
                stored = "true" if value else "false"
            else:
                stored = str(value)
            if key == "gemini_model":
                stored = "gemini-2.5-flash"
            if key == "email_transport" and stored not in {"smtp", "gmail_api"}:
                stored = DEFAULT_SETTINGS["email_transport"]
            if key == "auto_reply_mode" and stored not in {"propose", "autonomous"}:
                stored = DEFAULT_SETTINGS["auto_reply_mode"]
            set_value(db, key, stored)
            changed_keys.append(key)

    if get_bool(db, "warm_up_mode") and not warm_up_was_enabled:
        set_value(db, "warm_up_start_date", datetime.now(timezone.utc).date().isoformat())
        changed_keys.append("warm_up_start_date")
    elif not get_bool(db, "warm_up_mode"):
        set_value(db, "warm_up_start_date", "")

    if get_bool(db, "canary_verified"):
        set_value(db, "sender_readiness", "canary_verified")
    elif sender_credentials_configured(db):
        current = get_value(db, "sender_readiness", "not_configured")
        if current == "not_configured":
            set_value(db, "sender_readiness", "configured")

    emit_event(db, "settings.updated", payload={"changed_keys": sorted(set(changed_keys))})
    db.commit()
    return settings_read(db)


def settings_read(db: Session) -> dict[str, Any]:
    seed_settings(db)
    groq = get_key_list(db, "groq_keys")
    gemini = get_key_list(db, "gemini_keys")
    return {
        "gmail_user": get_value(db, "gmail_user"),
        "email_transport": get_value(db, "email_transport", DEFAULT_SETTINGS["email_transport"]),
        "gmail_app_password_configured": bool(get_value(db, "gmail_app_password")),
        "gmail_api_configured": all(
            bool(get_value(db, key))
            for key in ("gmail_api_client_id", "gmail_api_client_secret", "gmail_api_refresh_token")
        ),
        "report_recipient": get_value(db, "report_recipient"),
        "groq_keys_count": len(groq),
        "groq_keys_fingerprints": fingerprints(groq),
        "gemini_keys_count": len(gemini),
        "gemini_keys_fingerprints": fingerprints(gemini),
        "daily_send_cap": get_int(db, "daily_send_cap"),
        "hourly_send_cap": get_int(db, "hourly_send_cap"),
        "send_delay_s": get_int(db, "send_delay_s"),
        "followup_interval_days": get_int(db, "followup_interval_days"),
        "max_followups_per_lead": get_int(db, "max_followups_per_lead"),
        "campaign_context": get_value(db, "campaign_context"),
        "sender_name": get_value(db, "sender_name"),
        "sender_role": get_value(db, "sender_role"),
        "sender_offer": get_value(db, "sender_offer"),
        "sender_tone": get_value(db, "sender_tone", DEFAULT_SETTINGS["sender_tone"]),
        "sender_signature": get_value(db, "sender_signature"),
        "groq_model": get_value(db, "groq_model", DEFAULT_SETTINGS["groq_model"]),
        "gemini_model": DEFAULT_SETTINGS["gemini_model"],
        "follow_up_template_1": get_value(db, "follow_up_template_1", DEFAULT_SETTINGS["follow_up_template_1"]),
        "follow_up_template_2": get_value(db, "follow_up_template_2", DEFAULT_SETTINGS["follow_up_template_2"]),
        "blocked_domains": get_value(db, "blocked_domains"),
        "send_window_start": get_value(db, "send_window_start", DEFAULT_SETTINGS["send_window_start"]),
        "send_window_end": get_value(db, "send_window_end", DEFAULT_SETTINGS["send_window_end"]),
        "send_timezone": get_value(db, "send_timezone", DEFAULT_SETTINGS["send_timezone"]),
        "warm_up_mode": get_bool(db, "warm_up_mode"),
        "warm_up_start_date": get_value(db, "warm_up_start_date"),
        "warm_up_current_limit": get_effective_daily_send_cap(db),
        "imap_fetch_interval_minutes": get_int(db, "imap_fetch_interval_minutes"),
        "auto_reply_enabled": get_bool(db, "auto_reply_enabled"),
        "auto_reply_mode": get_value(db, "auto_reply_mode", DEFAULT_SETTINGS["auto_reply_mode"]),
        "auto_reply_daily_cap": get_int(db, "auto_reply_daily_cap"),
        "auto_reply_min_gap_minutes": get_int(db, "auto_reply_min_gap_minutes"),
        "auto_reply_safe_intents": get_value(db, "auto_reply_safe_intents", DEFAULT_SETTINGS["auto_reply_safe_intents"]),
        "dry_run": get_bool(db, "dry_run"),
        "canary_verified": get_bool(db, "canary_verified"),
        "sender_readiness": get_value(db, "sender_readiness", "not_configured"),
        "mode": mode_label(db),
    }


def mode_label(db: Session) -> str:
    if get_bool(db, "dry_run"):
        return "DRY-RUN"
    if not get_bool(db, "canary_verified"):
        return "CANARY"
    return "LIVE"


def sender_credentials_configured(db: Session) -> bool:
    if not get_value(db, "gmail_user"):
        return False
    if get_value(db, "email_transport", DEFAULT_SETTINGS["email_transport"]) == "gmail_api":
        return all(
            bool(get_value(db, key))
            for key in ("gmail_api_client_id", "gmail_api_client_secret", "gmail_api_refresh_token")
        )
    return bool(get_value(db, "gmail_app_password"))
