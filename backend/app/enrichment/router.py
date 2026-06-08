from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Contact, Draft
from app.db.session import get_db
from app.enrichment.service import (
    contact_evidence_payload,
    create_manual_lead_fact,
    enrichment_summary,
    ensure_seed_facts_for_contact,
    evidence_check,
    lead_fact_to_dict,
)

router = APIRouter(prefix="/api/enrichment", tags=["enrichment"])


class LeadFactCreate(BaseModel):
    field_key: str = Field(min_length=1)
    field_value: str = Field(min_length=1)
    source_url: str | None = None
    source_label: str = Field(min_length=1)
    source_type: str = "manual"
    confidence: float = 0.7
    raw_snippet: str | None = None


class EvidenceCheckRequest(BaseModel):
    draft_id: str | None = None
    subject: str | None = None
    body: str | None = None


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    return enrichment_summary(db)


@router.get("/contacts/{contact_id}")
def contact_evidence(contact_id: str, db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="contact not found")
    return contact_evidence_payload(db, contact)


@router.post("/contacts/{contact_id}/facts")
def create_fact(contact_id: str, payload: LeadFactCreate, db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="contact not found")
    try:
        fact = create_manual_lead_fact(
            db,
            contact,
            field_key=payload.field_key,
            field_value=payload.field_value,
            source_url=payload.source_url,
            source_label=payload.source_label,
            source_type=payload.source_type,
            confidence=payload.confidence,
            raw_snippet=payload.raw_snippet,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return lead_fact_to_dict(fact)


@router.post("/contacts/{contact_id}/seed")
def seed_facts(contact_id: str, db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="contact not found")
    facts = ensure_seed_facts_for_contact(db, contact)
    db.commit()
    return {"items": [lead_fact_to_dict(fact) for fact in facts], "total": len(facts)}


@router.post("/contacts/{contact_id}/evidence-check")
def check_contact_evidence(contact_id: str, payload: EvidenceCheckRequest | None = None, db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="contact not found")
    content = None
    if payload:
        content = f"{payload.subject or ''}\n{payload.body or ''}"
    if payload and payload.draft_id:
        draft = db.get(Draft, payload.draft_id)
        if not draft or draft.contact_id != contact.id:
            raise HTTPException(status_code=404, detail="draft not found")
        content = f"{draft.subject or ''}\n{draft.body or ''}"
    result = evidence_check(db, contact, content, draft_id=payload.draft_id if payload else None, persist=bool(payload and payload.draft_id))
    if payload and payload.draft_id:
        db.commit()
    return result


@router.get("/workbook")
def enrichment_workbook(db: Session = Depends(get_db)):
    contacts = db.query(Contact).filter(Contact.deleted_at.is_(None)).order_by(Contact.created_at.asc()).all()
    rows = []
    for contact in contacts:
        check = evidence_check(db, contact)
        rows.append(
            {
                "contact_id": contact.id,
                "email": contact.email,
                "name": contact.creator_name or contact.business_name or contact.email,
                "company": contact.business_name,
                "website": contact.website_url,
                "evidence_status": check["status"],
                "supported_claims": len(check["supported_claims"]),
                "stale_facts": len(check["stale_facts"]),
                "draft_readiness": "neutral_required" if check["neutral_copy_required"] else "source_backed",
            }
        )
    return {"items": rows, "total": len(rows), "summary": enrichment_summary(db)}
