from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent.memory import get_or_create_session
from app.core.time import utcnow
from app.db.models import PendingAgentAction
from app.db.session import get_db
from app.send.governance import governed_action_to_dict

router = APIRouter(prefix="/api/governed-actions", tags=["governed-actions"])


class GovernedActionSession(BaseModel):
    session_token: str


@router.get("/pending")
def pending_actions(session_token: str = Query(min_length=8), db: Session = Depends(get_db)):
    session = get_or_create_session(session_token, db)
    rows = (
        db.query(PendingAgentAction)
        .filter(
            PendingAgentAction.session_id == session.id,
            PendingAgentAction.consumed.is_(False),
            PendingAgentAction.expires_at >= utcnow(),
        )
        .order_by(PendingAgentAction.created_at.asc())
        .all()
    )
    db.commit()
    return {"items": [governed_action_to_dict(row) for row in rows], "total": len(rows)}


@router.post("/{action_id}/cancel")
def cancel_action(action_id: str, payload: GovernedActionSession, db: Session = Depends(get_db)):
    session = get_or_create_session(payload.session_token, db)
    action = db.get(PendingAgentAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="not_found")
    if action.session_id != session.id:
        raise HTTPException(status_code=409, detail="session_mismatch")
    if action.consumed:
        raise HTTPException(status_code=409, detail="consumed")
    action.consumed = True
    action.consumed_at = utcnow()
    if session.pending_action_id == action.id:
        session.pending_action_id = None
    db.commit()
    return {"status": "cancelled", "action_id": action.id}
