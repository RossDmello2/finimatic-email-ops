from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import Workbook
from app.db.session import get_db
from app.workflows.service import ensure_default_workbook, run_to_dict, run_workflow, workbook_payload

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


class WorkflowRunRequest(BaseModel):
    cost_cap_units: int | None = None


@router.get("")
def list_workflows(db: Session = Depends(get_db)):
    rows = db.query(Workbook).order_by(Workbook.created_at.asc()).all()
    return {"items": [{"id": row.id, "name": row.name, "description": row.description, "status": row.status} for row in rows], "total": len(rows)}


@router.get("/{workbook_id}")
def get_workflow(workbook_id: str, db: Session = Depends(get_db)):
    workbook = db.get(Workbook, workbook_id)
    if not workbook:
        raise HTTPException(status_code=404, detail="workflow not found")
    return workbook_payload(db, workbook)


@router.post("/{workbook_id}/run")
def run(workbook_id: str, payload: WorkflowRunRequest | None = None, db: Session = Depends(get_db)):
    workbook = db.get(Workbook, workbook_id)
    if not workbook:
        raise HTTPException(status_code=404, detail="workflow not found")
    row = run_workflow(db, workbook, cost_cap_units=payload.cost_cap_units if payload else None)
    db.commit()
    return run_to_dict(row, db)


@router.post("/{workbook_id}/retry-failed")
def retry_failed(workbook_id: str, payload: WorkflowRunRequest | None = None, db: Session = Depends(get_db)):
    workbook = db.get(Workbook, workbook_id)
    if not workbook:
        raise HTTPException(status_code=404, detail="workflow not found")
    row = run_workflow(db, workbook, retry_failed_only=True, cost_cap_units=payload.cost_cap_units if payload else None)
    db.commit()
    return run_to_dict(row, db)
