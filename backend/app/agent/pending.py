from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.agent.catalog import get_capability
from app.agent.memory import hash_session_token
from app.audit.service import redact_payload
from app.core.idempotency import sha256_key
from app.core.time import utcnow
from app.db.models import AgentSession, Contact, Draft, PendingAgentAction, PendingEmailActionRow

CONFIRMATION_TTL_SECONDS = 180
PendingStatus = Literal["valid", "not_found", "expired", "consumed", "session_mismatch", "draft_mismatch", "hash_mismatch"]
GenericPendingStatus = Literal[
    "valid",
    "not_found",
    "expired",
    "consumed",
    "session_mismatch",
    "target_mismatch",
    "hash_mismatch",
    "policy_now_blocked",
    "capability_not_allowed",
]


def params_hash(draft_id: str, contact_id: str, subject: str, body: str, stop_generation: int = 0) -> str:
    return sha256_key(draft_id, contact_id, subject, body, stop_generation)


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _session_id_from_token_or_id(value: str, db: Session) -> str | None:
    if db.get(AgentSession, value):
        return value
    row = db.query(AgentSession).filter_by(session_token_hash=hash_session_token(value)).first()
    return row.id if row else None


def create_pending_action(session_id: str, draft_id: str, contact_id: str, subject: str, body: str, db: Session) -> PendingEmailActionRow:
    now = utcnow()
    session = db.get(AgentSession, session_id)
    if session:
        cancel_pending_action(session_id, db)
        cancel_generic_pending_actions(session_id, db)
    contact = db.get(Contact, contact_id)
    to_email = contact.email if contact else "recipient"
    action = PendingEmailActionRow(
        session_id=session_id,
        draft_id=draft_id,
        contact_id=contact_id,
        params_hash=params_hash(
            draft_id,
            contact_id,
            subject,
            body,
            contact.send_stop_generation if contact else 0,
        ),
        source_label="Email Provider",
        confirmation_prompt=f"Send this draft to {to_email} with subject \"{subject}\"?",
        expires_at=now + timedelta(seconds=CONFIRMATION_TTL_SECONDS),
    )
    db.add(action)
    db.flush()
    if session:
        session.pending_action_id = action.id
    return action


def validate_pending_action(action_id: str, session_token_or_id: str, draft_id: str, db: Session) -> PendingStatus:
    action = db.get(PendingEmailActionRow, action_id)
    if action is None:
        return "not_found"
    if action.consumed:
        return "consumed"
    if _as_aware(action.expires_at) < utcnow():
        return "expired"
    session_id = _session_id_from_token_or_id(session_token_or_id, db)
    if not session_id or action.session_id != session_id:
        return "session_mismatch"
    if action.draft_id != draft_id:
        return "draft_mismatch"
    draft = db.get(Draft, draft_id)
    if not draft or draft.contact_id != action.contact_id:
        return "draft_mismatch"
    contact = db.get(Contact, draft.contact_id)
    current_hash = params_hash(
        draft.id,
        draft.contact_id,
        draft.subject,
        draft.body,
        contact.send_stop_generation if contact else 0,
    )
    if action.params_hash != current_hash:
        return "hash_mismatch"
    return "valid"


def consume_pending_action(action_id: str, db: Session) -> None:
    action = db.get(PendingEmailActionRow, action_id)
    if action is None:
        return
    action.consumed = True
    action.consumed_at = utcnow()
    session = db.get(AgentSession, action.session_id)
    if session and session.pending_action_id == action.id:
        session.pending_action_id = None
    db.flush()


def claim_pending_action(action_id: str, session_token_or_id: str, draft_id: str, db: Session) -> PendingStatus:
    status = validate_pending_action(action_id, session_token_or_id, draft_id, db)
    if status != "valid":
        return status
    now = utcnow()
    claimed = (
        db.query(PendingEmailActionRow)
        .filter(
            PendingEmailActionRow.id == action_id,
            PendingEmailActionRow.consumed.is_(False),
            PendingEmailActionRow.expires_at >= now,
        )
        .update({"consumed": True, "consumed_at": now}, synchronize_session=False)
    )
    db.flush()
    if claimed != 1:
        return validate_pending_action(action_id, session_token_or_id, draft_id, db)
    action = db.get(PendingEmailActionRow, action_id)
    session = db.get(AgentSession, action.session_id) if action else None
    if session and session.pending_action_id == action_id:
        session.pending_action_id = None
    return "valid"


def cancel_pending_action(session_id: str, db: Session) -> None:
    session = db.get(AgentSession, session_id)
    for action in db.query(PendingEmailActionRow).filter_by(session_id=session_id, consumed=False).all():
        action.consumed = True
        action.consumed_at = utcnow()
    if session:
        session.pending_action_id = None
    db.flush()


