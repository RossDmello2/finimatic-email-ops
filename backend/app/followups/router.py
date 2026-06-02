from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.time import parse_datetime
from app.db.models import FollowUpSequence
from app.db.session import get_db
from app.followups.service import approve_followup_draft, followup_to_dict, process_due_followups
from app.send.auto_process import schedule_auto_process

router = APIRouter(prefix="/api/followups", tags=["followups"])


class FollowUpPatch(BaseModel):
    due_at: str | None = None
    status: str | None = None
    stop_reason: str | None = None


@router.get("")
def list_followups(db: Session = Depends(get_db)):
    rows = db.query(FollowUpSequence).order_by(FollowUpSequence.created_at.asc()).all()
    return {"items": [followup_to_dict(row, db) for row in rows], "total": len(rows)}


@router.get("/{sequence_id}")
def get_followup(sequence_id: str, db: Session = Depends(get_db)):
    row = db.get(FollowUpSequence, sequence_id)
    if not row:
        raise HTTPException(status_code=404, detail="followup not found")
    return followup_to_dict(row, db)


@router.patch("/{sequence_id}")
def patch_followup(sequence_id: str, payload: FollowUpPatch, db: Session = Depends(get_db)):
    row = db.get(FollowUpSequence, sequence_id)
    if not row:
        raise HTTPException(status_code=404, detail="followup not found")
    if payload.due_at:
        row.due_at = parse_datetime(payload.due_at)
    if payload.status:
        row.status = payload.status
    if payload.stop_reason:
        row.stop_reason = payload.stop_reason
    db.commit()
    return followup_to_dict(row, db)


@router.post("/{sequence_id}/approve-draft")
def approve_draft(sequence_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        result = approve_followup_draft(db, sequence_id)
        schedule_auto_process(background_tasks)
        return result
    except ValueError as exc:
        code = str(exc)
        status_code = 404 if code in {"followup_not_found", "draft_not_found"} else 409
        raise HTTPException(status_code=status_code, detail=code)


@router.post("/process")
def process_followups(db: Session = Depends(get_db)):
    return process_due_followups(db)
