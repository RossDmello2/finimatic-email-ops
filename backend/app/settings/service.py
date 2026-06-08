from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.ai.key_utils import fingerprints, parse_keys
from app.ai.model_ids import normalize_groq_model
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
    "auto_process_enabled": "false",
    "auto_process_queue_interval_seconds": "5",
    "auto_process_followup_interval_seconds": "60",
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
    "auto_reply_autonomous_authorized": "false",
    "auto_reply_kill_switch": "true",
    "auto_reply_kill_generation": "0",
    "auto_reply_daily_cap": "20",
    "auto_reply_min_gap_minutes": "60",
    "auto_reply_safe_intents": "positive_interest,objection,question",
    "dry_run": "true",
    "canary_verified": "false",
    "report_recipient": "",
    "sender_readiness": "not_configured",
}

SECRET_KEYS = {"gmail_app_password", "gmail_api_client_id", "gmail_api_client_secret", "gmail_api_refresh_token", "groq_keys", "gemini_keys"}
SERVER_MANAGED_KEYS = {
    "canary_verified",
    "sender_readiness",
    "auto_reply_autonomous_authorized",
    "auto_reply_kill_switch",
}


def seed_settings(db: Session) -> None:
    existing_keys = {
        row[0]
        for row in db.query(Setting.key).filter(Setting.key.in_(DEFAULT_SETTINGS.keys())).all()
    }
    changed = False
    for key, value in DEFAULT_SETTINGS.items():
        if key not in existing_keys:
            if key == "email_transport" and os.getenv("FINIMATIC_HOSTING_MODE", "").strip().lower() == "render_free":
                value = "gmail_api"
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


def _settings_snapshot(db: Session) -> dict[str, str]:
    rows = db.query(Setting).filter(Setting.key.in_(DEFAULT_SETTINGS.keys())).all()
    values = dict(DEFAULT_SETTINGS)
    for row in rows:
        if row.value is not None:
            values[row.key] = row.value
    return values


def _snapshot_value(snapshot: dict[str, str], key: str, default: str = "") -> str:
    return snapshot.get(key, default)


def _snapshot_bool(snapshot: dict[str, str], key: str) -> bool:
    return _snapshot_value(snapshot, key, "false").lower() == "true"


def _snapshot_int(snapshot: dict[str, str], key: str) -> int:
    try:
        return int(_snapshot_value(snapshot, key, DEFAULT_SETTINGS.get(key, "0")))
    except ValueError:
        return int(DEFAULT_SETTINGS.get(key, "0"))


def _snapshot_key_list(snapshot: dict[str, str], key: str) -> list[str]:
    stored = _snapshot_value(snapshot, key, "[]")
    try:
        encrypted = json.loads(stored)
    except json.JSONDecodeError:
        return []
    return [decrypt_secret(item) for item in encrypted if item]


def _effective_daily_send_cap_from_snapshot(snapshot: dict[str, str]) -> int:
    configured = _snapshot_int(snapshot, "daily_send_cap")
    if not _snapshot_bool(snapshot, "warm_up_mode"):
        return configured
    start = _snapshot_value(snapshot, "warm_up_start_date")
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


def _mode_label_from_snapshot(snapshot: dict[str, str]) -> str:
    if _snapshot_bool(snapshot, "dry_run"):
        return "DRY-RUN"
    if not _snapshot_bool(snapshot, "canary_verified"):
        return "CANARY"
    return "LIVE"


