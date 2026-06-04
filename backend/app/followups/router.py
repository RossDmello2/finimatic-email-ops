from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.time import parse_datetime
from app.db.models import FollowUpSequence, SendQueue
from app.db.session import get_db
from app.followups.service import approve_followup_draft, followup_to_dict, process_due_followups
from app.send.queue_worker import process_pending_queue, queue_to_dict
from app.settings.service import get_bool

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
async def approve_draft(sequence_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if get_bool(db, "dry_run"):
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "DRY_RUN_ENABLED",
                "message": "Live mode is required before approval can send email. Turn off Dry Run, then approve again.",
            },
        )
    try:
        result = approve_followup_draft(db, sequence_id, immediate=True)
        process_result = await process_pending_queue(db, queue_ids=[result["queue_id"]])
        db.expire_all()
        queue = db.get(SendQueue, result["queue_id"])
        delivery_status = _delivery_status(process_result, queue)
        return {**result, "delivery_status": delivery_status, "delivery_result": process_result, "queue": queue_to_dict(queue, db) if queue else None}
    except ValueError as exc:
        code = str(exc)
        status_code = 404 if code in {"followup_not_found", "draft_not_found"} else 409
        raise HTTPException(status_code=status_code, detail=code)


@router.post("/process")
def process_followups(db: Session = Depends(get_db)):
    return process_due_followups(db)


def _delivery_status(result: dict, queue: SendQueue | None) -> str:
    if result.get("sent"):
        return "sent"
    if result.get("deferred"):
        return "deferred"
    if result.get("blocked"):
        return "blocked"
    if result.get("skipped"):
        return "dry_run_blocked"
    if queue is None:
        return "queued"
    if queue.status == "sent":
        return "sent"
    if queue.status == "pending":
        return "deferred"
    if queue.status == "blocked":
        return "blocked"
    if queue.status == "skipped":
        return "dry_run_blocked"
    if queue.status == "failed":
        return "failed"
    return queue.status or "queued"
