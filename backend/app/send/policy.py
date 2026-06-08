from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.contacts.utils import is_domain_blocked, send_window_open
from app.db.models import Draft, Reply, SendAttempt, SendQueue, Suppression
from app.drafts.service import draft_campaign_block_reason, draft_content_block_reasons
from app.deliverability.service import deliverability_policy
from app.enrichment.service import evidence_policy_for_draft
from app.settings.service import get_bool, get_effective_daily_send_cap, get_int, get_value
from app.verification.service import verification_policy_for_contact


@dataclass
class GateResult:
    gate: str
    passed: bool
    reason_code: str | None = None
    details: dict | None = None


@dataclass
class PolicyDecision:
    all_passed: bool
    gates: list[GateResult]
    block_reason_codes: list[str]


def _gate(name: str, passed: bool, reason: str, details: dict | None = None) -> GateResult:
    return GateResult(name, passed, None if passed else reason, details or {})


def prequeue_block_reasons(contact, db: Session) -> list[str]:
    verification_policy = verification_policy_for_contact(db, contact)
    if verification_policy["allowed"]:
        return []
    return [verification_policy["reason_code"] or "RECIPIENT_EMAIL_UNSAFE"]


def evaluate_policy(queue_entry: SendQueue, db: Session) -> PolicyDecision:
    contact = queue_entry.contact
    draft = db.get(Draft, queue_entry.draft_id)
    now = utcnow()
    one_day = now - timedelta(days=1)
    one_hour = now - timedelta(hours=1)
    success_attempts = db.query(SendAttempt).filter(SendAttempt.provider_accepted.is_(True))
    sent_today = success_attempts.filter(SendAttempt.sent_at >= one_day).count()
    sent_hour = success_attempts.filter(SendAttempt.sent_at >= one_hour).count()
    daily_cap = get_effective_daily_send_cap(db)
    hourly_cap = get_int(db, "hourly_send_cap")
    send_delay_s = get_int(db, "send_delay_s")
    last_success = success_attempts.order_by(SendAttempt.sent_at.desc()).first()
    last_sent_at = last_success.sent_at if last_success else None
    window_ok = True
    if last_sent_at and send_delay_s > 0:
        window_ok = (now - last_sent_at.replace(tzinfo=now.tzinfo)).total_seconds() >= send_delay_s

    existing_success = (
        db.query(SendAttempt)
        .filter(
            SendAttempt.idempotency_key == queue_entry.idempotency_key,
            SendAttempt.provider_accepted.is_(True),
        )
        .first()
    )
    suppression = db.query(Suppression).filter(Suppression.email == contact.email).first()
    active_reply = (
        db.query(Reply)
        .filter(Reply.contact_id == contact.id, Reply.archived_at.is_(None), Reply.classified_as.in_(["reply", "unsubscribe"]))
        .first()
    )
    verification_policy = verification_policy_for_contact(db, contact)
    deliverability = deliverability_policy(db, contact)
    deliverability_checks = deliverability["checks"]
    evidence_policy = evidence_policy_for_draft(db, draft, contact)

    gates = [
        _gate(
            "sender_verified",
            get_value(db, "sender_readiness") in {"smtp_verified", "provider_verified", "canary_verified"} or get_bool(db, "canary_verified"),
            "SENDER_NOT_VERIFIED",
        ),
        _gate("canary_verified", get_bool(db, "canary_verified"), "CANARY_NOT_VERIFIED"),
        _gate("draft_approved", bool(draft and draft.approved and draft.approved_at), "DRAFT_NOT_APPROVED"),
        _gate(
            "draft_contact_matches_queue",
            bool(draft and draft.contact_id == contact.id),
            "DRAFT_CONTACT_MISMATCH",
        ),
        _gate(
            "draft_content_sendable",
            not draft_content_block_reasons(draft, contact),
            (draft_content_block_reasons(draft, contact) or ["DRAFT_CONTENT_INVALID"])[0],
        ),
        _gate(
            "campaign_active",
            draft_campaign_block_reason(db, draft) is None,
            draft_campaign_block_reason(db, draft) or "CAMPAIGN_NOT_ACTIVE",
        ),
        _gate("contact_not_deleted", getattr(contact, "deleted_at", None) is None, "CONTACT_DELETED"),
        _gate(
            "no_suppression",
            not suppression and not is_domain_blocked(db, contact.email),
            "RECIPIENT_SUPPRESSED",
        ),
        _gate(
            "no_bounce",
            not db.query(Reply)
            .filter(Reply.contact_id == contact.id, Reply.archived_at.is_(None), Reply.classified_as.in_(["bounce", "complaint"]))
            .first(),
            "RECIPIENT_BOUNCED",
        ),
        _gate(
            "no_reply",
            contact.status not in {"replied", "unsubscribed"} and active_reply is None,
            "RECIPIENT_REPLIED",
        ),
        _gate("no_pause", contact.status != "manually_paused", "RECIPIENT_MANUALLY_PAUSED"),
        _gate("cap_daily", sent_today < daily_cap, "DAILY_CAP_EXCEEDED"),
        _gate("cap_hourly", sent_hour < hourly_cap, "HOURLY_CAP_EXCEEDED"),
        _gate("send_window_open", send_window_open(db, now), "SEND_WINDOW_NOT_ELAPSED"),
        _gate("window_ok", window_ok, "SEND_WINDOW_NOT_ELAPSED"),
        _gate("recipient_email_verified", verification_policy["allowed"], verification_policy["reason_code"] or "RECIPIENT_EMAIL_UNSAFE"),
        _gate("draft_evidence_supported", not evidence_policy["neutral_copy_required"], "UNSUPPORTED_PERSONALIZATION"),
        _gate(
            "sender_domain_healthy",
            deliverability_checks["sender_domain_healthy"]["allowed"],
            deliverability_checks["sender_domain_healthy"]["reason_code"] or "SENDER_DOMAIN_UNHEALTHY",
            deliverability_checks["sender_domain_healthy"],
        ),
        _gate(
            "per_domain_daily_cap",
            deliverability_checks["per_domain_daily_cap"]["allowed"],
            deliverability_checks["per_domain_daily_cap"]["reason_code"] or "RECIPIENT_DOMAIN_DAILY_CAP_EXCEEDED",
            deliverability_checks["per_domain_daily_cap"],
        ),
        _gate(
            "per_mailbox_ramp_stage",
            deliverability_checks["per_mailbox_ramp_stage"]["allowed"],
            deliverability_checks["per_mailbox_ramp_stage"]["reason_code"] or "MAILBOX_RAMP_CAP_EXCEEDED",
            deliverability_checks["per_mailbox_ramp_stage"],
        ),
        _gate(
            "bounce_rate_under_threshold",
            deliverability_checks["bounce_rate_under_threshold"]["allowed"],
            deliverability_checks["bounce_rate_under_threshold"]["reason_code"] or "BOUNCE_RATE_HIGH",
            deliverability_checks["bounce_rate_under_threshold"],
        ),
        _gate(
            "complaint_rate_under_threshold",
            deliverability_checks["complaint_rate_under_threshold"]["allowed"],
            deliverability_checks["complaint_rate_under_threshold"]["reason_code"] or "COMPLAINT_RATE_HIGH",
            deliverability_checks["complaint_rate_under_threshold"],
        ),
        _gate(
            "no_recent_deferral_spike",
            deliverability_checks["no_recent_deferral_spike"]["allowed"],
            deliverability_checks["no_recent_deferral_spike"]["reason_code"] or "RECENT_DEFERRAL_SPIKE",
            deliverability_checks["no_recent_deferral_spike"],
        ),
        _gate("idempotency_ok", existing_success is None, "IDEMPOTENCY_DUPLICATE"),
    ]
    reasons = [gate.reason_code for gate in gates if gate.reason_code]
    return PolicyDecision(all_passed=not reasons, gates=gates, block_reason_codes=reasons)


def store_policy_result(queue_entry: SendQueue, decision: PolicyDecision) -> None:
    queue_entry.policy_block_reasons = json.dumps(decision.block_reason_codes)


def policy_trace_to_dict(decision: PolicyDecision) -> list[dict]:
    return [
        {
            "gate": gate.gate,
            "passed": gate.passed,
            "reason_code": gate.reason_code,
            "details": gate.details or {},
        }
        for gate in decision.gates
    ]
