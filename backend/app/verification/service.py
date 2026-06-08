from __future__ import annotations

import json
import re
import time
from datetime import timedelta

from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.core.time import utcnow
from app.db.models import Contact, EmailVerification, EmailVerificationAttempt, Reply, Suppression
from app.verification.adapters import VerificationProviderAdapter, VerificationProviderResult

ALLOW_STATUSES = {"verified", "valid"}
WARN_STATUSES = {"catch_all", "role_based", "unknown"}
BLOCK_STATUSES = {"invalid", "disposable", "stale", "hard_bounce", "suppressed"}
ROLE_LOCAL_PARTS = {
    "admin",
    "abuse",
    "billing",
    "compliance",
    "contact",
    "help",
    "hr",
    "info",
    "legal",
    "marketing",
    "no-reply",
    "noreply",
    "postmaster",
    "sales",
    "support",
    "team",
}
DISPOSABLE_DOMAINS = {
    "10minutemail.com",
    "guerrillamail.com",
    "mailinator.com",
    "tempmail.com",
    "temp-mail.org",
    "yopmail.com",
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email_domain(email: str | None) -> str:
    value = (email or "").strip().lower()
    return value.rsplit("@", 1)[-1] if "@" in value else ""


def verification_to_dict(row: EmailVerification | None) -> dict:
    if row is None:
        return {
            "id": None,
            "contact_id": None,
            "email": "",
            "status": "unknown",
            "confidence": 0.0,
            "provider": "none",
            "provider_status": "not_checked",
            "is_role_based": False,
            "is_disposable": False,
            "is_catch_all": False,
            "mx_present": False,
            "last_verified_at": None,
            "expires_at": None,
            "raw_response_redacted": None,
        }
    return {
        "id": row.id,
        "contact_id": row.contact_id,
        "email": row.email,
        "status": row.status,
        "confidence": row.confidence,
        "provider": row.provider,
        "provider_status": row.provider_status,
        "is_role_based": row.is_role_based,
        "is_disposable": row.is_disposable,
        "is_catch_all": row.is_catch_all,
        "mx_present": row.mx_present,
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "raw_response_redacted": row.raw_response_redacted,
    }


def attempts_for_verification(db: Session, verification_id: str | None) -> list[dict]:
    if not verification_id:
        return []
    rows = (
        db.query(EmailVerificationAttempt)
        .filter(EmailVerificationAttempt.verification_id == verification_id)
        .order_by(EmailVerificationAttempt.created_at.desc())
        .limit(10)
        .all()
    )
    return [
        {
            "id": row.id,
            "provider": row.provider,
            "status": row.status,
            "response_code": row.response_code,
            "latency_ms": row.latency_ms,
            "cost_units": row.cost_units,
            "raw_response_redacted": row.raw_response_redacted,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


def get_verification_for_contact(db: Session, contact_or_id: Contact | str | None) -> EmailVerification | None:
    if contact_or_id is None:
        return None
    if isinstance(contact_or_id, Contact):
        contact = contact_or_id
    else:
        contact = db.get(Contact, contact_or_id)
    if not contact:
        return None
    return (
        db.query(EmailVerification)
        .filter(EmailVerification.email == contact.email.strip().lower())
        .order_by(EmailVerification.updated_at.desc())
        .first()
    )


def classify_email_status(email: str, db: Session, contact_id: str | None = None) -> dict:
    normalized = email.strip().lower()
    local = normalized.split("@", 1)[0] if "@" in normalized else ""
    domain = email_domain(normalized)
    suppression = db.query(Suppression).filter(Suppression.email == normalized).first()
    bounced = False
    if contact_id:
        bounced = bool(
            db.query(Reply)
            .filter(Reply.contact_id == contact_id, Reply.archived_at.is_(None), Reply.classified_as.in_(["bounce", "complaint"]))
            .first()
        )
    is_role = local in ROLE_LOCAL_PARTS
    is_disposable = domain in DISPOSABLE_DOMAINS
    domain_shape_valid = bool(domain and "." in domain)
    mx_present = False
    if suppression:
        status = "suppressed"
        confidence = 1.0
        provider_status = "suppression_match"
    elif bounced:
        status = "hard_bounce"
        confidence = 1.0
        provider_status = "prior_bounce_or_complaint"
    elif not EMAIL_RE.match(normalized) or not domain_shape_valid:
        status = "invalid"
        confidence = 0.95
        provider_status = "syntax_or_domain_failed"
    elif is_disposable:
        status = "disposable"
        confidence = 0.9
        provider_status = "disposable_domain"
    elif is_role:
        status = "role_based"
        confidence = 0.6
        provider_status = "role_address_syntax_only"
    else:
        status = "unknown"
        confidence = 0.5
        provider_status = "syntax_passed_domain_unverified"
    return {
        "status": status,
        "confidence": confidence,
        "provider_status": provider_status,
        "is_role_based": is_role,
        "is_disposable": is_disposable,
        "is_catch_all": False,
        "mx_present": mx_present,
        "raw_response_redacted": json.dumps({"domain": domain, "rule": provider_status}, sort_keys=True),
    }


class InternalRulesVerificationAdapter:
    name = "finimatic_rules"

    def verify(self, db: Session, contact: Contact) -> VerificationProviderResult:
        classified = classify_email_status(contact.email.strip().lower(), db, contact.id)
        return VerificationProviderResult(
            provider=self.name,
            status=classified["status"],
            provider_status=classified["provider_status"],
            confidence=classified["confidence"],
            is_role_based=classified["is_role_based"],
            is_disposable=classified["is_disposable"],
            is_catch_all=classified["is_catch_all"],
            mx_present=classified["mx_present"],
            raw_response_redacted=classified["raw_response_redacted"],
        )


def _get_or_create_row(db: Session, contact: Contact, normalized: str) -> EmailVerification:
    row = db.query(EmailVerification).filter(EmailVerification.email == normalized).first()
    if row is None:
        row = EmailVerification(email=normalized, contact_id=contact.id)
        db.add(row)
        db.flush()
    row.contact_id = contact.id
    return row


def _apply_provider_result(row: EmailVerification, result: VerificationProviderResult, now) -> None:
    row.status = result.status
    row.confidence = result.confidence
    row.provider = result.provider
    row.provider_status = result.provider_status
    row.is_role_based = result.is_role_based
    row.is_disposable = result.is_disposable
    row.is_catch_all = result.is_catch_all
    row.mx_present = result.mx_present
    row.last_verified_at = now
    row.expires_at = now + timedelta(days=30)
    row.raw_response_redacted = result.raw_response_redacted


def _record_attempt(
    db: Session,
    row: EmailVerification,
    *,
    provider: str,
    status: str,
    response_code: str | None,
    latency_ms: int,
    cost_units: int = 0,
    raw_response_redacted: str | None = None,
) -> None:
    db.add(
        EmailVerificationAttempt(
            verification_id=row.id,
            provider=provider,
            status=status,
            response_code=response_code,
            latency_ms=latency_ms,
            cost_units=cost_units,
            raw_response_redacted=raw_response_redacted,
        )
    )


def run_verification_waterfall(
    db: Session,
    contact: Contact,
    adapters: list[VerificationProviderAdapter] | None = None,
) -> EmailVerification:
    normalized = contact.email.strip().lower()
    row = _get_or_create_row(db, contact, normalized)
    active_adapters = adapters if adapters is not None else [InternalRulesVerificationAdapter()]
    now = utcnow()
    had_result = False

    for adapter in active_adapters:
        started = time.perf_counter()
        try:
            result = adapter.verify(db, contact)
        except Exception as exc:
            latency_ms = max(int((time.perf_counter() - started) * 1000), 0)
            _record_attempt(
                db,
                row,
                provider=getattr(adapter, "name", adapter.__class__.__name__),
                status="provider_error",
                response_code=exc.__class__.__name__,
                latency_ms=latency_ms,
                raw_response_redacted=json.dumps({"error_code": exc.__class__.__name__}, sort_keys=True),
            )
            continue

        latency_ms = max(int((time.perf_counter() - started) * 1000), 0)
        _apply_provider_result(row, result, now)
        _record_attempt(
            db,
            row,
            provider=result.provider,
            status=result.status,
            response_code=result.provider_status,
            latency_ms=latency_ms,
            cost_units=result.cost_units,
            raw_response_redacted=result.raw_response_redacted,
        )
        had_result = True
        if result.status != "unknown":
            break

    if not had_result:
        row.status = "unknown"
        row.confidence = 0.0
        row.provider = "none"
        row.provider_status = "provider_unavailable"
        row.is_role_based = False
        row.is_disposable = False
        row.is_catch_all = False
        row.mx_present = False
        row.last_verified_at = now
        row.expires_at = now + timedelta(days=30)
        row.raw_response_redacted = json.dumps({"rule": "provider_unavailable"}, sort_keys=True)

    emit_event(db, "verification.completed", entity_type="contact", entity_id=contact.id, payload={"email": normalized, "status": row.status})
    return row


def run_local_verification(db: Session, contact: Contact) -> EmailVerification:
    return run_verification_waterfall(db, contact)


def verification_policy_for_contact(db: Session, contact: Contact | None) -> dict:
    if not contact:
        return {"status": "unknown", "severity": "warn", "reason_code": "RECIPIENT_EMAIL_UNKNOWN", "allowed": True}
    row = get_verification_for_contact(db, contact)
    if row is None:
        return {"status": "unknown", "severity": "warn", "reason_code": "RECIPIENT_EMAIL_UNKNOWN", "allowed": True}
    now = utcnow()
    status = row.status
    if row.expires_at and row.expires_at.replace(tzinfo=now.tzinfo) < now:
        status = "stale"
    if status in ALLOW_STATUSES:
        return {"status": status, "severity": "allow", "reason_code": None, "allowed": True}
    if status in WARN_STATUSES:
        return {"status": status, "severity": "warn", "reason_code": f"RECIPIENT_EMAIL_{status.upper()}", "allowed": True}
    return {"status": status, "severity": "block", "reason_code": f"RECIPIENT_EMAIL_{status.upper()}", "allowed": False}


def list_verification_rows(db: Session) -> list[dict]:
    rows = db.query(EmailVerification).order_by(EmailVerification.updated_at.desc()).all()
    return [verification_to_dict(row) for row in rows]


def verification_summary(db: Session) -> dict:
    rows = db.query(EmailVerification).all()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    total_contacts = db.query(Contact).filter(Contact.deleted_at.is_(None)).count()
    verified = counts.get("verified", 0) + counts.get("valid", 0)
    blocked = sum(counts.get(status, 0) for status in BLOCK_STATUSES)
    warnings = sum(counts.get(status, 0) for status in WARN_STATUSES)
    return {
        "total_contacts": total_contacts,
        "verified_or_valid": verified,
        "warnings": warnings,
        "blocked": blocked,
        "unchecked": max(total_contacts - len(rows), 0),
        "counts": counts,
    }
