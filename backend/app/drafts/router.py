from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.gateway import AIGateway
from app.ai.gateway import GROQ_MODEL_DEFAULT
from app.ai.gemini_pool import GEMINI_MODEL_DEFAULT
from app.ai.prompts import sender_profile_from_settings
from app.ai.schema import AIFailure, DraftSuggestion
from app.audit.service import emit_event
from app.core.idempotency import sha256_key
from app.core.time import iso, utcnow
from app.db.models import Contact, Draft, SendQueue
from app.db.session import SessionLocal, get_db
from app.send.auto_process import schedule_auto_process
from app.send.queue_worker import create_queue_entry
from app.settings.service import get_int, get_key_list, get_value

router = APIRouter(prefix="/api/drafts", tags=["drafts"])
BULK_JOBS: dict[str, dict] = {}
REQUEUE_STATUSES = {"failed", "blocked"}


class DraftCreate(BaseModel):
    contact_id: str
    subject: str = ""
    body: str = ""
    warnings: list[str] = []


class DraftGenerate(BaseModel):
    contact_id: str
    provider: str = "auto"
    tone: str = "professional"
    length: str = "medium"
    instruction: str | None = None


class BulkDraftGenerate(BaseModel):
    contact_ids: list[str]
    provider: str = "auto"
    tone: str | None = None


class BulkApprove(BaseModel):
    draft_ids: list[str]


class DraftApprove(BaseModel):
    sequence_num: int | None = None


class DraftPatch(BaseModel):
    subject: str | None = None
    body: str | None = None
    warnings: list[str] | None = None


def draft_to_dict(draft: Draft, error_code: str | None = None) -> dict:
    return {
        "id": draft.id,
        "contact_id": draft.contact_id,
        "subject": draft.subject,
        "body": draft.body,
        "ai_provider": draft.ai_provider,
        "ai_model": draft.ai_model,
        "warnings": json.loads(draft.warnings or "[]"),
        "source": draft.source,
        "rejected": draft.rejected,
        "approved": draft.approved,
        "approved_at": iso(draft.approved_at),
        "created_at": iso(draft.created_at),
        "updated_at": iso(draft.updated_at),
        "error_code": error_code,
    }


def build_gateway(db: Session) -> AIGateway:
    return AIGateway(
        get_key_list(db, "groq_keys"),
        get_key_list(db, "gemini_keys"),
        get_value(db, "campaign_context"),
        sender_profile_from_settings(db),
        get_value(db, "groq_model", GROQ_MODEL_DEFAULT),
        get_value(db, "gemini_model", GEMINI_MODEL_DEFAULT),
    )


def provider_model(gateway: AIGateway, provider: str) -> str | None:
    return gateway.model_for_provider(provider)


def _next_sequence_num(db: Session, contact_id: str) -> int:
    rows = db.query(SendQueue).filter_by(contact_id=contact_id).order_by(SendQueue.sequence_num.asc()).all()
    for row in rows:
        if row.sequence_num > 1 and row.status in REQUEUE_STATUSES:
            return row.sequence_num
    return max([row.sequence_num for row in rows] or [0]) + 1


def store_generated_draft(
    db: Session,
    contact: Contact,
    provider: str,
    gateway: AIGateway,
    suggestion: DraftSuggestion,
    error_code: str | None = None,
) -> Draft:
    draft = Draft(
        contact_id=contact.id,
        subject=suggestion.subject,
        body=suggestion.body,
        ai_provider=provider,
        ai_model=provider_model(gateway, provider),
        warnings=json.dumps(suggestion.warnings),
        approved=False,
    )
    db.add(draft)
    contact.status = "draft_ready" if not error_code else "draft_needed"
    db.flush()
    return draft


