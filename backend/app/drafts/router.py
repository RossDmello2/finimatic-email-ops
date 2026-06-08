from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.gateway import AIGateway
from app.ai.gateway import GROQ_MODEL_DEFAULT
from app.ai.gemini_pool import GEMINI_MODEL_DEFAULT
from app.ai.prompts import sender_profile_from_settings
from app.ai.schema import AIFailure, DraftSuggestion
from app.audit.service import emit_event
from app.contacts.utils import resolve_tokens
from app.core.idempotency import sha256_key
from app.core.time import iso, utcnow
from app.db.models import Contact, Draft, SendAttempt, SendQueue, Template
from app.db.session import SessionLocal, get_db
from app.drafts.service import draft_campaign_block_reason, draft_content_block_reasons, invalidate_draft_approval
from app.enrichment.service import evidence_check, evidence_policy_for_draft
from app.send.auto_process import auto_process_enabled
from app.send.policy import prequeue_block_reasons
from app.send.queue_worker import (
    APPROVAL_DELAY_SCHEDULE_SOURCE,
    EXPLICIT_SEND_NOW_SCHEDULE_SOURCE,
    create_queue_entry,
    process_pending_queue,
    queue_to_dict,
)
from app.send.sequence import provider_acceptance_evidence_present, sequence_prerequisite_met
from app.settings.service import get_bool, get_int, get_key_list, get_value

router = APIRouter(prefix="/api/drafts", tags=["drafts"])
BULK_JOBS: dict[str, dict] = {}
REQUEUE_STATUSES = {"failed", "blocked", "skipped", "simulated", "cancelled"}


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
    template_id: str | None = None
    instruction: str | None = None
    mode: Literal["ai_only", "template_only", "template_plus_ai"] | None = None
    generation_mode: Literal["ai", "template_fill", "template_ai"] | None = None


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
    rows_by_sequence = {
        row.sequence_num: row
        for row in rows
        if row.sequence_num >= 1
    }
    sequence_num = 1
    while True:
        row = rows_by_sequence.get(sequence_num)
        if row is None:
            return sequence_num
        if _queue_has_provider_acceptance(db, row):
            sequence_num += 1
            continue
        if row.status in REQUEUE_STATUSES:
            return sequence_num
        if row.status in {"provider_accepted", "sent"}:
            return sequence_num
        return sequence_num


def store_generated_draft(
    db: Session,
    contact: Contact,
    provider: str,
    gateway: AIGateway,
    suggestion: DraftSuggestion,
    error_code: str | None = None,
) -> Draft:
    warnings = list(suggestion.warnings)
    draft = Draft(
        contact_id=contact.id,
        subject=suggestion.subject,
        body=suggestion.body,
        ai_provider=provider,
        ai_model=provider_model(gateway, provider),
        warnings=json.dumps(warnings),
        approved=False,
    )
    db.add(draft)
    contact.status = "draft_ready" if not error_code else "draft_needed"
    db.flush()
    if not error_code:
        check = evidence_policy_for_draft(db, draft, contact, persist=True)
        if check["neutral_copy_required"]:
            neutral_subject = check.get("neutral_subject") or draft.subject
            neutral_body = check.get("neutral_body") or draft.body
            draft.subject = neutral_subject
            draft.body = neutral_body
            warnings.append("Neutral copy required because approved evidence was missing.")
            draft.warnings = json.dumps(warnings[:10])
            emit_event(
                db,
                "draft.neutral_fallback_applied",
                entity_type="draft",
                entity_id=draft.id,
                payload={"contact_id": contact.id, "reason": "source_backed_personalization_missing"},
            )
    return draft


def _resolved_template_suggestion(template: Template, contact: Contact, warnings: list[str] | None = None) -> DraftSuggestion | AIFailure:
    subject = resolve_tokens(template.subject_template, contact).strip()
    body = resolve_tokens(template.body_template, contact).strip()
    if not subject or len(body) < 10:
        return AIFailure(error_code="template_invalid", provider="template", detail="empty_subject_or_body")
    return DraftSuggestion(subject=subject[:200], body=body[:5000], warnings=warnings or [])


def _bulk_generation_mode(payload: BulkDraftGenerate) -> Literal["ai", "template_fill", "template_ai"]:
    legacy_modes = {
        "ai_only": "ai",
        "template_only": "template_fill",
        "template_plus_ai": "template_ai",
    }
    mapped_legacy = legacy_modes.get(payload.mode) if payload.mode else None
    if payload.generation_mode and mapped_legacy and payload.generation_mode != mapped_legacy:
        raise HTTPException(status_code=422, detail="conflicting_bulk_generation_modes")
    return payload.generation_mode or mapped_legacy or "ai"


