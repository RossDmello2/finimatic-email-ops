from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.core.time import utcnow
from app.db.models import (
    Contact,
    DeliverabilityCheck,
    InboxPlacementTest,
    RecipientDomainCap,
    Reply,
    SendAttempt,
    SenderDomainHealth,
    SenderMailbox,
)
from app.settings.service import get_int, get_value
from app.verification.service import email_domain


BLOCK_DOMAIN_STATUSES = {"failed", "unhealthy", "blocked"}
WARN_DOMAIN_STATUSES = {"unknown", "not_connected", "partial"}
RAMP_STAGE_DAILY_CAPS = {
    "low_volume": 10,
    "warming": 25,
    "steady": 75,
    "established": 150,
}
RATE_WINDOW_DAYS = 7
BOUNCE_RATE_THRESHOLD = 0.05
COMPLAINT_RATE_THRESHOLD = 0.003


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def mailbox_to_dict(row: SenderMailbox) -> dict:
    return {
        "id": row.id,
        "email": row.email,
        "domain": row.domain,
        "provider": row.provider,
        "transport": row.transport,
        "status": row.status,
        "daily_cap": row.daily_cap,
        "hourly_cap": row.hourly_cap,
        "min_delay_s": row.min_delay_s,
        "ramp_stage": row.ramp_stage,
        "last_health_check_at": _iso(row.last_health_check_at),
    }


def domain_health_to_dict(row: SenderDomainHealth | None, domain: str | None = None) -> dict:
    if row is None:
        return {
            "id": None,
            "domain": domain or "",
            "spf_status": "unknown",
            "dkim_status": "unknown",
            "dmarc_status": "unknown",
            "alignment_status": "unknown",
            "postmaster_status": "not_connected",
            "spam_rate_bucket": "unknown",
            "reputation_bucket": "unknown",
            "last_checked_at": None,
        }
    return {
        "id": row.id,
        "domain": row.domain,
        "spf_status": row.spf_status,
        "dkim_status": row.dkim_status,
        "dmarc_status": row.dmarc_status,
        "alignment_status": row.alignment_status,
        "postmaster_status": row.postmaster_status,
        "spam_rate_bucket": row.spam_rate_bucket,
        "reputation_bucket": row.reputation_bucket,
        "last_checked_at": _iso(row.last_checked_at),
    }


def check_to_dict(row: DeliverabilityCheck) -> dict:
    return {
        "id": row.id,
        "sender_email": row.sender_email,
        "domain": row.domain,
        "check_type": row.check_type,
        "status": row.status,
        "severity": row.severity,
        "details_redacted": row.details_redacted,
        "checked_at": _iso(row.checked_at),
    }


def inbox_test_to_dict(row: InboxPlacementTest) -> dict:
    return {
        "id": row.id,
        "sender_email": row.sender_email,
        "seed_email": row.seed_email,
        "recipient_domain": row.recipient_domain,
        "subject": row.subject,
        "status": row.status,
        "placement": row.placement,
        "provider_msg_id": row.provider_msg_id,
        "sent_at": _iso(row.sent_at),
        "checked_at": _iso(row.checked_at),
        "details_redacted": row.details_redacted,
    }


def cap_to_dict(row: RecipientDomainCap) -> dict:
    return {
        "id": row.id,
        "sender_email": row.sender_email,
        "recipient_domain": row.recipient_domain,
        "daily_cap": row.daily_cap,
        "sent_today": row.sent_today,
        "last_sent_at": _iso(row.last_sent_at),
        "window_date": row.window_date,
        "status": row.status,
    }


def sender_email(db: Session) -> str:
    return (get_value(db, "gmail_user") or "").strip().lower()


def sender_domain(db: Session) -> str:
    return email_domain(sender_email(db))