def _queue_approved_draft(db: Session, draft: Draft, contact: Contact, sequence_num: int = 1) -> SendQueue:
    existing_queue = db.query(SendQueue).filter_by(contact_id=contact.id, sequence_num=sequence_num).first()
    if existing_queue and existing_queue.status not in REQUEUE_STATUSES:
        if existing_queue.draft_id != draft.id:
            reason = "sequence_already_sent" if existing_queue.status == "sent" else "sequence_already_queued"
            detail = {
                "reason": reason,
                "queue_id": existing_queue.id,
                "draft_id": existing_queue.draft_id,
                "status": existing_queue.status,
            }
            if existing_queue.status == "sent":
                detail["next_sequence_num"] = _next_sequence_num(db, contact.id)
            raise HTTPException(
                status_code=409,
                detail=detail,
            )
        return existing_queue

    if existing_queue:
        delay = get_int(db, "send_delay_s")
        previous_status = existing_queue.status
        existing_queue.draft_id = draft.id
        existing_queue.idempotency_key = sha256_key(contact.id, sequence_num, draft.id)
        existing_queue.scheduled_at = utcnow() + timedelta(seconds=delay) if delay > 0 else utcnow()
        existing_queue.status = "pending"
        existing_queue.policy_block_reasons = json.dumps([])
        db.flush()
        emit_event(db, "queue.entry_requeued", entity_type="send_queue", entity_id=existing_queue.id, payload={"previous_status": previous_status})
        return existing_queue

    return create_queue_entry(db, contact.id, draft.id, sequence_num)


@router.get("")
def list_drafts(db: Session = Depends(get_db)):
    rows = db.query(Draft).order_by(Draft.created_at.asc()).all()
    return {"items": [draft_to_dict(row) for row in rows], "total": len(rows)}


@router.post("")
def create_draft(payload: DraftCreate, db: Session = Depends(get_db)):
    contact = db.get(Contact, payload.contact_id)
    if not contact or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="contact not found")
    draft = Draft(
        contact_id=payload.contact_id,
        subject=payload.subject,
        body=payload.body,
        ai_provider="manual",
        warnings=json.dumps(payload.warnings),
        approved=False,
    )
    db.add(draft)
    db.flush()
    emit_event(db, "draft.created", entity_type="draft", entity_id=draft.id)
    db.commit()
    return draft_to_dict(draft)


@router.patch("/{draft_id}")
def patch_draft(draft_id: str, payload: DraftPatch, db: Session = Depends(get_db)):
    draft = db.get(Draft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="draft not found")
    if payload.subject is not None:
        draft.subject = payload.subject
    if payload.body is not None:
        draft.body = payload.body
    if payload.warnings is not None:
        draft.warnings = json.dumps(payload.warnings)
    emit_event(db, "draft.edited", entity_type="draft", entity_id=draft.id)
    db.commit()
    return draft_to_dict(draft)


@router.post("/generate")
async def generate_draft(payload: DraftGenerate, db: Session = Depends(get_db)):
    contact = db.get(Contact, payload.contact_id)
    if not contact or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="contact not found")
    gateway = build_gateway(db)
    result = await gateway.generate_draft(contact, payload.provider, payload.tone, payload.length, payload.instruction)
    error_code = None
    if isinstance(result, AIFailure):
        suggestion = DraftSuggestion.model_construct(subject="", body="", warnings=[])
        error_code = result.error_code
        failure_payload = {"provider": result.provider, "error_code": result.error_code}
        model = provider_model(gateway, result.provider)
        if model:
            failure_payload["model"] = model
        emit_event(db, "draft.ai_failed", entity_type="contact", entity_id=contact.id, payload=failure_payload)
    else:
        suggestion = result
        event_payload = {"provider": payload.provider}
        model = provider_model(gateway, payload.provider)
        if model:
            event_payload["model"] = model
        emit_event(db, "draft.ai_generated", entity_type="contact", entity_id=contact.id, payload=event_payload)
    draft = store_generated_draft(db, contact, payload.provider, gateway, suggestion, error_code)
    db.commit()
    return draft_to_dict(draft, error_code=error_code)


@router.post("/generate-bulk")
def generate_bulk(payload: BulkDraftGenerate):
    job_id = uuid.uuid4().hex
    BULK_JOBS[job_id] = {
        "job_id": job_id,
        "status": "running",
        "total": len(payload.contact_ids),
        "completed": 0,
        "generated": 0,
        "failed": 0,
        "skipped": 0,
        "errors": [],
    }
    thread = threading.Thread(target=_run_bulk_generation, args=(job_id, payload), daemon=True)
    thread.start()
    return BULK_JOBS[job_id]


@router.get("/bulk-status/{job_id}")
def bulk_status(job_id: str):
    job = BULK_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="bulk job not found")
    return job


