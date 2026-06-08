from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import Contact, Reply
from app.db.session import get_db
from app.conversations.auto_reply_service import AutoReplyService
from app.replies.imap_fetcher import run_imap_fetch_with_lock
from app.replies.service import create_reply_record, refresh_contact_status_after_reply_change, reply_to_dict
from app.audit.service import emit_event
from app.core.time import utcnow

router = APIRouter(prefix="/api/replies", tags=["replies"])


class ReplyCreate(BaseModel):
    contact_id: str
    classified_as: str
    raw_summary: str | None = None
    intent: str | None = None


@router.get("")
def list_replies(
    include_archived: bool = Query(False),
    archived_only: bool = Query(False),
    contact_id: str | None = Query(None),
    classified_as: str | None = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(Reply, Contact.email).join(Contact, Contact.id == Reply.contact_id)
    if archived_only:
        query = query.filter(Reply.archived_at.is_not(None))
    elif not include_archived:
        query = query.filter(Reply.archived_at.is_(None))
    if contact_id:
        query = query.filter(Reply.contact_id == contact_id)
    if classified_as:
        query = query.filter(Reply.classified_as == classified_as)
    rows = query.order_by(Reply.received_at.desc()).all()
    return {"items": [reply_to_dict(row, email) for row, email in rows], "total": len(rows)}


@router.post("")
def create_reply(payload: ReplyCreate, db: Session = Depends(get_db)):
    contact = db.get(Contact, payload.contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="contact not found")
    row, created = create_reply_record(
        db,
        contact,
        payload.classified_as,
        payload.raw_summary,
        intent=payload.intent,
        stop_followups=payload.classified_as in {"reply", "unsubscribe", "bounce"},
    )
    if not created:
        db.rollback()
        raise HTTPException(status_code=409, detail="Already marked")
    try:
        asyncio.run(
            AutoReplyService().process_reply(
                contact.id,
                row.id,
                db,
                allow_autonomous=False,
            )
        )
    except Exception as exc:
        emit_event(db, "auto_reply.failed", entity_type="contact", entity_id=contact.id, payload={"reply_id": row.id, "error_code": exc.__class__.__name__})
    db.commit()
    return reply_to_dict(row, contact.email)


@router.post("/fetch")
def fetch_replies(db: Session = Depends(get_db)):
    return run_imap_fetch_with_lock(db)


@router.post("/{reply_id}/archive")
def archive_reply(reply_id: str, db: Session = Depends(get_db)):
    row = db.get(Reply, reply_id)
    if not row:
        raise HTTPException(status_code=404, detail="reply not found")
    row.archived_at = utcnow()
    contact = db.get(Contact, row.contact_id)
    if contact:
        refresh_contact_status_after_reply_change(db, contact)
    emit_event(db, "reply.archived", entity_type="reply", entity_id=row.id, payload={"classified_as": row.classified_as})
    db.commit()
    return reply_to_dict(row, contact.email if contact else None)


@router.post("/{reply_id}/restore")
def restore_reply(reply_id: str, db: Session = Depends(get_db)):
    row = db.get(Reply, reply_id)
    if not row:
        raise HTTPException(status_code=404, detail="reply not found")
    existing = (
        db.query(Reply)
        .filter(
            Reply.contact_id == row.contact_id,
            Reply.classified_as == row.classified_as,
            Reply.archived_at.is_(None),
            Reply.id != row.id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Active reply already exists")
    row.archived_at = None
    contact = db.get(Contact, row.contact_id)
    if contact:
        refresh_contact_status_after_reply_change(db, contact)
    emit_event(db, "reply.restored", entity_type="reply", entity_id=row.id, payload={"classified_as": row.classified_as})
    db.commit()
    return reply_to_dict(row, contact.email if contact else None)


@router.delete("/{reply_id}")
def delete_reply(reply_id: str, db: Session = Depends(get_db)):
    row = db.get(Reply, reply_id)
    if not row:
        raise HTTPException(status_code=404, detail="reply not found")
    contact = db.get(Contact, row.contact_id)
    payload = {"classified_as": row.classified_as}
    db.delete(row)
    if contact:
        refresh_contact_status_after_reply_change(db, contact)
    emit_event(db, "reply.deleted", entity_type="reply", entity_id=reply_id, payload=payload)
    db.commit()
    return {"deleted": True, "id": reply_id}