def ensure_sender_mailbox(db: Session) -> SenderMailbox | None:
    email = sender_email(db)
    if not email:
        return None
    domain = email_domain(email)
    row = db.query(SenderMailbox).filter(SenderMailbox.email == email).first()
    if row:
        return row
    row = SenderMailbox(
        email=email,
        domain=domain,
        provider="gmail",
        transport=get_value(db, "email_transport", "smtp"),
        status="unknown",
        daily_cap=min(max(get_int(db, "daily_send_cap"), 1), 10),
        hourly_cap=min(max(get_int(db, "hourly_send_cap"), 1), 2),
        min_delay_s=max(get_int(db, "send_delay_s"), 60),
        ramp_stage="low_volume",
    )
    db.add(row)
    db.flush()
    return row


def ensure_domain_health(db: Session) -> SenderDomainHealth | None:
    domain = sender_domain(db)
    if not domain:
        return None
    row = db.query(SenderDomainHealth).filter(SenderDomainHealth.domain == domain).first()
    if row:
        return row
    row = SenderDomainHealth(domain=domain)
    db.add(row)
    db.flush()
    return row


def run_deliverability_check(db: Session) -> dict:
    now = utcnow()
    mailbox = ensure_sender_mailbox(db)
    domain = ensure_domain_health(db)
    if mailbox:
        mailbox.last_health_check_at = now
        mailbox.transport = get_value(db, "email_transport", mailbox.transport)
        mailbox.status = "ready" if get_value(db, "sender_readiness") in {"smtp_verified", "provider_verified", "canary_verified"} else "needs_verification"
    if domain:
        domain.last_checked_at = now
        if domain.domain == "gmail.com":
            domain.spf_status = "managed_by_google"
            domain.dkim_status = "managed_by_google"
            domain.dmarc_status = "not_owner_controlled"
            domain.alignment_status = "google_managed"
            domain.postmaster_status = "not_owner_controlled"
            domain.reputation_bucket = "unknown"
        db.add(
            DeliverabilityCheck(
                sender_email=mailbox.email if mailbox else None,
                domain=domain.domain,
                check_type="sender_domain_baseline",
                status="warning" if domain.domain == "gmail.com" else "unknown",
                severity="warning" if domain.domain == "gmail.com" else "info",
                details_redacted="Consumer gmail.com domain is Google-managed; use a controlled Workspace domain for Postmaster-level diagnosis."
                if domain.domain == "gmail.com"
                else "DNS/Postmaster status requires provider integration or manual capture.",
                checked_at=now,
            )
        )
    emit_event(db, "deliverability.checked", entity_type="sender_domain", entity_id=domain.domain if domain else None, payload={"sender": sender_email(db), "domain": sender_domain(db)})
    return deliverability_summary(db)


def recipient_domain_policy(db: Session, contact: Contact | None) -> dict:
    sender = sender_email(db)
    if not sender or not contact:
        return {"allowed": True, "reason_code": None, "status": "unknown", "used": 0, "daily_cap": None}
    domain = email_domain(contact.email)
    if not domain:
        return {"allowed": False, "reason_code": "RECIPIENT_DOMAIN_UNKNOWN", "status": "blocked", "used": 0, "daily_cap": None}
    today = utcnow().date().isoformat()
    cap = (
        db.query(RecipientDomainCap)
        .filter(RecipientDomainCap.sender_email == sender, RecipientDomainCap.recipient_domain == domain, RecipientDomainCap.window_date == today)
        .first()
    )
    one_day = utcnow() - timedelta(days=1)
    sent_today = (
        db.query(SendAttempt)
        .join(Contact, Contact.id == SendAttempt.contact_id)
        .filter(SendAttempt.provider_accepted.is_(True), SendAttempt.sent_at >= one_day, Contact.email.ilike(f"%@{domain}"))
        .count()
    )
    daily_cap = cap.daily_cap if cap else 10
    if cap and cap.status == "blocked":
        return {"allowed": False, "reason_code": "RECIPIENT_DOMAIN_CAP_BLOCKED", "status": "blocked", "used": sent_today, "daily_cap": daily_cap}
    if sent_today >= daily_cap:
        return {"allowed": False, "reason_code": "RECIPIENT_DOMAIN_DAILY_CAP_EXCEEDED", "status": "cap_exceeded", "used": sent_today, "daily_cap": daily_cap}
    return {"allowed": True, "reason_code": None, "status": "active", "used": sent_today, "daily_cap": daily_cap}


