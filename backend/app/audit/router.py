from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.audit.service import audit_to_dict
from app.db.models import AuditEvent
from app.db.session import get_db

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def list_audit(db: Session = Depends(get_db)):
    items = db.query(AuditEvent).order_by(AuditEvent.created_at.asc()).all()
    return {"items": [audit_to_dict(item, db) for item in items], "total": len(items)}
