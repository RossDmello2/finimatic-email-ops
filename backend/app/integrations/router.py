from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import Contact
from app.db.session import get_db
from app.integrations.service import (
    cancel_contact_sync_preview,
    confirm_contact_sync_preview,
    create_contact_sync_preview,
    integration_summary,
    journal_to_dict,
    preview_to_dict,
)

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class PreviewSyncRequest(BaseModel):
    contact_id: str


class ConfirmSyncRequest(BaseModel):
    preview_id: str


class CancelSyncRequest(BaseModel):
    preview_id: str


@router.get("")
def list_integrations(db: Session = Depends(get_db)):
    return integration_summary(db, ensure_defaults=False)


@router.post("/{provider}/preview-sync")
def preview_sync(provider: str, payload: PreviewSyncRequest, db: Session = Depends(get_db)):
    contact = db.get(Contact, payload.contact_id)
    if not contact or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="contact not found")
    try:
        row = create_contact_sync_preview(db, provider, contact)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return preview_to_dict(row)


@router.post("/{provider}/confirm-sync")
def confirm_sync(provider: str, payload: ConfirmSyncRequest, db: Session = Depends(get_db)):
    try:
        row = confirm_contact_sync_preview(db, provider, payload.preview_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return journal_to_dict(row)


@router.post("/{provider}/cancel-sync")
def cancel_sync(provider: str, payload: CancelSyncRequest, db: Session = Depends(get_db)):
    try:
        row = cancel_contact_sync_preview(db, provider, payload.preview_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return preview_to_dict(row)
