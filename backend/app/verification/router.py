from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import Contact
from app.db.session import get_db
from app.verification.service import (
    attempts_for_verification,
    get_verification_for_contact,
    list_verification_rows,
    run_local_verification,
    verification_policy_for_contact,
    verification_summary,
    verification_to_dict,
)

router = APIRouter(prefix="/api/verification", tags=["verification"])


class RunSelectedRequest(BaseModel):
    contact_ids: list[str]


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    return verification_summary(db)


@router.get("")
def list_verifications(db: Session = Depends(get_db)):
    rows = list_verification_rows(db)
    return {"items": rows, "total": len(rows), "summary": verification_summary(db)}


@router.get("/contact/{contact_id}")
def contact_verification(contact_id: str, db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="contact not found")
    row = get_verification_for_contact(db, contact)
    return {
        "verification": verification_to_dict(row),
        "attempts": attempts_for_verification(db, row.id if row else None),
        "policy": verification_policy_for_contact(db, contact),
    }


@router.post("/contact/{contact_id}/run")
def run_contact_verification(contact_id: str, db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="contact not found")
    row = run_local_verification(db, contact)
    db.commit()
    return {
        "verification": verification_to_dict(row),
        "attempts": attempts_for_verification(db, row.id),
        "policy": verification_policy_for_contact(db, contact),
    }


@router.post("/run-selected")
def run_selected(payload: RunSelectedRequest, db: Session = Depends(get_db)):
    results = []
    for contact_id in payload.contact_ids:
        contact = db.get(Contact, contact_id)
        if not contact or contact.deleted_at is not None:
            continue
        row = run_local_verification(db, contact)
        results.append(verification_to_dict(row))
    db.commit()
    return {"items": results, "total": len(results), "summary": verification_summary(db)}