def _legacy_bulk_mode(generation_mode: str) -> str:
    return {
        "ai": "ai_only",
        "template_fill": "template_only",
        "template_ai": "template_plus_ai",
    }[generation_mode]


def _template_instruction(
    template: Template,
    resolved_subject: str,
    resolved_body: str,
    operator_instruction: str | None,
) -> str:
    extra = " ".join((operator_instruction or "").split())[:300]
    parts = [
        f"Additional operator instruction: {extra or 'none'}",
        "Use this resolved template as the required structure. Keep the same sections and call-to-action.",
        "Personalize only with known contact fields and operator notes. Do not invent facts.",
        f"Template name: {template.name}",
        f"Template subject: {resolved_subject}",
        f"Template body: {resolved_body}",
    ]
    return "\n".join(parts)[:1200]


def _apply_bulk_draft(
    db: Session,
    contact: Contact,
    *,
    subject: str,
    body: str,
    provider: str,
    model: str | None,
    warnings: list[str],
    source: str,
    apply_neutral_fallback: bool = True,
) -> tuple[Draft, str]:
    draft = db.query(Draft).filter(Draft.contact_id == contact.id, Draft.approved.is_(False)).order_by(Draft.created_at.desc()).first()
    action = "updated" if draft else "created"
    if not draft:
        draft = Draft(contact_id=contact.id, approved=False)
        db.add(draft)
    draft.subject = subject
    draft.body = body
    draft.ai_provider = provider
    draft.ai_model = model
    draft.warnings = json.dumps(warnings[:10])
    draft.source = source
    draft.rejected = False
    draft.approved = False
    draft.approved_at = None
    contact.status = "draft_ready"
    db.flush()
    check = evidence_policy_for_draft(db, draft, contact, persist=True)
    if apply_neutral_fallback and check["neutral_copy_required"]:
        neutral_subject = check.get("neutral_subject") or draft.subject
        neutral_body = check.get("neutral_body") or draft.body
        draft.subject = neutral_subject
        draft.body = neutral_body
        neutral_warnings = [*warnings, "Neutral copy required because approved evidence was missing."]
        draft.warnings = json.dumps(neutral_warnings[:10])
        emit_event(
            db,
            "draft.neutral_fallback_applied",
            entity_type="draft",
            entity_id=draft.id,
            payload={"contact_id": contact.id, "reason": "source_backed_personalization_missing"},
        )
    return draft, action


def _latest_queue_attempt(db: Session, queue: SendQueue) -> SendAttempt | None:
    return (
        db.query(SendAttempt)
        .filter(SendAttempt.queue_id == queue.id)
        .order_by(SendAttempt.created_at.desc().nullslast(), SendAttempt.id.desc())
        .first()
    )


def _queue_has_provider_acceptance(db: Session, queue: SendQueue) -> bool:
    attempts = (
        db.query(SendAttempt)
        .filter(
            SendAttempt.queue_id == queue.id,
            SendAttempt.provider_accepted.is_(True),
        )
        .order_by(SendAttempt.created_at.desc().nullslast(), SendAttempt.id.desc())
        .all()
    )
    return any(provider_acceptance_evidence_present(attempt) for attempt in attempts)


def _queue_can_be_reused_without_provider_risk(db: Session, queue: SendQueue) -> bool:
    latest = _latest_queue_attempt(db, queue)
    if latest is None:
        return True
    if latest.provider_accepted is True or latest.status == "reconciliation_required":
        return False
    return latest.provider_contacted is False


def _sequence_conflict_detail(db: Session, queue: SendQueue, draft: Draft) -> dict:
    provider_accepted = _queue_has_provider_acceptance(db, queue)
    if provider_accepted:
        return {
            "reason": "sequence_already_sent",
            "queue_id": queue.id,
            "draft_id": queue.draft_id,
            "status": queue.status,
            "next_sequence_num": _next_sequence_num(db, draft.contact_id),
        }
    if queue.status in {"processing", "reconciliation_required"} or not _queue_can_be_reused_without_provider_risk(db, queue):
        return {
            "reason": "queue_reconciliation_required",
            "queue_id": queue.id,
            "draft_id": queue.draft_id,
            "status": queue.status,
        }
    if queue.status == "cancelled" and queue.draft_id != draft.id:
        return {
            "reason": "queue_cancelled_requires_explicit_new_action",
            "queue_id": queue.id,
            "draft_id": queue.draft_id,
            "status": queue.status,
        }
    return {
        "reason": "sequence_already_queued",
        "queue_id": queue.id,
        "draft_id": queue.draft_id,
        "status": queue.status,
    }


