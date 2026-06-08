from __future__ import annotations

import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.contacts.utils import custom_fields_with_tags
from app.core.time import iso, utcnow
from app.db.models import Contact
from app.db.session import get_db
from app.send.stop_service import stop_contact_send_work

router = APIRouter(prefix="/api/contacts", tags=["contacts"])

STOP_REASON_BY_STATUS = {
    "replied": "RECIPIENT_REPLIED",
    "unsubscribed": "RECIPIENT_UNSUBSCRIBED",
    "bounced": "RECIPIENT_BOUNCED",
    "suppressed": "RECIPIENT_SUPPRESSED",
    "manually_paused": "RECIPIENT_MANUALLY_PAUSED",
}


class ContactCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    email: str
    creator_name: str | None = None
    business_name: str | None = None
    website_url: str | None = None
    source: str = "manual"
    provenance: str | None = None
    notes: str | None = None
    personalization: str | None = None
    lead_category: str | None = None
    tags: str | list[str] | None = None


class ContactPatch(BaseModel):
    status: str | None = None
    notes: str | None = None
    personalization: str | None = None
    auto_reply_override: str | None = None


def contact_to_dict(contact: Contact) -> dict:
    return {
        "id": contact.id,
        "email": contact.email,
        "creator_name": contact.creator_name,
        "business_name": contact.business_name,
        "website_url": contact.website_url,
        "source": contact.source,
        "provenance": contact.provenance,
        "notes": contact.notes,
        "personalization": contact.personalization,
        "lead_category": contact.lead_category,
        "custom_fields": json.loads(contact.custom_fields or "{}"),
        "auto_reply_override": contact.auto_reply_override,
        "status": contact.status,
        "deleted_at": iso(contact.deleted_at),
        "created_at": iso(contact.created_at),
        "updated_at": iso(contact.updated_at),
    }


@router.get("")
def list_contacts(db: Session = Depends(get_db)):
    items = db.query(Contact).filter(Contact.deleted_at.is_(None)).order_by(Contact.created_at.asc()).all()
    return {"items": [contact_to_dict(item) for item in items], "total": len(items)}


@router.get("/recently-deleted")
def list_recently_deleted_contacts(db: Session = Depends(get_db)):
    cutoff = utcnow() - timedelta(days=7)
    items = (
        db.query(Contact)
        .filter(Contact.deleted_at.is_not(None), Contact.deleted_at >= cutoff)
        .order_by(Contact.deleted_at.desc())
        .all()
    )
    return {"items": [contact_to_dict(item) for item in items], "total": len(items)}


@router.post("")
def create_contact(payload: ContactCreate, db: Session = Depends(get_db)):
    contact = Contact(
        email=payload.email.strip().lower(),
        creator_name=payload.creator_name,
        business_name=payload.business_name,
        website_url=payload.website_url,
        source=payload.source,
        provenance=payload.provenance,
        notes=payload.notes,
        personalization=payload.personalization,
        lead_category=payload.lead_category,
        custom_fields=custom_fields_with_tags(
            json.dumps(
                {
                    key: value
                    for key, value in payload.model_dump(exclude_unset=True).items()
                    if key
                    not in {
                        "email",
                        "creator_name",
                        "business_name",
                        "website_url",
                        "source",
                        "provenance",
                        "notes",
                        "personalization",
                        "lead_category",
                        "tags",
                    }
                }
            ),
            payload.tags,
        ),
    )
    db.add(contact)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="duplicate contact") from exc
    emit_event(db, "import.committed", entity_type="contact", entity_id=contact.id, payload={"source": payload.source})
    db.commit()
    return contact_to_dict(contact)


@router.patch("/{contact_id}")
def patch_contact(contact_id: str, payload: ContactPatch, db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="contact not found")
    data = payload.model_dump(exclude_unset=True)
    if "auto_reply_override" in data and data["auto_reply_override"] not in {None, "enabled", "disabled", "propose"}:
        raise HTTPException(status_code=422, detail="auto_reply_override must be enabled, disabled, propose, or null")
    previous_status = contact.status
    for key, value in data.items():
        setattr(contact, key, value)
    next_status = data.get("status")
    if next_status != previous_status and next_status in STOP_REASON_BY_STATUS:
        stop_contact_send_work(db, contact.id, STOP_REASON_BY_STATUS[next_status])
    emit_event(db, "reply.classified" if data.get("status") in {"replied", "bounced"} else "settings.updated", entity_type="contact", entity_id=contact.id, payload=data)
    db.commit()
    return contact_to_dict(contact)


@router.delete("/{contact_id}")
def delete_contact(contact_id: str, db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="contact not found")
    newly_deleted = contact.deleted_at is None
    if newly_deleted:
        contact.deleted_at = utcnow()
    affected = stop_contact_send_work(db, contact.id, "CONTACT_DELETED") if newly_deleted else {
        "queue_ids": [],
        "followup_ids": [],
        "pending_email_action_ids": [],
        "pending_agent_action_ids": [],
    }
    emit_event(
        db,
        "contact.deleted",
        entity_type="contact",
        entity_id=contact.id,
        payload={
            "email": contact.email,
            "cancelled_queue": len(affected["queue_ids"]),
            "stopped_followups": len(affected["followup_ids"]),
            "cancelled_email_actions": len(affected["pending_email_action_ids"]),
            "cancelled_agent_actions": len(affected["pending_agent_action_ids"]),
        },
    )
    db.commit()
    return contact_to_dict(contact)


@router.post("/{contact_id}/restore")
def restore_contact(contact_id: str, db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="contact not found")
    contact.deleted_at = None
    emit_event(db, "contact.restored", entity_type="contact", entity_id=contact.id, payload={"email": contact.email})
    db.commit()
    return contact_to_dict(contact)
