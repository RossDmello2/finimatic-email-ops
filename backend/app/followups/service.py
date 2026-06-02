from __future__ import annotations

import json
import re
from datetime import timedelta

from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.ai.gateway import GROQ_MODEL_DEFAULT
from app.ai.prompts import custom_field_context, sender_profile_from_settings
from app.contacts.utils import is_domain_blocked, resolve_tokens
from app.core.time import utcnow
from app.db.models import Contact, Draft, FollowUpSequence, Reply, Suppression
from app.send.queue_worker import create_queue_entry
from app.settings.service import get_int, get_key_list, get_value

FOLLOWUP_PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]")
FOLLOWUP_BANNED_REPLACEMENTS = {
    "I hope you're doing well": "",
    "I hope you are doing well": "",
    "Hope you're doing well": "",
    "Hope you are doing well": "",
    "I wanted to follow up": "This is a short note after my previous email",
    "Just checking in": "A quick practical note",
    "leverage": "use",
    "synergy": "fit",
    "cutting-edge": "practical",
    "innovative solution": "system",
    "reach out": "talk",
    "circle back": "continue",
    "touch base": "continue",
    "paradigm": "approach",
    "value-add": "useful note",
}


def followup_to_dict(row: FollowUpSequence, db: Session | None = None) -> dict:
    contact = db.get(Contact, row.contact_id) if db is not None else None
    pending_draft = db.get(Draft, row.pending_draft_id) if db is not None and row.pending_draft_id else None
    return {
        "id": row.id,
        "contact_id": row.contact_id,
        "contact_email": contact.email if contact else None,
        "contact_name": (contact.creator_name or contact.business_name) if contact else None,
        "sequence_num": row.sequence_num,
        "due_at": row.due_at.isoformat() if row.due_at else None,
        "draft_id": row.draft_id,
        "pending_draft_id": row.pending_draft_id,
        "pending_draft": {
            "id": pending_draft.id,
            "subject": pending_draft.subject,
            "body": pending_draft.body,
            "approved": pending_draft.approved,
        } if pending_draft else None,
        "status": row.status,
        "stop_reason": row.stop_reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def stop_reason(db: Session, contact: Contact) -> str | None:
    if contact.status == "replied":
        return "RECIPIENT_REPLIED"
    if contact.status == "unsubscribed":
        return "RECIPIENT_UNSUBSCRIBED"
    if contact.status == "suppressed":
        return "RECIPIENT_SUPPRESSED"
    if contact.status == "bounced":
        return "RECIPIENT_BOUNCED"
    if contact.status == "manually_paused":
        return "RECIPIENT_MANUALLY_PAUSED"
    if db.query(Suppression).filter_by(email=contact.email).first():
        return "RECIPIENT_SUPPRESSED"
    reply = (
        db.query(Reply)
        .filter(
            Reply.contact_id == contact.id,
            Reply.archived_at.is_(None),
            Reply.classified_as.in_(["reply", "unsubscribe", "bounce", "complaint"]),
        )
        .first()
    )
    if reply:
        if reply.classified_as == "reply":
            return "RECIPIENT_REPLIED"
        if reply.classified_as == "bounce":
            return "RECIPIENT_BOUNCED"
        return "RECIPIENT_STOPPED"
    return None


def process_due_followups(db: Session) -> dict:
    due = db.query(FollowUpSequence).filter(FollowUpSequence.status == "due", FollowUpSequence.due_at <= utcnow()).all()
    stopped = 0
    dispatched = 0
    skipped = 0
    for row in due:
        contact = db.get(Contact, row.contact_id)
        reason = stop_reason(db, contact)
        if reason:
            row.status = "stopped"
            row.stop_reason = reason
            contact.status = "follow_up_stopped"
            emit_event(db, "followup.stopped", entity_type="follow_up_sequence", entity_id=row.id, payload={"reason": reason})
            stopped += 1
        else:
            draft = _make_followup_draft(db, contact, row.sequence_num)
            row.draft_id = draft.id
            row.pending_draft_id = draft.id
            row.status = "pending_approval"
            emit_event(
                db,
                "followup.draft_proposed",
                entity_type="follow_up_sequence",
                entity_id=row.id,
                payload={"contact_id": contact.id, "sequence_num": row.sequence_num, "draft_id": draft.id},
            )
            skipped += 1
    db.commit()
    return {"processed": len(due), "stopped": stopped, "dispatched": dispatched, "skipped": skipped}


def _template_for_sequence(db: Session, sequence_num: int) -> str:
    template_key = "follow_up_template_1" if sequence_num <= 2 else "follow_up_template_2"
    return get_value(db, template_key)


def _make_followup_draft(db: Session, contact: Contact, sequence_num: int) -> Draft:
    template = _template_for_sequence(db, sequence_num)
    subject = f"Following up with {contact.creator_name or contact.business_name or 'you'}"
    body = template
    keys = get_key_list(db, "groq_keys")
    if keys:
        try:
            from groq import Groq

            client = Groq(api_key=keys[0])
            response = client.chat.completions.create(
                model=get_value(db, "groq_model", GROQ_MODEL_DEFAULT),
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Use this follow-up template as the base prompt and return only valid JSON "
                            "{\"subject\":\"...\",\"body\":\"...\",\"warnings\":[]}.\n"
                            f"Template: {template}\n"
                            f"Recipient: {contact.creator_name or contact.business_name or contact.email}\n"
                            f"Website: {contact.website_url or 'not provided'}\n"
                            f"Notes: {contact.personalization or contact.notes or contact.lead_category or 'unknown'}\n"
                            f"Imported tags/custom fields: {custom_field_context(contact)}"
                        ),
                    }
                ],
                response_format={"type": "json_object"},
                timeout=30,
            )
            parsed = json.loads(response.choices[0].message.content or "{}")
            subject = str(parsed.get("subject") or subject)
            body = str(parsed.get("body") or body)
        except Exception:
            subject = f"Following up with {contact.creator_name or contact.business_name or 'you'}"
            body = template
    subject, body = _sanitize_followup_copy(db, contact, sequence_num, subject, body)
    draft = Draft(
        contact_id=contact.id,
        subject=resolve_tokens(subject, contact),
        body=resolve_tokens(body, contact),
        ai_provider="groq" if keys else "manual",
        ai_model=get_value(db, "groq_model", GROQ_MODEL_DEFAULT) if keys else None,
        warnings=json.dumps([]),
        notes=f"followup_auto:seq{sequence_num}",
        approved=False,
        approved_at=None,
    )
    db.add(draft)
    db.flush()
    return draft


