from __future__ import annotations

import re
from datetime import datetime


HEX_ID_PATTERN = re.compile(r"\b[0-9a-f]{32}\b")
ISO_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)

TERM_TRANSLATIONS = {
    "conversation_active": "replied and needs your response",
    "pending_approval": "waiting for your approval",
    "follow_up_stopped": "no longer receiving follow-ups",
    "suppressed": "opted out",
    "unsubscribed": "asked to be removed",
    "imported": "new contact (not yet emailed)",
    "needs_review": "needs review",
    "draft_needed": "needs a draft",
    "draft_ready": "draft is ready",
    "approved": "draft approved",
    "queued": "email scheduled to send",
    "sent": "email accepted by the provider",
    "blocked": "blocked by a sending rule",
    "blocked_by_policy": "blocked by a sending rule",
    "draft_not_approved": "draft not approved yet",
    "manually_paused": "paused by you",
    "follow_up_due": "follow-up is due",
    "complete": "campaign finished",
    "bounced": "email could not be delivered",
    "positive_interest": "interested",
    "negative_no": "not interested",
    "objection": "has concerns",
    "question": "asked a question",
    "auto_reply": "automated out-of-office",
    "classified_as": "",
    "contact_id": "",
    "draft_id": "",
    "params_hash": "",
    "idempotency_key": "",
    "sequence_num": "",
    "action_class": "",
    "privacy_class": "",
    "provider_msg_id": "",
    "batch_id": "",
    "send_attempt": "email send",
    "queue_entry": "scheduled email",
    "audit_event": "event",
    "reason_code": "reason",
}

BANNED_FIELD_PATTERNS = [
    re.compile(r"^\s*[a-z_]+_id\s*[:=]", re.IGNORECASE),
    re.compile(r"^\s*params_hash\s*[:=]", re.IGNORECASE),
    re.compile(r"^\s*idempotency_key\s*[:=]", re.IGNORECASE),
    re.compile(r"^\s*sequence_num\s*[:=]", re.IGNORECASE),
    re.compile(r"^\s*action_class\s*[:=]", re.IGNORECASE),
    re.compile(r"^\s*privacy_class\s*[:=]", re.IGNORECASE),
]


def _humanize_iso_match(m: re.Match) -> str:
    raw = m.group(0).replace("Z", "+00:00").replace(" ", "T")
    try:
        value = datetime.fromisoformat(raw)
    except Exception:
        return "recently"
    if value.tzinfo is not None:
        value = value.replace(tzinfo=None)
    delta = datetime.utcnow() - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    if seconds < 172800:
        return "yesterday"
    days = seconds // 86400
    return f"{days} days ago"


def format_for_layman(response: str, contact_map: dict[str, str] | None = None) -> str:
    """
    Steps:
    1. Replace known 32-char hex IDs with display names from contact_map
    2. Replace any remaining bare 32-char hex IDs with "[contact]"
    3. Replace ISO timestamps with relative time
    4. Replace status codes and technical terms using TERM_TRANSLATIONS
    5. Filter lines that match BANNED_FIELD_PATTERNS entirely
    6. Collapse 3+ consecutive blank lines to 2
    7. Return stripped result
    """
    result = str(response or "")
    if contact_map:
        for contact_id, display in contact_map.items():
            result = result.replace(str(contact_id), str(display))
    result = HEX_ID_PATTERN.sub("[contact]", result)
    result = ISO_PATTERN.sub(_humanize_iso_match, result)

    for technical, plain in TERM_TRANSLATIONS.items():
        if plain:
            if "_" in technical:
                result = re.sub(r"\b" + re.escape(technical) + r"\b", plain, result, flags=re.IGNORECASE)
            else:
                result = re.sub(
                    r"(\b(?:status|classification|classified as|reply classification|reason)\s*(?:is|:|=)\s*)"
                    + re.escape(technical)
                    + r"\b",
                    lambda match: match.group(1) + plain,
                    result,
                    flags=re.IGNORECASE,
                )
        else:
            result = re.sub(r"\b" + re.escape(technical) + r"\b\s*[:=]?\s*", "", result, flags=re.IGNORECASE)

    lines = []
    for line in result.splitlines():
        if any(pattern.match(line) for pattern in BANNED_FIELD_PATTERNS):
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def build_contact_name_map(contacts: list) -> dict[str, str]:
    result: dict[str, str] = {}
    for contact in contacts:
        contact_id = str(getattr(contact, "id", "") or "")
        if not HEX_ID_PATTERN.fullmatch(contact_id):
            continue
        email = str(getattr(contact, "email", "") or "")
        name = (
            getattr(contact, "creator_name", None)
            or getattr(contact, "business_name", None)
            or (email.split("@")[0] if email else "contact")
        )
        result[contact_id] = f"{name} ({email})" if email else str(name)
    return result
