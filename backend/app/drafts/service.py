from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.contacts.utils import resolve_tokens
from app.db.models import CampaignPlan, Contact, Draft, SendQueue
from app.send.stop_service import QUEUE_CANCELLABLE_STATUSES

UNRESOLVED_TOKEN_RE = re.compile(r"\{\{\s*[^{}]+\s*\}\}")
CAMPAIGN_NOTE_RE = re.compile(r"^campaign:([^:]+):step\d+$")


def draft_content_block_reasons(draft: Draft | None, contact: Contact | None = None) -> list[str]:
    if draft is None:
        return ["DRAFT_NOT_FOUND"]
    reasons: list[str] = []
    if not (draft.subject or "").strip():
        reasons.append("DRAFT_SUBJECT_EMPTY")
    if not (draft.body or "").strip():
        reasons.append("DRAFT_BODY_EMPTY")
    resolved_subject = resolve_tokens(draft.subject or "", contact) if contact else draft.subject or ""
    resolved_body = resolve_tokens(draft.body or "", contact) if contact else draft.body or ""
    if UNRESOLVED_TOKEN_RE.search(resolved_subject) or UNRESOLVED_TOKEN_RE.search(resolved_body):
        reasons.append("DRAFT_UNRESOLVED_TEMPLATE_TOKEN")
    return reasons


def draft_campaign_block_reason(db: Session, draft: Draft | None) -> str | None:
    if draft is None:
        return None
    match = CAMPAIGN_NOTE_RE.match((draft.notes or "").strip())
    if not match:
        return None
    campaign = db.get(CampaignPlan, match.group(1))
    if campaign is None:
        return "CAMPAIGN_NOT_FOUND"
    if campaign.status != "active":
        return "CAMPAIGN_NOT_ACTIVE"
    return None


def invalidate_draft_approval(
    db: Session,
    draft: Draft,
    *,
    reason: str = "DRAFT_EDITED_REQUIRES_REAPPROVAL",
) -> list[str]:
    draft.approved = False
    draft.approved_at = None
    queue_rows = (
        db.query(SendQueue)
        .filter(
            SendQueue.draft_id == draft.id,
            SendQueue.status.in_(QUEUE_CANCELLABLE_STATUSES),
        )
        .all()
    )
    for row in queue_rows:
        try:
            reasons = list(json.loads(row.policy_block_reasons or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            reasons = []
        if reason not in reasons:
            reasons.append(reason)
        row.status = "cancelled"
        row.processing_started_at = None
        row.processing_token = None
        row.policy_block_reasons = json.dumps(reasons)
    if queue_rows:
        emit_event(
            db,
            "draft.approval_invalidated",
            entity_type="draft",
            entity_id=draft.id,
            payload={"reason": reason, "cancelled_queue_ids": [row.id for row in queue_rows]},
        )
    return [row.id for row in queue_rows]
