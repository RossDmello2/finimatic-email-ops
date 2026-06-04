from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditEvent, Contact, Draft, FollowUpSequence, PendingEmailActionRow, Reply, SendQueue, Suppression


SECRET_WORDS = ("password", "secret", "token", "key", "credential")
SECRET_PREFIXES = ("g" + "sk_", "AI" + "za")
SECRET_VALUE_RE = re.compile(
    "|".join(
        [
            r"gs" + r"k_[A-Za-z0-9_\-]+",
            r"AI" + r"za[A-Za-z0-9_\-]+",
            r"gAAAA[A-Za-z0-9_\-=]{20,}",
            r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+",
            r"(password|secret|token|api[_-]?key|credential)\s*[:=]\s*[^,\s;]+",
        ]
    )
    ,
    re.IGNORECASE,
)


def redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if any(word in key.lower() for word in SECRET_WORDS):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, str):
        if value.startswith(SECRET_PREFIXES):
            return "<redacted>"
        return SECRET_VALUE_RE.sub("<redacted>", value)
    return value


def emit_event(
    db: Session,
    event_type: str,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor: str = "system",
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        payload=json.dumps(redact_payload(payload or {}), sort_keys=True),
    )
    db.add(event)
    return event


EVENT_LABELS = {
    "settings.updated": "Settings updated",
    "import.preview": "Import checked",
    "import.committed": "Contacts imported",
    "import.row_rejected": "Import row rejected",
    "contact.restored": "Contact restored",
    "contact.enriched": "Contact enriched",
    "draft.created": "Draft saved",
    "draft.edited": "Draft edited",
    "draft.ai_generated": "AI draft generated",
    "draft.ai_failed": "AI draft failed",
    "draft.approved": "Draft approved",
    "queue.entry_created": "Email queued",
    "queue.entry_requeued": "Email requeued",
    "queue.policy_evaluated": "Sending rules checked",
    "queue.temporarily_deferred": "Email deferred",
    "queue.gate_blocked": "Email blocked",
    "send.attempt": "Delivery attempted",
    "send.success": "Email sent",
    "send.failed": "Email delivery failed",
    "send.dry_run_blocked": "Dry Run blocked delivery",
    "followup.due_calculated": "Next follow-up scheduled",
    "followup.stopped": "Follow-up stopped",
    "followup.draft_generated": "Follow-up draft generated",
    "followup.draft_failed": "Follow-up draft failed",
    "reply.received": "Reply received",
    "reply.classified": "Reply classified",
    "reply.archived": "Reply archived",
    "reply.restored": "Reply restored",
    "reply.deleted": "Reply deleted",
    "suppression.added": "Recipient opted out",
    "suppression.removed": "Opt-out removed",
    "sender.smtp_verified": "Email provider verified",
    "sender.smtp_failed": "Email provider failed verification",
    "gmail_api.oauth_connected": "Gmail API connected",
    "gmail_api.oauth_failed": "Gmail API connection failed",
    "canary.attempt": "Canary test started",
    "canary.success": "Canary verified",
    "canary.duplicate_blocked": "Duplicate canary blocked",
    "agent.goal_framed": "Assistant understood request",
    "agent.intent_resolved": "Assistant chose next step",
    "agent.slots_filled": "Assistant collected details",
    "agent.tool_executed": "Assistant checked data",
    "agent.confirmation_created": "Assistant prepared send confirmation",
    "agent.confirmation_valid": "Send confirmation accepted",
    "agent.confirmation_invalid": "Send confirmation rejected",
    "agent.confirmation_expired": "Send confirmation expired",
    "agent.send_executed": "Assistant sent email",
    "agent.send_failed": "Assistant send failed",
    "agent.session_cancelled": "Assistant action cancelled",
    "agent.awareness_answered": "Assistant answered a question",
    "agent.capability_denied": "Assistant refused an unsafe request",
    "agent.clarification_asked": "Assistant asked for clarification",
}

SECRET_DETAIL_KEYS = {"changed_keys", "key", "token", "secret", "credential", "password", "idempotency_key", "params_hash"}


def audit_to_dict(event: AuditEvent, db: Session | None = None) -> dict[str, Any]:
    payload = json.loads(event.payload or "{}")
    context = _audit_context(event, payload, db) if db is not None else {}
    event_label = EVENT_LABELS.get(event.event_type, _plain_event_label(event.event_type))
    detail = _audit_detail(event_label, event, payload, context)
    return {
        "id": event.id,
        "event_type": event.event_type,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "actor": event.actor,
        "payload": payload,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "event_label": event_label,
        "entity_label": context.get("entity_label"),
        "contact_name": context.get("contact_name"),
        "contact_email": context.get("contact_email"),
        "detail": detail,
    }