def _queue_approved_draft(
    db: Session,
    draft: Draft,
    contact: Contact,
    sequence_num: int = 1,
    *,
    immediate: bool = False,
) -> SendQueue:
    if draft.rejected:
        raise HTTPException(status_code=422, detail={"blocked": ["DRAFT_REJECTED"]})
    _validate_queue_approval(db, draft, contact)
    if not sequence_prerequisite_met(db, contact.id, sequence_num):
        raise HTTPException(
            status_code=409,
            detail={"reason": "prior_sequence_not_provider_accepted", "sequence_num": sequence_num},
        )
    existing_queue = db.query(SendQueue).filter_by(contact_id=contact.id, sequence_num=sequence_num).first()
    if existing_queue:
        if _queue_has_provider_acceptance(db, existing_queue):
            if existing_queue.draft_id != draft.id:
                raise HTTPException(status_code=409, detail=_sequence_conflict_detail(db, existing_queue, draft))
            return existing_queue
        if existing_queue.status in {"processing", "reconciliation_required"} or not _queue_can_be_reused_without_provider_risk(
            db,
            existing_queue,
        ):
            raise HTTPException(status_code=409, detail=_sequence_conflict_detail(db, existing_queue, draft))
        if existing_queue.status == "cancelled" and existing_queue.draft_id != draft.id:
            raise HTTPException(status_code=409, detail=_sequence_conflict_detail(db, existing_queue, draft))
        if existing_queue.draft_id == draft.id and existing_queue.status not in {"pending", *REQUEUE_STATUSES}:
            return existing_queue

    if existing_queue:
        delay = get_int(db, "send_delay_s")
        previous_status = existing_queue.status
        existing_queue.draft_id = draft.id
        existing_queue.idempotency_key = sha256_key(contact.id, sequence_num, draft.id)
        existing_queue.scheduled_at = utcnow() if immediate else utcnow() + timedelta(seconds=delay) if delay > 0 else utcnow()
        existing_queue.schedule_source = (
            EXPLICIT_SEND_NOW_SCHEDULE_SOURCE if immediate else APPROVAL_DELAY_SCHEDULE_SOURCE
        )
        existing_queue.status = "pending"
        existing_queue.policy_block_reasons = json.dumps([])
        db.flush()
        emit_event(
            db,
            "queue.entry_requeued",
            entity_type="send_queue",
            entity_id=existing_queue.id,
            payload={
                "previous_status": previous_status,
                "draft_id": draft.id,
                "sequence_num": sequence_num,
                "schedule_source": existing_queue.schedule_source,
            },
        )
        return existing_queue

    queue = create_queue_entry(db, contact.id, draft.id, sequence_num)
    if immediate and queue.status in {"pending", "skipped"}:
        queue.scheduled_at = utcnow()
        queue.schedule_source = EXPLICIT_SEND_NOW_SCHEDULE_SOURCE
        db.flush()
    return queue


def _validate_queue_approval(db: Session, draft: Draft, contact: Contact) -> None:
    blocked = prequeue_block_reasons(contact, db)
    if blocked:
        raise HTTPException(status_code=422, detail={"blocked": blocked})
    evidence = evidence_policy_for_draft(db, draft, contact, persist=True)
    if evidence["neutral_copy_required"]:
        emit_event(
            db,
            "draft.approval_blocked",
            entity_type="draft",
            entity_id=draft.id,
            payload={"blocked": ["UNSUPPORTED_PERSONALIZATION"], "evidence_status": evidence["status"]},
        )
        raise HTTPException(
            status_code=422,
            detail={"blocked": ["UNSUPPORTED_PERSONALIZATION"], "evidence_check": evidence},
        )


def _dry_run_direct_send_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "reason": "DRY_RUN_ENABLED",
            "message": "Live mode is required before approval can send email. Turn off Dry Run, then approve again.",
        },
    )