def _sanitize_followup_copy(db: Session, contact: Contact, sequence_num: int, subject: str, body: str) -> tuple[str, str]:
    original = f"{subject}\n{body}"
    for phrase, replacement in FOLLOWUP_BANNED_REPLACEMENTS.items():
        subject = re.sub(re.escape(phrase), replacement, subject, flags=re.IGNORECASE)
        body = re.sub(re.escape(phrase), replacement, body, flags=re.IGNORECASE)
    subject = FOLLOWUP_PLACEHOLDER_RE.sub("", subject).strip(" -:") or _fallback_followup_subject(contact, sequence_num)
    body = FOLLOWUP_PLACEHOLDER_RE.sub("", body).strip()
    if FOLLOWUP_PLACEHOLDER_RE.search(original) or not body:
        body = _fallback_followup_body(db, contact, sequence_num)
    else:
        body = _ensure_followup_signature(db, body)
    return subject[:120], body


def _fallback_followup_subject(contact: Contact, sequence_num: int) -> str:
    name = contact.creator_name or contact.business_name or "you"
    if sequence_num <= 2:
        return f"Quick note for {name}"
    return f"Closing the loop for {name}"


def _configured_offer_sentence(db: Session) -> str:
    profile = sender_profile_from_settings(db)
    offer = (profile.sender_offer or get_value(db, "campaign_context") or "").strip()
    if not offer:
        return "the idea from my earlier email may be useful if it maps to your current priorities."
    return offer.rstrip(".!?") + "."


