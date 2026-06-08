from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent.pending import claim_generic_pending_action
from app.audit.service import audit_to_dict, emit_event
from app.core.time import utcnow
from app.conversations.auto_reply_service import AutoReplyService
from app.db.models import AuditEvent, Contact, Draft, PendingAgentAction
from app.db.session import get_db
from app.drafts.router import draft_to_dict
from app.security.authorization import require_admin_access
from app.send.governance import governed_action_to_dict, prepare_governed_action
from app.settings.service import get_int, get_value, set_value

router = APIRouter(prefix="/api/auto-reply", tags=["auto-reply"])

AUTO_REPLY_EVENTS = {
    "auto_reply.sent",
    "auto_reply.proposed",
    "auto_reply.failed",
    "auto_reply.approved_and_sent",
    "auto_reply.rejected",
    "auto_reply.skipped",
    "auto_reply.quality_failed",
    "auto_reply.killed",
}


class AutoReplyPrepare(BaseModel):
    session_token: str


class AutoReplyConfirm(AutoReplyPrepare):
    action_id: str


@router.post("/approve/{draft_id}", status_code=202)
def approve_auto_reply(draft_id: str, payload: AutoReplyPrepare, db: Session = Depends(get_db)):
    draft = db.get(Draft, draft_id)
    if not draft or draft.source != "auto_reply_proposed" or draft.approved or draft.rejected:
        raise HTTPException(status_code=409, detail="draft_not_pending")
    contact = db.get(Contact, draft.contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="contact_not_found")
    params = {
        "draft_id": draft.id,
        "contact_id": contact.id,
        "to": contact.email,
        "subject": draft.subject,
        "body": draft.body,
        "stop_generation": contact.send_stop_generation,
    }
    action = prepare_governed_action(
        db,
        session_token=payload.session_token,
        capability="auto_reply_approve",
        entity_type="draft",
        entity_id=draft.id,
        params=params,
        source_label="Auto-Reply",
        goal="Approve and send one reviewed Auto-Reply draft",
        evidence_summary="Draft, recipient, and current policy were reviewed.",
        policy_result="pending final policy recheck",
        proposed_side_effect=f"Send one Auto-Reply to {contact.email}.",
        confirmation_prompt=f'Approve and send this reply to {contact.email} with subject "{draft.subject}"?',
    )
    db.commit()
    return {"status": "pending_confirmation", "pending_action": governed_action_to_dict(action)}