def set_settings(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    seed_settings(db)
    changed_keys: list[str] = []
    warm_up_was_enabled = get_bool(db, "warm_up_mode")

    for secret_key in ("gmail_app_password", "gmail_api_client_id", "gmail_api_client_secret", "gmail_api_refresh_token"):
        if secret_key in payload and payload[secret_key] is not None:
            new_secret = str(payload[secret_key])
            if get_secret(db, secret_key) != new_secret:
                set_value(db, secret_key, encrypt_secret(new_secret))
                changed_keys.append(secret_key)

    for key_name in ("groq_keys", "gemini_keys"):
        if key_name in payload and payload[key_name] is not None:
            keys = parse_keys(payload[key_name])
            if get_key_list(db, key_name) != keys:
                encrypted = [encrypt_secret(key) for key in keys]
                set_value(db, key_name, json.dumps(encrypted))
                changed_keys.append(key_name)

    for key, value in payload.items():
        if key in SECRET_KEYS or key in SERVER_MANAGED_KEYS or value is None:
            continue
        if key in DEFAULT_SETTINGS:
            if isinstance(value, bool):
                stored = "true" if value else "false"
            else:
                stored = str(value)
            if key == "gemini_model":
                stored = "gemini-2.5-flash"
            if key == "groq_model":
                stored = normalize_groq_model(stored, DEFAULT_SETTINGS["groq_model"])
            if key == "email_transport" and stored not in {"smtp", "gmail_api"}:
                stored = DEFAULT_SETTINGS["email_transport"]
            if key == "auto_reply_mode" and stored not in {"propose", "autonomous"}:
                stored = DEFAULT_SETTINGS["auto_reply_mode"]
            if get_value(db, key, DEFAULT_SETTINGS.get(key, "")) != stored:
                set_value(db, key, stored)
                changed_keys.append(key)

    if get_bool(db, "warm_up_mode") and not warm_up_was_enabled:
        set_value(db, "warm_up_start_date", datetime.now(timezone.utc).date().isoformat())
        changed_keys.append("warm_up_start_date")
    elif not get_bool(db, "warm_up_mode"):
        set_value(db, "warm_up_start_date", "")

    sender_identity_keys = {
        "email_transport",
        "gmail_user",
        "gmail_app_password",
        "gmail_api_client_id",
        "gmail_api_client_secret",
        "gmail_api_refresh_token",
        "report_recipient",
    }
    if sender_identity_keys.intersection(changed_keys):
        set_value(db, "canary_verified", "false")
        set_value(db, "sender_readiness", "configured" if sender_credentials_configured(db) else "not_configured")

    if get_bool(db, "canary_verified"):
        set_value(db, "sender_readiness", "canary_verified")
    elif sender_credentials_configured(db):
        current = get_value(db, "sender_readiness", "not_configured")
        if current == "not_configured":
            set_value(db, "sender_readiness", "configured")

    scheduling_keys = {
        "send_window_start",
        "send_window_end",
        "send_timezone",
        "send_delay_s",
        "email_transport",
        "gmail_user",
        "gmail_app_password",
        "gmail_api_client_id",
        "gmail_api_client_secret",
        "gmail_api_refresh_token",
    }
    rescheduled_queue_entries = 0
    if scheduling_keys.intersection(changed_keys):
        from app.send.queue_worker import reschedule_policy_deferred_queue_entries

        rescheduled_queue_entries = reschedule_policy_deferred_queue_entries(
            db,
            changed_keys=set(changed_keys),
        )

    emit_event(
        db,
        "settings.updated",
        payload={
            "changed_keys": sorted(set(changed_keys)),
            "rescheduled_queue_entries": rescheduled_queue_entries,
        },
    )
    db.commit()
    return settings_read(db)


def settings_read(db: Session) -> dict[str, Any]:
    seed_settings(db)
    from app.send.smtp_adapter import resolve_transport_metadata

    snapshot = _settings_snapshot(db)
    groq = _snapshot_key_list(snapshot, "groq_keys")
    gemini = _snapshot_key_list(snapshot, "gemini_keys")
    transport = resolve_transport_metadata(db)
    auto_process_configured = _snapshot_bool(snapshot, "auto_process_enabled")
    scheduler_disabled = os.getenv("FINIMATIC_DISABLE_SCHEDULER") == "1"
    auto_process_runtime_disabled = os.getenv("FINIMATIC_DISABLE_AUTO_PROCESS") == "1"
    hosting_mode = os.getenv("FINIMATIC_HOSTING_MODE", "local").strip().lower() or "local"
    automation_reliability = (
        "best_effort_free_tier"
        if hosting_mode == "render_free"
        else "runtime_dependent"
    )
    auto_process_effective = auto_process_configured and not scheduler_disabled and not auto_process_runtime_disabled
    auto_process_block_reason = (
        "runtime_scheduler_disabled"
        if scheduler_disabled
        else "runtime_auto_process_disabled"
        if auto_process_runtime_disabled
        else "setting_disabled"
        if not auto_process_configured
        else None
    )
    return {
        "gmail_user": _snapshot_value(snapshot, "gmail_user"),
        "email_transport": _snapshot_value(snapshot, "email_transport", DEFAULT_SETTINGS["email_transport"]),
        "configured_transport": transport.configured_transport,
        "effective_transport": transport.effective_transport,
        "transport_source": transport.transport_source,
        "transport_simulated": transport.simulated,
        "transport_mismatch": transport.configured_transport != transport.effective_transport,
        "gmail_app_password_configured": bool(_snapshot_value(snapshot, "gmail_app_password")),
        "gmail_api_configured": all(
            bool(_snapshot_value(snapshot, key))
            for key in ("gmail_api_client_id", "gmail_api_client_secret", "gmail_api_refresh_token")
        ),
        "report_recipient": _snapshot_value(snapshot, "report_recipient"),
        "groq_keys_count": len(groq),
        "groq_keys_fingerprints": fingerprints(groq),
        "gemini_keys_count": len(gemini),
        "gemini_keys_fingerprints": fingerprints(gemini),
        "daily_send_cap": _snapshot_int(snapshot, "daily_send_cap"),
        "hourly_send_cap": _snapshot_int(snapshot, "hourly_send_cap"),
        "send_delay_s": _snapshot_int(snapshot, "send_delay_s"),
        "auto_process_enabled": auto_process_configured,
        "auto_process_effective": auto_process_effective,
        "auto_process_block_reason": auto_process_block_reason,
        "scheduler_enabled": not scheduler_disabled,
        "hosting_mode": hosting_mode,
        "automation_reliability": automation_reliability,
        "smtp_available": hosting_mode != "render_free",
        "auto_process_queue_interval_seconds": _snapshot_int(snapshot, "auto_process_queue_interval_seconds"),
        "auto_process_followup_interval_seconds": _snapshot_int(snapshot, "auto_process_followup_interval_seconds"),
        "followup_interval_days": _snapshot_int(snapshot, "followup_interval_days"),
        "max_followups_per_lead": _snapshot_int(snapshot, "max_followups_per_lead"),
        "campaign_context": _snapshot_value(snapshot, "campaign_context"),
        "sender_name": _snapshot_value(snapshot, "sender_name"),
        "sender_role": _snapshot_value(snapshot, "sender_role"),
        "sender_offer": _snapshot_value(snapshot, "sender_offer"),
        "sender_tone": _snapshot_value(snapshot, "sender_tone", DEFAULT_SETTINGS["sender_tone"]),
        "sender_signature": _snapshot_value(snapshot, "sender_signature"),
        "groq_model": normalize_groq_model(_snapshot_value(snapshot, "groq_model", DEFAULT_SETTINGS["groq_model"]), DEFAULT_SETTINGS["groq_model"]),
        "gemini_model": DEFAULT_SETTINGS["gemini_model"],
        "follow_up_template_1": _snapshot_value(snapshot, "follow_up_template_1", DEFAULT_SETTINGS["follow_up_template_1"]),
        "follow_up_template_2": _snapshot_value(snapshot, "follow_up_template_2", DEFAULT_SETTINGS["follow_up_template_2"]),
        "blocked_domains": _snapshot_value(snapshot, "blocked_domains"),
        "send_window_start": _snapshot_value(snapshot, "send_window_start", DEFAULT_SETTINGS["send_window_start"]),
        "send_window_end": _snapshot_value(snapshot, "send_window_end", DEFAULT_SETTINGS["send_window_end"]),
        "send_timezone": _snapshot_value(snapshot, "send_timezone", DEFAULT_SETTINGS["send_timezone"]),
        "warm_up_mode": _snapshot_bool(snapshot, "warm_up_mode"),
        "warm_up_start_date": _snapshot_value(snapshot, "warm_up_start_date"),
        "warm_up_current_limit": _effective_daily_send_cap_from_snapshot(snapshot),
        "imap_fetch_interval_minutes": _snapshot_int(snapshot, "imap_fetch_interval_minutes"),
        "auto_reply_enabled": _snapshot_bool(snapshot, "auto_reply_enabled"),
        "auto_reply_mode": _snapshot_value(snapshot, "auto_reply_mode", DEFAULT_SETTINGS["auto_reply_mode"]),
        "auto_reply_autonomous_authorized": _snapshot_bool(snapshot, "auto_reply_autonomous_authorized"),
        "auto_reply_kill_switch": _snapshot_bool(snapshot, "auto_reply_kill_switch"),
        "auto_reply_daily_cap": _snapshot_int(snapshot, "auto_reply_daily_cap"),
        "auto_reply_min_gap_minutes": _snapshot_int(snapshot, "auto_reply_min_gap_minutes"),
        "auto_reply_safe_intents": _snapshot_value(snapshot, "auto_reply_safe_intents", DEFAULT_SETTINGS["auto_reply_safe_intents"]),
        "dry_run": _snapshot_bool(snapshot, "dry_run"),
        "canary_verified": _snapshot_bool(snapshot, "canary_verified"),
        "sender_readiness": _snapshot_value(snapshot, "sender_readiness", "not_configured"),
        "mode": _mode_label_from_snapshot(snapshot),
        "api_security_mode": "unauthenticated_local_only",
        "api_security_enforced": False,
        "release_blocked": True,
        "release_block_reason": "production_identity_architecture_not_configured",
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
