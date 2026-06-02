from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import SendQueue
from app.db.session import get_db
from app.send.auto_process import schedule_auto_process
from app.send.queue_worker import create_queue_entry, process_pending_queue, queue_to_dict

router = APIRouter(prefix="/api/queue", tags=["queue"])


class QueueCreate(BaseModel):
    contact_id: str
    draft_id: str
    sequence_num: int = 1


@router.get("")
def list_queue(db: Session = Depends(get_db)):
    items = db.query(SendQueue).order_by(SendQueue.created_at.asc()).all()
    return {"items": [queue_to_dict(item) for item in items], "total": len(items)}


@router.post("")
def create_queue(payload: QueueCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    entry = create_queue_entry(db, payload.contact_id, payload.draft_id, payload.sequence_num)
    db.commit()
    schedule_auto_process(background_tasks)
    return queue_to_dict(entry)


@router.get("/{queue_id}")
def get_queue(queue_id: str, db: Session = Depends(get_db)):
    entry = db.get(SendQueue, queue_id)
    if not entry:
        raise HTTPException(status_code=404, detail="queue entry not found")
    return queue_to_dict(entry)


@router.post("/process")
async def process_queue(db: Session = Depends(get_db)):
    return await process_pending_queue(db)