def _fallback_followup_body(db: Session, contact: Contact, sequence_num: int) -> str:
    profile = sender_profile_from_settings(db)
    signature = profile.sender_signature
    offer_sentence = _configured_offer_sentence(db)
    name = contact.creator_name or contact.business_name or "there"
    if sequence_num <= 2:
        body = (
            f"{name}, a quick practical note after my earlier email: {offer_sentence} "
            "Would you be open to sharing two suitable times for a short conversation?"
        )
    else:
        body = (
            f"{name}, I will close the loop here in case now is not the right time. If this becomes relevant later, "
            "would you like me to send a concise pilot outline?"
        )
    return f"{body}\n\n{signature}"


def _ensure_followup_signature(db: Session, body: str) -> str:
    profile = sender_profile_from_settings(db)
    signature = profile.sender_signature.strip()
    if signature in body:
        return body
    body = re.sub(r"\n*Best regards\.?\s*$", "", body, flags=re.IGNORECASE).strip()
    return f"{body}\n\n{signature}"


def _schedule_next_followup(db: Session, contact_id: str, draft_id: str, current_sequence: int) -> None:
    max_followups = get_int(db, "max_followups_per_lead")
    followup_index = current_sequence - 1
    if followup_index >= max_followups:
        return
    next_sequence = current_sequence + 1
    existing = db.query(FollowUpSequence).filter_by(contact_id=contact_id, sequence_num=next_sequence).first()
    if existing:
        return
    row = FollowUpSequence(
        contact_id=contact_id,
        draft_id=draft_id,
        sequence_num=next_sequence,
        due_at=utcnow() + timedelta(days=get_int(db, "followup_interval_days")),
        status="due",
    )
    db.add(row)
    emit_event(db, "followup.due_calculated", entity_type="contact", entity_id=contact_id, payload={"sequence_num": next_sequence})


def approve_followup_draft(db: Session, sequence_id: str) -> dict:
    row = db.get(FollowUpSequence, sequence_id)
    if not row:
        raise ValueError("followup_not_found")
    if row.status == "stopped":
        raise ValueError(row.stop_reason or "followup_stopped")
    if not row.pending_draft_id:
        raise ValueError("pending_draft_missing")
    draft = db.get(Draft, row.pending_draft_id)
    if not draft:
        raise ValueError("draft_not_found")
    if draft.approved:
        raise ValueError("draft_already_approved")
    contact = db.get(Contact, row.contact_id)
    block_reasons = _followup_approval_block_reasons(db, contact)
    if block_reasons:
        row.status = "stopped"
        row.stop_reason = block_reasons[0]
        emit_event(
            db,
            "followup.approval_blocked",
            entity_type="follow_up_sequence",
            entity_id=row.id,
            payload={"contact_id": row.contact_id, "reasons": block_reasons},
        )
        db.commit()
        raise ValueError(block_reasons[0])
    draft.approved = True
    draft.approved_at = utcnow()
    queue = create_queue_entry(db, row.contact_id, draft.id, row.sequence_num)
    row.status = "dispatched"
    row.draft_id = draft.id
    emit_event(
        db,
        "followup.approved_and_queued",
        entity_type="follow_up_sequence",
        entity_id=row.id,
        payload={"contact_id": row.contact_id, "sequence_num": row.sequence_num, "draft_id": draft.id, "queue_id": queue.id},
    )
    _schedule_next_followup(db, row.contact_id, draft.id, row.sequence_num)
    db.commit()
    return {"status": "queued", "queue_id": queue.id}


def _followup_approval_block_reasons(db: Session, contact: Contact | None) -> list[str]:
    if not contact:
        return ["CONTACT_NOT_FOUND"]
    reasons: list[str] = []
    if contact.deleted_at is not None:
        reasons.append("CONTACT_DELETED")
    if db.query(Suppression).filter(Suppression.email == contact.email).first() or is_domain_blocked(db, contact.email):
        reasons.append("RECIPIENT_SUPPRESSED")
    if contact.status == "unsubscribed":
        reasons.append("RECIPIENT_UNSUBSCRIBED")
    if contact.status == "bounced":
        reasons.append("RECIPIENT_BOUNCED")
    if contact.status == "manually_paused":
        reasons.append("RECIPIENT_MANUALLY_PAUSED")
    if db.query(Reply).filter(Reply.contact_id == contact.id, Reply.archived_at.is_(None), Reply.classified_as.in_(["reply", "unsubscribe", "bounce"])).first():
        reasons.append("RECIPIENT_REPLIED")
    return reasons