def _audit_context(event: AuditEvent, payload: dict[str, Any], db: Session | None) -> dict[str, str | None]:
    if db is None:
        return {}
    contact: Contact | None = None
    draft: Draft | None = None
    email: str | None = None
    entity_label: str | None = None

    if event.entity_type == "contact" and event.entity_id:
        contact = db.get(Contact, event.entity_id)
    elif event.entity_type == "draft" and event.entity_id:
        draft = db.get(Draft, event.entity_id)
        contact = db.get(Contact, draft.contact_id) if draft else None
        entity_label = f"Draft: {draft.subject}" if draft else None
    elif event.entity_type == "send_queue" and event.entity_id:
        queue = db.get(SendQueue, event.entity_id)
        if queue:
            contact = db.get(Contact, queue.contact_id)
            draft = db.get(Draft, queue.draft_id)
            entity_label = f"Queue item #{queue.sequence_num}"
    elif event.entity_type == "follow_up_sequence" and event.entity_id:
        followup = db.get(FollowUpSequence, event.entity_id)
        if followup:
            contact = db.get(Contact, followup.contact_id)
            entity_label = f"Follow-up #{followup.sequence_num}"
    elif event.entity_type == "reply" and event.entity_id:
        reply = db.get(Reply, event.entity_id)
        contact = db.get(Contact, reply.contact_id) if reply else None
        entity_label = "Reply"
    elif event.entity_type == "pending_email_action" and event.entity_id:
        action = db.get(PendingEmailActionRow, event.entity_id)
        if action:
            contact = db.get(Contact, action.contact_id)
            draft = db.get(Draft, action.draft_id)
            entity_label = "Pending confirmation"
    elif event.entity_type == "suppression" and event.entity_id:
        suppression = db.get(Suppression, event.entity_id)
        if suppression:
            email = suppression.email
            contact = db.query(Contact).filter(Contact.email == email).first()
            entity_label = "Opt-out"

    if contact is None and payload.get("contact_id"):
        contact = db.get(Contact, str(payload["contact_id"]))
    if draft is None and payload.get("draft_id"):
        draft = db.get(Draft, str(payload["draft_id"]))
        if draft and contact is None:
            contact = db.get(Contact, draft.contact_id)
    if contact is None and payload.get("queue_id"):
        queue = db.get(SendQueue, str(payload["queue_id"]))
        contact = db.get(Contact, queue.contact_id) if queue else None
    if email is None:
        for key in ("email", "recipient", "sender", "gmail_user"):
            value = payload.get(key)
            if isinstance(value, str) and "@" in value:
                email = value
                break
    if contact is None and email:
        contact = db.query(Contact).filter(Contact.email == email).first()

    contact_email = contact.email if contact else email
    contact_name = (contact.creator_name or contact.business_name) if contact else None
    if draft and not entity_label:
        entity_label = f"Draft: {draft.subject}"
    if not entity_label:
        entity_label = _plain_event_label(event.entity_type or "system")
    return {
        "contact_name": contact_name,
        "contact_email": contact_email,
        "entity_label": entity_label,
    }


def _plain_event_label(value: str) -> str:
    words = re.sub(r"[_\.]+", " ", value or "event").strip()
    return words[:1].upper() + words[1:] if words else "Event"


def _audit_detail(event_label: str, event: AuditEvent, payload: dict[str, Any], context: dict[str, str | None]) -> str:
    who = _who_label(context)
    parts = [event_label]
    if who:
        parts.append(f"for {who}")
    hints = _payload_hints(payload)
    if hints:
        parts.append(f"({', '.join(hints)})")
    if event.entity_type and not who:
        parts.append(f"on {_plain_event_label(event.entity_type).lower()}")
    detail = " ".join(parts).strip()
    if not detail.endswith("."):
        detail += "."
    return detail


def _who_label(context: dict[str, str | None]) -> str:
    name = context.get("contact_name")
    email = context.get("contact_email")
    if name and email:
        return f"{name} ({email})"
    return name or email or ""


def _payload_hints(payload: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for key in ("provider", "model", "status", "error_code", "reason", "source"):
        value = payload.get(key)
        if value:
            hints.append(f"{_plain_event_label(key).lower()}: {sanitize_detail(value)}")
    reasons = payload.get("reasons")
    if isinstance(reasons, list) and reasons:
        hints.append("reason: " + ", ".join(sanitize_detail(item) for item in reasons[:3]))
    sequence = payload.get("sequence_num")
    if sequence:
        hints.append(f"follow-up #{sanitize_detail(sequence)}")
    return [hint for hint in hints if not any(secret in hint.lower() for secret in SECRET_DETAIL_KEYS)][:4]


def sanitize_detail(value: Any) -> str:
    clean = redact_payload(value)
    if isinstance(clean, (dict, list)):
        clean = json.dumps(clean, sort_keys=True)
    text = SECRET_VALUE_RE.sub("<redacted>", str(clean))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:90]
