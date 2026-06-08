from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.db.models import Contact, Suppression
from app.db.session import get_db
from app.replies.service import refresh_contact_status_after_reply_change
from app.send.stop_service import stop_contact_send_work

router = APIRouter(prefix="/api/suppressions", tags=["suppressions"])


class SuppressionCreate(BaseModel):
    email: str
    reason: str = "manual"
    source: str | None = None


def suppression_to_dict(row: Suppression) -> dict:
    return {
        "id": row.id,
        "email": row.email,
        "reason": row.reason,
        "source": row.source,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("")
def list_suppressions(db: Session = Depends(get_db)):
    rows = db.query(Suppression).order_by(Suppression.created_at.asc()).all()
    return {"items": [suppression_to_dict(row) for row in rows], "total": len(rows)}


@router.post("")
def create_suppression(payload: SuppressionCreate, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    row = db.query(Suppression).filter_by(email=email).first()
    if row is None:
        row = Suppression(email=email, reason=payload.reason, source=payload.source)
        db.add(row)
        db.flush()
        emit_event(db, "suppression.added", entity_type="suppression", entity_id=row.id, payload={"email": email, "reason": payload.reason})
    contact = db.query(Contact).filter(Contact.email == email, Contact.deleted_at.is_(None)).first()
    if contact:
        contact.status = "suppressed"
        stop_contact_send_work(db, contact.id, "RECIPIENT_SUPPRESSED")
    db.commit()
    return suppression_to_dict(row)


@router.delete("/{suppression_id}")
def delete_suppression(suppression_id: str, db: Session = Depends(get_db)):
    row = db.get(Suppression, suppression_id)
    if row is None:
        raise HTTPException(status_code=404, detail="suppression not found")
    payload = {"email": row.email, "reason": row.reason}
    contact = db.query(Contact).filter(Contact.email == row.email, Contact.deleted_at.is_(None)).first()
    db.delete(row)
    db.flush()
    if contact:
        refresh_contact_status_after_reply_change(db, contact)
    emit_event(db, "suppression.removed", entity_type="suppression", entity_id=suppression_id, payload=payload)
    db.commit()
    return {"deleted": True, "id": suppression_id}
