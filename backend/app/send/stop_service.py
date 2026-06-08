from __future__ import annotations

import json

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.core.time import utcnow
from app.db.models import Contact, Draft, FollowUpSequence, PendingAgentAction, PendingEmailActionRow, SendQueue

QUEUE_CANCELLABLE_STATUSES = {
    "pending",
    "skipped",
    "processing",
    "simulated",
    "blocked",
    "failed",
    "reconciliation_required",
}
FOLLOWUP_STOPPABLE_STATUSES = {
    "due",
    "skipped",
    "pending_approval",
    "approved",
    "queued",
    "dispatched",
    "simulated",
    "blocked",
    "failed",
    "reconciliation_required",
}


def _append_reason(raw: str | None, reason: str) -> str:
    try:
        reasons = list(json.loads(raw or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        reasons = []
    if reason not in reasons:
        reasons.append(reason)
    return json.dumps(reasons)


def stop_contact_send_work(db: Session, contact_id: str, reason: str) -> dict:
    contact = db.get(Contact, contact_id)
    if contact is not None:
        db.query(Contact).filter(Contact.id == contact_id).update(
            {Contact.send_stop_generation: Contact.send_stop_generation + 1},
            synchronize_session=False,
        )
        db.expire(contact, ["send_stop_generation"])
    queue_rows = (
        db.query(SendQueue)
        .filter(
            SendQueue.contact_id == contact_id,
            SendQueue.status.in_(QUEUE_CANCELLABLE_STATUSES),
        )
        .all()
    )
    followup_rows = (
        db.query(FollowUpSequence)
        .filter(
            FollowUpSequence.contact_id == contact_id,
            FollowUpSequence.status.in_(FOLLOWUP_STOPPABLE_STATUSES),
        )
        .all()
    )
    email_actions = (
        db.query(PendingEmailActionRow)
        .filter(PendingEmailActionRow.contact_id == contact_id, PendingEmailActionRow.consumed.is_(False))
        .all()
    )
    draft_ids = [row[0] for row in db.query(Draft.id).filter(Draft.contact_id == contact_id).all()]
    generic_filter = PendingAgentAction.entity_type == "contact"
    if draft_ids:
        generic_filter = or_(
            generic_filter,
            (PendingAgentAction.entity_type == "draft") & PendingAgentAction.entity_id.in_(draft_ids),
        )
    generic_actions = (
        db.query(PendingAgentAction)
        .filter(
            PendingAgentAction.consumed.is_(False),
            generic_filter,
            or_(
                PendingAgentAction.entity_id == contact_id,
                PendingAgentAction.entity_type == "draft",
            ),
        )
        .all()
    )

    now = utcnow()
    for row in queue_rows:
        row.status = "cancelled"
        row.processing_started_at = None
        row.processing_token = None
        row.policy_block_reasons = _append_reason(row.policy_block_reasons, reason)
    for row in followup_rows:
        row.status = "stopped"
        row.stop_reason = reason
    for action in [*email_actions, *generic_actions]:
        action.consumed = True
        action.consumed_at = now

    affected = {
        "reason": reason,
        "queue_ids": [row.id for row in queue_rows],
        "followup_ids": [row.id for row in followup_rows],
        "pending_email_action_ids": [row.id for row in email_actions],
        "pending_agent_action_ids": [row.id for row in generic_actions],
    }
    if any(affected[key] for key in affected if key.endswith("_ids")):
        emit_event(
            db,
            "send_work.cancelled",
            entity_type="contact",
            entity_id=contact_id,
            payload=affected,
        )
    return affected