def _run_bulk_generation(job_id: str, payload: BulkDraftGenerate) -> None:
    job = BULK_JOBS[job_id]
    with SessionLocal() as db:
        gateway = build_gateway(db)
        tone = payload.tone or get_value(db, "sender_tone", "Professional")
        for contact_id in payload.contact_ids:
            try:
                contact = db.get(Contact, contact_id)
                if not contact or contact.deleted_at is not None:
                    job["skipped"] += 1
                    continue
                existing = db.query(Draft).filter(Draft.contact_id == contact.id, Draft.approved.is_(False)).first()
                if existing:
                    job["skipped"] += 1
                    continue
                result = asyncio.run(gateway.generate_draft(contact, payload.provider, tone, "medium"))
                if isinstance(result, AIFailure):
                    suggestion = DraftSuggestion.model_construct(subject="", body="", warnings=[])
                    store_generated_draft(db, contact, payload.provider, gateway, suggestion, result.error_code)
                    emit_event(
                        db,
                        "draft.ai_failed",
                        entity_type="contact",
                        entity_id=contact.id,
                        payload={"provider": result.provider, "error_code": result.error_code},
                    )
                    job["failed"] += 1
                else:
                    draft = store_generated_draft(db, contact, payload.provider, gateway, result)
                    event_payload = {"provider": payload.provider}
                    model = provider_model(gateway, payload.provider)
                    if model:
                        event_payload["model"] = model
                    emit_event(db, "draft.ai_generated", entity_type="contact", entity_id=contact.id, payload=event_payload)
                    job["generated"] += 1
                db.commit()
                if payload.provider == "groq":
                    time.sleep(1)
            except Exception as exc:
                db.rollback()
                job["failed"] += 1
                job["errors"].append(exc.__class__.__name__)
            finally:
                job["completed"] += 1
        job["status"] = "completed"


@router.post("/approve-bulk")
def approve_bulk(payload: BulkApprove, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    approved = 0
    queued = 0
    for draft_id in payload.draft_ids:
        draft = db.get(Draft, draft_id)
        if not draft:
            continue
        contact = db.get(Contact, draft.contact_id)
        if not contact or contact.deleted_at is not None:
            continue
        if not draft.approved:
            draft.approved = True
            draft.approved_at = utcnow()
            approved += 1
        contact.status = "approved"
        queue = _queue_approved_draft(db, draft, contact, 1)
        emit_event(db, "draft.approved", entity_type="draft", entity_id=draft.id, payload={"queue_id": queue.id})
        queued += 1
    db.commit()
    if queued:
        schedule_auto_process(background_tasks)
    return {"approved": approved, "queued": queued}


@router.post("/{draft_id}/subject-variants")
async def subject_variants(draft_id: str, db: Session = Depends(get_db)):
    draft = db.get(Draft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="draft not found")
    gateway = build_gateway(db)
    result = await gateway.generate_subject_variants(draft)
    if isinstance(result, AIFailure):
        emit_event(db, "draft.ai_failed", entity_type="draft", entity_id=draft.id, payload={"provider": result.provider, "error_code": result.error_code})
        db.commit()
        return {"variants": [], "error_code": result.error_code}
    emit_event(db, "draft.ai_generated", entity_type="draft", entity_id=draft.id, payload={"provider": "groq", "model": gateway.groq_model, "kind": "subject_variants"})
    db.commit()
    return {"variants": result}


@router.post("/{draft_id}/approve")
def approve_draft(draft_id: str, background_tasks: BackgroundTasks, payload: DraftApprove | None = None, db: Session = Depends(get_db)):
    draft = db.get(Draft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="draft not found")
    draft.approved = True
    draft.approved_at = utcnow()
    contact = db.get(Contact, draft.contact_id)
    if not contact or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="contact not found")
    contact.status = "approved"
    sequence_num = payload.sequence_num if payload and payload.sequence_num else 1
    if sequence_num < 1:
        raise HTTPException(status_code=422, detail="sequence_num must be >= 1")
    queue = _queue_approved_draft(db, draft, contact, sequence_num)
    emit_event(db, "draft.approved", entity_type="draft", entity_id=draft.id, payload={"queue_id": queue.id, "sequence_num": sequence_num})
    db.commit()
    schedule_auto_process(background_tasks)
    return {**draft_to_dict(draft), "queue_id": queue.id}