def sender_domain_policy(db: Session) -> dict:
    domain = ensure_domain_health(db)
    mailbox = ensure_sender_mailbox(db)
    if not mailbox:
        return {"allowed": True, "reason_code": None, "status": "missing_sender", "severity": "warn"}
    if mailbox.status in BLOCK_DOMAIN_STATUSES:
        return {"allowed": False, "reason_code": "SENDER_MAILBOX_UNHEALTHY", "status": mailbox.status, "severity": "block"}
    if domain and any(getattr(domain, attr) in BLOCK_DOMAIN_STATUSES for attr in ("spf_status", "dkim_status", "dmarc_status", "alignment_status", "reputation_bucket")):
        return {"allowed": False, "reason_code": "SENDER_DOMAIN_UNHEALTHY", "status": "domain_unhealthy", "severity": "block"}
    return {"allowed": True, "reason_code": None, "status": mailbox.status, "severity": "allow" if mailbox.status == "ready" else "warn"}


def mailbox_ramp_policy(db: Session) -> dict:
    mailbox = ensure_sender_mailbox(db)
    if not mailbox:
        return {"allowed": True, "reason_code": None, "status": "missing_sender", "sent_today": 0, "effective_cap": None}
    one_day = utcnow() - timedelta(days=1)
    sent_today = (
        db.query(SendAttempt)
        .filter(SendAttempt.provider_accepted.is_(True), SendAttempt.sender_identity == mailbox.email, SendAttempt.sent_at >= one_day)
        .count()
    )
    stage_cap = RAMP_STAGE_DAILY_CAPS.get(mailbox.ramp_stage, RAMP_STAGE_DAILY_CAPS["low_volume"])
    mailbox_cap = max(mailbox.daily_cap or stage_cap, 1)
    effective_cap = min(mailbox_cap, stage_cap)
    if sent_today >= effective_cap:
        return {
            "allowed": False,
            "reason_code": "MAILBOX_RAMP_CAP_EXCEEDED",
            "status": "cap_exceeded",
            "sent_today": sent_today,
            "effective_cap": effective_cap,
            "ramp_stage": mailbox.ramp_stage,
            "stage_cap": stage_cap,
            "mailbox_cap": mailbox_cap,
        }
    return {
        "allowed": True,
        "reason_code": None,
        "status": "active",
        "sent_today": sent_today,
        "effective_cap": effective_cap,
        "ramp_stage": mailbox.ramp_stage,
        "stage_cap": stage_cap,
        "mailbox_cap": mailbox_cap,
    }


def _sender_signal_rate_policy(db: Session, signal: str, threshold: float, reason_code: str) -> dict:
    sender = sender_email(db)
    since = utcnow() - timedelta(days=RATE_WINDOW_DAYS)
    sent_query = db.query(SendAttempt).filter(SendAttempt.provider_accepted.is_(True), SendAttempt.sent_at >= since)
    if sender:
        sent_query = sent_query.filter(SendAttempt.sender_identity == sender)
    sent_count = sent_query.count()
    signal_count = (
        db.query(Reply)
        .filter(Reply.classified_as == signal, Reply.archived_at.is_(None), Reply.received_at >= since)
        .count()
    )
    denominator = max(sent_count, 1)
    rate = signal_count / denominator
    if signal_count and rate > threshold:
        return {
            "allowed": False,
            "reason_code": reason_code,
            "status": "threshold_exceeded",
            "count": signal_count,
            "sent_count": sent_count,
            "rate": round(rate, 6),
            "threshold": threshold,
            "window_days": RATE_WINDOW_DAYS,
        }
    return {
        "allowed": True,
        "reason_code": None,
        "status": "active",
        "count": signal_count,
        "sent_count": sent_count,
        "rate": round(rate, 6),
        "threshold": threshold,
        "window_days": RATE_WINDOW_DAYS,
    }


