from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.db.models import Contact, Draft, SendQueue
from app.db.session import get_db
from app.drafts.service import draft_content_block_reasons
from app.send.auto_process import schedule_auto_process
from app.send.policy import prequeue_block_reasons
from app.send.queue_worker import (
    cancel_queue_entry,
    clear_queue_entries,
    create_queue_entry,
    process_pending_queue,
    queue_list_to_dict,
    queue_to_dict,
    reconcile_queue_entry,
    retry_queue_entry,
    send_queue_entry_now,
)
from app.send.sequence import sequence_prerequisite_met

router = APIRouter(prefix="/api/queue", tags=["queue"])


class QueueCreate(BaseModel):
    contact_id: str
    draft_id: str
    sequence_num: int = Field(default=1, ge=1)


class QueueReconcile(BaseModel):
    action: str


@router.get("")
def list_queue(db: Session = Depends(get_db)):
    items = (
        db.query(SendQueue)
        .options(joinedload(SendQueue.contact), joinedload(SendQueue.draft))
        .order_by(SendQueue.created_at.asc())
        .all()
    )
    return {"items": queue_list_to_dict(items, db), "total": len(items)}


@router.delete("")
def clear_queue(db: Session = Depends(get_db)):
    return clear_queue_entries(db)


@router.post("")
def create_queue(payload: QueueCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    contact = db.get(Contact, payload.contact_id)
    if not contact or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="contact_not_found")
    draft = db.get(Draft, payload.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="draft_not_found")
    if draft.contact_id != contact.id:
        raise HTTPException(status_code=409, detail="draft_contact_mismatch")
    content_blocks = draft_content_block_reasons(draft, contact)
    if content_blocks:
        raise HTTPException(status_code=422, detail={"blocked": content_blocks})
    blocked = prequeue_block_reasons(contact, db)
    if blocked:
        raise HTTPException(status_code=422, detail={"blocked": blocked})
    if not sequence_prerequisite_met(db, contact.id, payload.sequence_num):
        raise HTTPException(
            status_code=409,
            detail={"reason": "prior_sequence_not_provider_accepted", "sequence_num": payload.sequence_num},
        )
    entry = create_queue_entry(db, payload.contact_id, payload.draft_id, payload.sequence_num)
    db.commit()
    schedule_auto_process(background_tasks)
    return queue_to_dict(entry, db)


@router.get("/{queue_id}")
def get_queue(queue_id: str, db: Session = Depends(get_db)):
    entry = db.get(SendQueue, queue_id)
    if not entry:
        raise HTTPException(status_code=404, detail="queue entry not found")
    return queue_to_dict(entry, db)


@router.post("/process")
async def process_queue(db: Session = Depends(get_db)):
    return await process_pending_queue(db)


@router.post("/{queue_id}/send-now")
async def send_now(queue_id: str, db: Session = Depends(get_db)):
    try:
        return await send_queue_entry_now(db, queue_id)
    except ValueError as exc:
        code = str(exc)
        status_code = 404 if code == "queue_not_found" else 409
        raise HTTPException(status_code=status_code, detail=code) from exc


@router.post("/{queue_id}/reconcile")
def reconcile_queue(queue_id: str, payload: QueueReconcile, db: Session = Depends(get_db)):
    try:
        entry = reconcile_queue_entry(db, queue_id, payload.action)
    except ValueError as exc:
        code = str(exc)
        status_code = 404 if code == "queue_not_found" else 409
        raise HTTPException(status_code=status_code, detail=code) from exc
    return queue_to_dict(entry, db)


@router.post("/{queue_id}/retry")
def retry_queue(queue_id: str, db: Session = Depends(get_db)):
    try:
        entry = retry_queue_entry(db, queue_id)
    except ValueError as exc:
        code = str(exc)
        status_code = 404 if code == "queue_not_found" else 409
        raise HTTPException(status_code=status_code, detail=code) from exc
    return queue_to_dict(entry, db)


@router.post("/{queue_id}/cancel")
def cancel_queue(queue_id: str, db: Session = Depends(get_db)):
    try:
        entry = cancel_queue_entry(db, queue_id)
    except ValueError as exc:
        code = str(exc)
        status_code = 404 if code == "queue_not_found" else 409
        raise HTTPException(status_code=status_code, detail=code) from exc
    return queue_to_dict(entry, db)


@router.delete("/{queue_id}")
def delete_queue(queue_id: str, db: Session = Depends(get_db)):
    try:
        entry = cancel_queue_entry(db, queue_id)
    except ValueError as exc:
        code = str(exc)
        status_code = 404 if code == "queue_not_found" else 409
        raise HTTPException(status_code=status_code, detail=code) from exc
    return queue_to_dict(entry, db)