@router.post("/confirm/{draft_id}")
async def confirm_auto_reply(draft_id: str, payload: AutoReplyConfirm, db: Session = Depends(get_db)):
    draft = db.get(Draft, draft_id)
    if not draft or draft.source != "auto_reply_proposed" or draft.approved or draft.rejected:
        raise HTTPException(status_code=409, detail="draft_not_pending")
    contact = db.get(Contact, draft.contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="contact_not_found")
    params = {
        "draft_id": draft.id,
        "contact_id": contact.id,
        "to": contact.email,
        "subject": draft.subject,
        "body": draft.body,
        "stop_generation": contact.send_stop_generation,
    }
    block_reasons = AutoReplyService()._send_block_reasons(db, contact, allow_proposed=True)
    policy_blocking = [reason for reason in block_reasons if reason != "DRY_RUN_ENABLED"]
    status = claim_generic_pending_action(
        payload.action_id,
        payload.session_token,
        capability="auto_reply_approve",
        entity_type="draft",
        entity_id=draft.id,
        current_params=params,
        policy_allowed=not policy_blocking,
        db=db,
    )
    if status != "valid":
        db.commit()
        raise HTTPException(status_code=409, detail=status)
    db.commit()
    try:
        result = await AutoReplyService().approve_pending_draft(draft_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return {"status": result.action, "message_id": result.message_id, "reason": result.reason}


@router.post("/autonomous/prepare", status_code=202)
def prepare_autonomous(
    payload: AutoReplyPrepare,
    db: Session = Depends(get_db),
    _principal=Depends(require_admin_access),
):
    mailbox = get_value(db, "gmail_user")
    params = {
        "mailbox": mailbox,
        "daily_cap": get_int(db, "auto_reply_daily_cap"),
        "minimum_gap_minutes": get_int(db, "auto_reply_min_gap_minutes"),
        "safe_intents": get_value(db, "auto_reply_safe_intents"),
        "stop_conditions": "reply stop, suppression, bounce, unsubscribe, kill switch, policy block",
    }
    action = prepare_governed_action(
        db,
        session_token=payload.session_token,
        capability="auto_reply_enable_autonomous",
        entity_type="settings",
        entity_id="auto_reply",
        params=params,
        source_label="Auto-Reply",
        goal="Enable autonomous Auto-Reply",
        evidence_summary="Mailbox, caps, allowed intents, minimum gap, and stop conditions were reviewed.",
        policy_result="activation requires this confirmation",
        proposed_side_effect="Allow governed replies to send automatically after all policy gates pass.",
        confirmation_prompt=f"Enable autonomous Auto-Reply for {mailbox or 'the configured mailbox'}?",
    )
    db.commit()
    return {"status": "pending_confirmation", "pending_action": governed_action_to_dict(action)}


@router.post("/autonomous/confirm")
def confirm_autonomous(
    payload: AutoReplyConfirm,
    db: Session = Depends(get_db),
    _principal=Depends(require_admin_access),
):
    params = {
        "mailbox": get_value(db, "gmail_user"),
        "daily_cap": get_int(db, "auto_reply_daily_cap"),
        "minimum_gap_minutes": get_int(db, "auto_reply_min_gap_minutes"),
        "safe_intents": get_value(db, "auto_reply_safe_intents"),
        "stop_conditions": "reply stop, suppression, bounce, unsubscribe, kill switch, policy block",
    }
    status = claim_generic_pending_action(
        payload.action_id,
        payload.session_token,
        capability="auto_reply_enable_autonomous",
        entity_type="settings",
        entity_id="auto_reply",
        current_params=params,
        policy_allowed=bool(params["mailbox"]),
        db=db,
    )
    if status != "valid":
        db.commit()
        raise HTTPException(status_code=409, detail=status)
    set_value(db, "auto_reply_autonomous_authorized", "true")
    set_value(db, "auto_reply_kill_switch", "false")
    set_value(db, "auto_reply_enabled", "true")
    set_value(db, "auto_reply_mode", "autonomous")
    db.commit()
    return {"status": "autonomous_enabled"}


@router.post("/kill")
def kill_autonomous(db: Session = Depends(get_db)):
    set_value(db, "auto_reply_enabled", "false")
    set_value(db, "auto_reply_autonomous_authorized", "false")
    set_value(db, "auto_reply_kill_switch", "true")
    set_value(db, "auto_reply_kill_generation", str(get_int(db, "auto_reply_kill_generation") + 1))
    pending = (
        db.query(PendingAgentAction)
        .filter(
            PendingAgentAction.capability.in_(
                {"auto_reply_approve", "auto_reply_enable_autonomous"}
            ),
            PendingAgentAction.consumed.is_(False),
        )
        .all()
    )
    now = utcnow()
    for action in pending:
        action.consumed = True
        action.consumed_at = now
    emit_event(
        db,
        "auto_reply.killed",
        entity_type="settings",
        entity_id="auto_reply",
        payload={"invalidated_pending_action_ids": [action.id for action in pending]},
    )
    db.commit()
    return {"status": "killed", "invalidated_pending_actions": len(pending)}


@router.post("/reject/{draft_id}")
def reject_auto_reply(draft_id: str, db: Session = Depends(get_db)):
    try:
        draft = AutoReplyService().reject_pending_draft(draft_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return {"status": "rejected", "draft": draft_to_dict(draft)}


@router.get("/pending")
def pending_auto_replies(db: Session = Depends(get_db)):
    items = AutoReplyService().pending_drafts(db)
    return {"items": items, "total": len(items)}


@router.get("/log")
def auto_reply_log(db: Session = Depends(get_db)):
    rows = (
        db.query(AuditEvent)
        .filter(AuditEvent.event_type.in_(AUTO_REPLY_EVENTS))
        .order_by(AuditEvent.created_at.desc())
        .limit(100)
        .all()
    )
    return {"items": [audit_to_dict(row) for row in rows], "total": len(rows)}