def bounce_rate_policy(db: Session) -> dict:
    return _sender_signal_rate_policy(db, "bounce", BOUNCE_RATE_THRESHOLD, "BOUNCE_RATE_HIGH")


def complaint_rate_policy(db: Session) -> dict:
    return _sender_signal_rate_policy(db, "complaint", COMPLAINT_RATE_THRESHOLD, "COMPLAINT_RATE_HIGH")


def deferral_policy(db: Session) -> dict:
    one_hour = utcnow() - timedelta(hours=1)
    recent_deferrals = (
        db.query(SendAttempt)
        .filter(SendAttempt.status == "failed", SendAttempt.error_code.in_(["temporary_failure", "rate_limited", "gmail_deferral", "deferral"]), SendAttempt.sent_at >= one_hour)
        .count()
    )
    if recent_deferrals >= 3:
        return {"allowed": False, "reason_code": "RECENT_DEFERRAL_SPIKE", "count": recent_deferrals}
    return {"allowed": True, "reason_code": None, "count": recent_deferrals}


def deliverability_policy(db: Session, contact: Contact | None) -> dict:
    sender_policy = sender_domain_policy(db)
    domain_policy = recipient_domain_policy(db, contact)
    ramp = mailbox_ramp_policy(db)
    bounce_rate = bounce_rate_policy(db)
    complaint_rate = complaint_rate_policy(db)
    deferral = deferral_policy(db)
    checks = {
        "sender_domain_healthy": sender_policy,
        "per_domain_daily_cap": domain_policy,
        "per_mailbox_ramp_stage": ramp,
        "bounce_rate_under_threshold": bounce_rate,
        "complaint_rate_under_threshold": complaint_rate,
        "no_recent_deferral_spike": deferral,
    }
    reasons = [check["reason_code"] for check in checks.values() if check.get("reason_code")]
    return {"allowed": not reasons, "reasons": reasons, "checks": checks}


def deliverability_summary(db: Session) -> dict:
    mailbox = ensure_sender_mailbox(db)
    domain = ensure_domain_health(db)
    checks = db.query(DeliverabilityCheck).order_by(DeliverabilityCheck.checked_at.desc()).limit(20).all()
    inbox_tests = db.query(InboxPlacementTest).order_by(InboxPlacementTest.created_at.desc()).limit(10).all()
    caps = db.query(RecipientDomainCap).order_by(RecipientDomainCap.updated_at.desc()).limit(20).all()
    policy = deliverability_policy(db, None)
    return {
        "sender": sender_email(db),
        "domain": sender_domain(db),
        "mailbox": mailbox_to_dict(mailbox) if mailbox else None,
        "domain_health": domain_health_to_dict(domain, sender_domain(db)),
        "checks": [check_to_dict(row) for row in checks],
        "inbox_tests": [inbox_test_to_dict(row) for row in inbox_tests],
        "recipient_domain_caps": [cap_to_dict(row) for row in caps],
        "policy": policy,
    }


def create_inbox_placement_test(db: Session, seed_email: str, subject: str) -> InboxPlacementTest:
    sender = sender_email(db)
    row = InboxPlacementTest(
        sender_email=sender,
        seed_email=seed_email.strip().lower(),
        recipient_domain=email_domain(seed_email),
        subject=subject,
        status="planned",
        placement="unknown",
        details_redacted="Planned seed test. No email is sent by this endpoint.",
    )
    db.add(row)
    emit_event(db, "deliverability.inbox_test_planned", entity_type="inbox_placement_test", entity_id=row.id, payload={"seed_domain": row.recipient_domain})
    return row


def policy_trace(db: Session) -> dict:
    return {
        "sender": sender_email(db),
        "domain": sender_domain(db),
        "policy": deliverability_policy(db, None),
        "raw": json.dumps({"note": "Trace intentionally omits provider secrets and raw message headers."}),
    }