def _delivery_status(result: dict, queue: SendQueue | None) -> str:
    if result.get("provider_accepted"):
        return "provider_accepted"
    if result.get("sent"):
        return "sent"
    if result.get("simulated"):
        return "simulated"
    if result.get("reconciliation_required"):
        return "reconciliation_required"
    if result.get("deferred"):
        return "deferred"
    if result.get("blocked"):
        return "blocked"
    if result.get("skipped"):
        return "dry_run_blocked"
    if queue is None:
        return "queued"
    if queue.status == "provider_accepted":
        return "provider_accepted"
    if queue.status == "sent":
        return "sent"
    if queue.status == "pending":
        return "deferred"
    if queue.status == "blocked":
        return "blocked"
    if queue.status == "skipped":
        return "dry_run_blocked"
    if queue.status == "simulated":
        return "simulated"
    if queue.status == "reconciliation_required":
        return "reconciliation_required"
    if queue.status == "failed":
        return "failed"
    return queue.status or "queued"


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
    content_changed = (
        (payload.subject is not None and payload.subject != draft.subject)
        or (payload.body is not None and payload.body != draft.body)
    )
    if content_changed:
        invalidate_draft_approval(db, draft)
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
def generate_bulk(payload: BulkDraftGenerate, db: Session = Depends(get_db)):
    generation_mode = _bulk_generation_mode(payload)
    if generation_mode in {"template_fill", "template_ai"} and not payload.template_id:
        raise HTTPException(status_code=422, detail="template_id_required")
    if payload.template_id and not db.get(Template, payload.template_id):
        raise HTTPException(status_code=404, detail="template not found")
    job_id = uuid.uuid4().hex
    BULK_JOBS[job_id] = {
        "job_id": job_id,
        "status": "running",
        "total": len(payload.contact_ids),
        "completed": 0,
        "generated": 0,
        "created": 0,
        "updated": 0,
        "failed": 0,
        "skipped": 0,
        "errors": [],
        "mode": payload.mode or _legacy_bulk_mode(generation_mode),
        "generation_mode": generation_mode,
        "results": [],
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
        generation_mode = _bulk_generation_mode(payload)
        legacy_mode = payload.mode or _legacy_bulk_mode(generation_mode)
        gateway = None if generation_mode == "template_fill" else build_gateway(db)
        tone = payload.tone or get_value(db, "sender_tone", "Professional")
        template = db.get(Template, payload.template_id) if payload.template_id else None
        for contact_id in payload.contact_ids:
            result_row = {
                "contact_id": contact_id,
                "status": "pending",
                "mode": generation_mode,
                "legacy_mode": legacy_mode,
            }
            try:
                contact = db.get(Contact, contact_id)
                if not contact or contact.deleted_at is not None:
                    job["skipped"] += 1
                    result_row.update({"status": "skipped", "reason": "contact_not_found"})
                    job["results"].append(result_row)
                    continue
                result_row["email"] = contact.email
                if generation_mode == "template_fill":
                    if not template:
                        raise ValueError("template_not_found")
                    suggestion = _resolved_template_suggestion(template, contact)
                    if isinstance(suggestion, AIFailure):
                        contact.status = "draft_needed"
                        job["failed"] += 1
                        job["errors"].append(suggestion.error_code)
                        result_row.update(
                            {
                                "status": "failed",
                                "reason": suggestion.error_code,
                                "provider": suggestion.provider,
                            }
                        )
                        job["results"].append(result_row)
                        emit_event(
                            db,
                            "draft.template_failed",
                            entity_type="contact",
                            entity_id=contact.id,
                            payload={"template_id": template.id, "error_code": suggestion.error_code, "bulk": True},
                        )
                        db.commit()
                        continue
                    draft, action = _apply_bulk_draft(
                        db,
                        contact,
                        subject=suggestion.subject,
                        body=suggestion.body,
                        provider="template",
                        model=None,
                        warnings=[*suggestion.warnings, f"Filled from template: {template.name}"],
                        source="template_fill",
                        apply_neutral_fallback=False,
                    )
                    event_payload = {
                        "contact_id": contact.id,
                        "template_id": template.id,
                        "bulk": True,
                        "mode": legacy_mode,
                        "generation_mode": generation_mode,
                    }
                    emit_event(db, "draft.template_generated", entity_type="draft", entity_id=draft.id, payload=event_payload)
                    emit_event(db, "draft.template_filled", entity_type="draft", entity_id=draft.id, payload=event_payload)
                else:
                    instruction = payload.instruction
                    resolved_subject = ""
                    resolved_body = ""
                    if generation_mode == "template_ai":
                        if not template:
                            raise ValueError("template_not_found")
                        resolved_subject = resolve_tokens(template.subject_template, contact)
                        resolved_body = resolve_tokens(template.body_template, contact)
                        instruction = _template_instruction(
                            template,
                            resolved_subject,
                            resolved_body,
                            payload.instruction,
                        )
                    assert gateway is not None
                    result = asyncio.run(gateway.generate_draft(contact, payload.provider, tone, "medium", instruction))
                    if isinstance(result, AIFailure):
                        emit_event(
                            db,
                            "draft.ai_failed",
                            entity_type="contact",
                            entity_id=contact.id,
                            payload={
                                "provider": result.provider,
                                "error_code": result.error_code,
                                "bulk": True,
                                "mode": legacy_mode,
                                "generation_mode": generation_mode,
                            },
                        )
                        job["errors"].append(result.error_code)
                        if generation_mode == "template_ai" and template:
                            fallback = _resolved_template_suggestion(
                                template,
                                contact,
                                warnings=[f"AI personalization failed ({result.error_code}); used resolved template."],
                            )
                            if isinstance(fallback, AIFailure):
                                contact.status = "draft_needed"
                                job["failed"] += 1
                                result_row.update(
                                    {
                                        "status": "failed",
                                        "reason": fallback.error_code,
                                        "provider": fallback.provider,
                                    }
                                )
                                job["results"].append(result_row)
                                db.commit()
                                continue
                            draft, action = _apply_bulk_draft(
                                db,
                                contact,
                                subject=fallback.subject,
                                body=fallback.body,
                                provider="template",
                                model=None,
                                warnings=fallback.warnings,
                                source="template_ai_fallback",
                                apply_neutral_fallback=False,
                            )
                            result_row["reason"] = f"ai_failed_{result.error_code}_template_fallback"
                        else:
                            contact.status = "draft_needed"
                            job["failed"] += 1
                            result_row.update({"status": "failed", "reason": result.error_code, "provider": result.provider})
                            job["results"].append(result_row)
                            db.commit()
                            continue
                    else:
                        assert gateway is not None
                        warnings = list(result.warnings)
                        if generation_mode == "template_ai" and template:
                            warnings.append(f"AI rewrite based on template: {template.name}")
                        draft, action = _apply_bulk_draft(
                            db,
                            contact,
                            subject=result.subject,
                            body=result.body,
                            provider=payload.provider,
                            model=provider_model(gateway, payload.provider),
                            warnings=warnings,
                            source=generation_mode,
                            apply_neutral_fallback=generation_mode != "template_ai",
                        )
                        event_payload = {
                            "provider": payload.provider,
                            "bulk": True,
                            "mode": legacy_mode,
                            "generation_mode": generation_mode,
                        }
                        model = provider_model(gateway, payload.provider)
                        if model:
                            event_payload["model"] = model
                        if template:
                            event_payload["template_id"] = template.id
                        emit_event(db, "draft.ai_generated", entity_type="contact", entity_id=contact.id, payload=event_payload)
                if action == "created":
                    job["created"] += 1
                else:
                    job["updated"] += 1
                job["generated"] += 1
                result_row.update({"status": "generated", "draft_id": draft.id, "action": action})
                job["results"].append(result_row)
                db.commit()
                if generation_mode != "template_fill" and payload.provider == "groq":
                    time.sleep(1)
            except Exception as exc:
                db.rollback()
                job["failed"] += 1
                job["errors"].append(exc.__class__.__name__)
                result_row.update({"status": "failed", "reason": exc.__class__.__name__})
                job["results"].append(result_row)
            finally:
                job["completed"] += 1
        job["status"] = "completed"


async def _bulk_approve_drafts(payload: BulkApprove, db: Session, *, dispatch: bool) -> dict:
    if dispatch and get_bool(db, "dry_run"):
        raise _dry_run_direct_send_error()
    selected = len(payload.draft_ids)
    approved = 0
    queued = 0
    blocked = 0
    skipped = 0
    queue_ids: list[str] = []
    items: list[dict] = []
    seen: set[str] = set()
    for draft_id in payload.draft_ids:
        if draft_id in seen:
            skipped += 1
            items.append({"draft_id": draft_id, "status": "skipped", "reason": "duplicate_selection"})
            continue
        seen.add(draft_id)
        draft = db.get(Draft, draft_id)
        if not draft:
            skipped += 1
            items.append({"draft_id": draft_id, "status": "skipped", "reason": "draft_not_found"})
            continue
        contact = db.get(Contact, draft.contact_id)
        if not contact or contact.deleted_at is not None:
            skipped += 1
            items.append({"draft_id": draft_id, "status": "skipped", "reason": "contact_not_found"})
            continue
        content_blocks = draft_content_block_reasons(draft, contact)
        if content_blocks:
            blocked += 1
            items.append({"draft_id": draft_id, "status": "blocked", "reason": content_blocks[0]})
            continue
        campaign_block = draft_campaign_block_reason(db, draft)
        if campaign_block:
            blocked += 1
            items.append({"draft_id": draft_id, "status": "blocked", "reason": campaign_block})
            continue
        try:
            if not draft.approved:
                draft.approved = True
                draft.approved_at = utcnow()
                approved += 1
            contact.status = "approved"
            queue = _queue_approved_draft(db, draft, contact, 1, immediate=dispatch)
            emit_event(
                db,
                "draft.approved",
                entity_type="draft",
                entity_id=draft.id,
                payload={"queue_id": queue.id, "bulk": True, "dispatch_requested": dispatch},
            )
            queued += 1
            queue_ids.append(queue.id)
            items.append({"draft_id": draft_id, "status": "queued", "queue_id": queue.id})
        except HTTPException as exc:
            blocked += 1
            items.append({"draft_id": draft_id, "status": "blocked", "reason": exc.detail})
    db.commit()
    result = {
        "processed": 0,
        "eligible_count": 0,
        "provider_accepted": 0,
        "sent": 0,
        "blocked": 0,
        "simulated": 0,
        "skipped": 0,
        "failed": 0,
        "reconciliation_required": 0,
        "deferred": 0,
        "policy_rescheduled": 0,
        "future_scheduled_count": 0,
        "next_due_at": None,
        "blocked_reasons": {},
        "scheduler_effective": auto_process_enabled(db),
        "zero_work_reason": "approve_only" if not dispatch else "no_selected_rows",
    }
    if dispatch and queue_ids:
        result = await process_pending_queue(db, queue_ids=queue_ids)
    return {
        "selected": selected,
        "approved": approved,
        "queued": queued,
        "blocked": blocked,
        "skipped": skipped,
        "dispatch_requested": dispatch,
        "scheduler_effective": auto_process_enabled(db),
        "items": items,
        **result,
    }


@router.post("/approve-bulk")
async def approve_bulk(payload: BulkApprove, db: Session = Depends(get_db)):
    return await _bulk_approve_drafts(payload, db, dispatch=False)


@router.post("/approve-bulk-and-send")
async def approve_bulk_and_send(payload: BulkApprove, db: Session = Depends(get_db)):
    return await _bulk_approve_drafts(payload, db, dispatch=True)


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
async def approve_draft(draft_id: str, background_tasks: BackgroundTasks, payload: DraftApprove | None = None, db: Session = Depends(get_db)):
    draft = db.get(Draft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="draft not found")
    contact = db.get(Contact, draft.contact_id)
    if not contact or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="contact not found")
    content_blocks = draft_content_block_reasons(draft, contact)
    if content_blocks:
        raise HTTPException(status_code=422, detail={"blocked": content_blocks})
    campaign_block = draft_campaign_block_reason(db, draft)
    if campaign_block:
        raise HTTPException(status_code=409, detail=campaign_block)
    sequence_num = payload.sequence_num if payload and payload.sequence_num else 1
    if sequence_num < 1:
        raise HTTPException(status_code=422, detail="sequence_num must be >= 1")
    if not sequence_prerequisite_met(db, contact.id, sequence_num):
        raise HTTPException(
            status_code=409,
            detail={"reason": "prior_sequence_not_provider_accepted", "sequence_num": sequence_num},
        )
    _validate_queue_approval(db, draft, contact)
    if get_bool(db, "dry_run"):
        raise _dry_run_direct_send_error()
    draft.approved = True
    draft.approved_at = utcnow()
    contact.status = "approved"
    queue = _queue_approved_draft(db, draft, contact, sequence_num, immediate=True)
    emit_event(db, "draft.approved", entity_type="draft", entity_id=draft.id, payload={"queue_id": queue.id, "sequence_num": sequence_num})
    db.commit()
    result = await process_pending_queue(db, queue_ids=[queue.id])
    db.expire_all()
    queue = db.get(SendQueue, queue.id)
    draft = db.get(Draft, draft_id)
    return {
        **draft_to_dict(draft),
        "queue_id": queue.id if queue else None,
        "queue": queue_to_dict(queue, db) if queue else None,
        "delivery_status": _delivery_status(result, queue),
        "delivery_result": result,
    }
