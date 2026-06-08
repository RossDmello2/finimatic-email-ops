from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deliverability.service import create_inbox_placement_test, deliverability_summary, inbox_test_to_dict, policy_trace, run_deliverability_check

router = APIRouter(prefix="/api/deliverability", tags=["deliverability"])


class InboxPlacementRequest(BaseModel):
    seed_email: str = Field(min_length=3)
    subject: str = "Finimatic inbox placement seed"


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    return deliverability_summary(db)


@router.get("/policy-trace")
def trace(db: Session = Depends(get_db)):
    return policy_trace(db)


@router.post("/check")
def check(db: Session = Depends(get_db)):
    result = run_deliverability_check(db)
    db.commit()
    return result


@router.post("/inbox-placement-test")
def inbox_placement_test(payload: InboxPlacementRequest, db: Session = Depends(get_db)):
    row = create_inbox_placement_test(db, payload.seed_email, payload.subject)
    db.commit()
    return inbox_test_to_dict(row)