def generic_params_hash(capability: str, entity_type: str, entity_id: str | None, params: dict[str, Any]) -> str:
    return sha256_key(capability, entity_type, entity_id or "", json.dumps(params, sort_keys=True, default=str))


def _capability_allows_generic_pending(capability: str) -> bool:
    spec = get_capability(capability) or {}
    return bool(spec.get("side_effect") and spec.get("confirmation_required"))


def create_generic_pending_action(
    session_id: str,
    *,
    capability: str,
    entity_type: str,
    entity_id: str | None,
    params: dict[str, Any],
    source_label: str,
    goal: str,
    evidence_summary: str,
    policy_result: str,
    proposed_side_effect: str,
    confirmation_prompt: str,
    db: Session,
) -> PendingAgentAction:
    if not _capability_allows_generic_pending(capability):
        raise ValueError("capability_not_allowed")
    now = utcnow()
    session = db.get(AgentSession, session_id)
    if session:
        cancel_pending_action(session_id, db)
        cancel_generic_pending_actions(session_id, db)
    snapshot = redact_payload(
        {
            "goal": goal,
            "capability": capability,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "params": params,
            "evidence_summary": evidence_summary,
            "policy_result": policy_result,
            "proposed_side_effect": proposed_side_effect,
        }
    )
    action = PendingAgentAction(
        session_id=session_id,
        action_type="side_effect",
        capability=capability,
        entity_type=entity_type,
        entity_id=entity_id,
        params_hash=generic_params_hash(capability, entity_type, entity_id, params),
        source_label=source_label,
        confirmation_prompt=confirmation_prompt,
        action_snapshot_redacted=json.dumps(snapshot, sort_keys=True, default=str),
        policy_snapshot_redacted=json.dumps(redact_payload({"allowed": True, "result": policy_result}), sort_keys=True, default=str),
        expires_at=now + timedelta(seconds=CONFIRMATION_TTL_SECONDS),
    )
    db.add(action)
    db.flush()
    if session:
        session.pending_action_id = action.id
    return action


def validate_generic_pending_action(
    action_id: str,
    session_token_or_id: str,
    *,
    capability: str,
    entity_type: str,
    entity_id: str | None,
    current_params: dict[str, Any],
    policy_allowed: bool,
    db: Session,
) -> GenericPendingStatus:
    action = db.get(PendingAgentAction, action_id)
    if action is None:
        return "not_found"
    if not _capability_allows_generic_pending(action.capability) or action.capability != capability:
        return "capability_not_allowed"
    if action.consumed:
        return "consumed"
    if _as_aware(action.expires_at) < utcnow():
        return "expired"
    session_id = _session_id_from_token_or_id(session_token_or_id, db)
    if not session_id or action.session_id != session_id:
        return "session_mismatch"
    if action.entity_type != entity_type or (action.entity_id or "") != (entity_id or ""):
        return "target_mismatch"
    current_hash = generic_params_hash(action.capability, action.entity_type, action.entity_id, current_params)
    if action.params_hash != current_hash:
        return "hash_mismatch"
    if not policy_allowed:
        return "policy_now_blocked"
    return "valid"


def claim_generic_pending_action(
    action_id: str,
    session_token_or_id: str,
    *,
    capability: str,
    entity_type: str,
    entity_id: str | None,
    current_params: dict[str, Any],
    policy_allowed: bool,
    db: Session,
) -> GenericPendingStatus:
    status = validate_generic_pending_action(
        action_id,
        session_token_or_id,
        capability=capability,
        entity_type=entity_type,
        entity_id=entity_id,
        current_params=current_params,
        policy_allowed=policy_allowed,
        db=db,
    )
    if status != "valid":
        return status
    now = utcnow()
    claimed = (
        db.query(PendingAgentAction)
        .filter(
            PendingAgentAction.id == action_id,
            PendingAgentAction.consumed.is_(False),
            PendingAgentAction.expires_at >= now,
        )
        .update({"consumed": True, "consumed_at": now}, synchronize_session=False)
    )
    db.flush()
    if claimed != 1:
        return validate_generic_pending_action(
            action_id,
            session_token_or_id,
            capability=capability,
            entity_type=entity_type,
            entity_id=entity_id,
            current_params=current_params,
            policy_allowed=policy_allowed,
            db=db,
        )
    action = db.get(PendingAgentAction, action_id)
    session = db.get(AgentSession, action.session_id) if action and action.session_id else None
    if session and session.pending_action_id == action_id:
        session.pending_action_id = None
    return "valid"


def cancel_generic_pending_actions(session_id: str, db: Session) -> None:
    session = db.get(AgentSession, session_id)
    for action in db.query(PendingAgentAction).filter_by(session_id=session_id, consumed=False).all():
        action.consumed = True
        action.consumed_at = utcnow()
    if session and session.pending_action_id:
        generic = db.get(PendingAgentAction, session.pending_action_id)
        if generic:
            session.pending_action_id = None
    db.flush()
