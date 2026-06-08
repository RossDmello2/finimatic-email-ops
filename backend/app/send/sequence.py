from __future__ import annotations

import json
import re
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import emit_event
from app.db.models import CampaignPlan, Draft, FollowUpSequence, SendAttempt, SendQueue
from app.send.outcomes import valid_gmail_message_id

CAMPAIGN_NOTE_RE = re.compile(r"^campaign:([^:]+):step(\d+)$")
CAMPAIGN_STEP_DEFAULT_DELAYS = {1: 0, 2: 3, 3: 3}


def provider_acceptance_evidence_present(attempt: SendAttempt | None) -> bool:
    if attempt is None or attempt.provider_accepted is not True:
        return False
    if attempt.simulated is not False or attempt.provider_contacted is not True:
        return False
    if attempt.effective_transport == "gmail_api":
        return valid_gmail_message_id(attempt.provider_msg_id)
    if attempt.effective_transport == "smtp":
        return attempt.provider_response_classification == "smtp_transaction_completed"
    if attempt.effective_transport == "test_provider":
        return (
            attempt.provider_response_classification == "test_provider_accepted"
            and bool((attempt.provider_msg_id or "").strip())
        )
    return False


def campaign_step_definition(campaign: CampaignPlan, sequence_num: int) -> dict[str, object]:
    raw = getattr(campaign, f"step_{sequence_num}_draft", None)
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    delay = value.get("delay_days", CAMPAIGN_STEP_DEFAULT_DELAYS.get(sequence_num, 0))
    if isinstance(delay, bool):
        delay = -1
    try:
        delay_days = int(delay)
    except (TypeError, ValueError):
        delay_days = -1
    return {
        "subject": str(value.get("subject") or ""),
        "body": str(value.get("body") or ""),
        "purpose": str(value.get("purpose") or ""),
        "delay_days": delay_days,
    }


def campaign_step_error(campaign: CampaignPlan, sequence_num: int) -> str | None:
    step = campaign_step_definition(campaign, sequence_num)
    if not step["subject"].strip() or not step["body"].strip():
        return f"campaign_step_{sequence_num}_incomplete"
    if int(step["delay_days"]) < 0:
        return f"campaign_step_{sequence_num}_delay_invalid"
    if sequence_num == 1 and int(step["delay_days"]) != 0:
        return "campaign_step_1_delay_must_be_zero"
    return None


def campaign_context_for_draft(
    db: Session,
    draft_id: str | None,
) -> tuple[CampaignPlan, int] | None:
    draft = db.get(Draft, draft_id) if draft_id else None
    match = CAMPAIGN_NOTE_RE.match((draft.notes or "").strip()) if draft else None
    if not match:
        return None
    campaign = db.get(CampaignPlan, match.group(1))
    if campaign is None:
        return None
    return campaign, int(match.group(2))


def followup_belongs_to_campaign(db: Session, row: FollowUpSequence, campaign_id: str) -> bool:
    for draft_id in (row.pending_draft_id, row.draft_id):
        context = campaign_context_for_draft(db, draft_id)
        if context and context[0].id == campaign_id:
            return True
    return False


def schedule_next_sequence_after_acceptance(
    db: Session,
    queue: SendQueue,
    attempt: SendAttempt,
) -> tuple[bool, FollowUpSequence | None]:
    """Schedule campaign work atomically with a durable provider acceptance projection."""
    context = campaign_context_for_draft(db, queue.draft_id)
    if context is None:
        return False, None
    campaign, current_step = context
    if current_step != queue.sequence_num:
        raise ValueError("campaign_sequence_mismatch")
    persisted_attempt = db.get(SendAttempt, attempt.id)
    if (
        persisted_attempt is None
        or persisted_attempt.queue_id != queue.id
        or persisted_attempt.contact_id != queue.contact_id
        or persisted_attempt.draft_id != queue.draft_id
        or queue.status != "provider_accepted"
        or not provider_acceptance_evidence_present(persisted_attempt)
        or persisted_attempt.sent_at is None
    ):
        raise ValueError("durable_provider_acceptance_required")

    next_sequence = current_step + 1
    if campaign.status != "active":
        emit_event(
            db,
            "campaign.sequence_blocked",
            entity_type="campaign_plan",
            entity_id=campaign.id,
            payload={
                "contact_id": queue.contact_id,
                "sequence_num": next_sequence,
                "reason": "CAMPAIGN_NOT_ACTIVE",
            },
        )
        return True, None
    if next_sequence > 3:
        return True, None
    error = campaign_step_error(campaign, next_sequence)
    if error:
        emit_event(
            db,
            "campaign.sequence_blocked",
            entity_type="campaign_plan",
            entity_id=campaign.id,
            payload={"contact_id": queue.contact_id, "sequence_num": next_sequence, "reason": error},
        )
        return True, None

    existing = (
        db.query(FollowUpSequence)
        .filter_by(contact_id=queue.contact_id, sequence_num=next_sequence)
        .first()
    )
    if existing is not None:
        return True, existing

    step = campaign_step_definition(campaign, next_sequence)
    row = FollowUpSequence(
        contact_id=queue.contact_id,
        draft_id=queue.draft_id,
        sequence_num=next_sequence,
        due_at=persisted_attempt.sent_at + timedelta(days=int(step["delay_days"])),
        status="due",
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = (
            db.query(FollowUpSequence)
            .filter_by(contact_id=queue.contact_id, sequence_num=next_sequence)
            .first()
        )
        if existing is None:
            raise
        return True, existing
    emit_event(
        db,
        "campaign.sequence_scheduled",
        entity_type="follow_up_sequence",
        entity_id=row.id,
        payload={
            "campaign_id": campaign.id,
            "contact_id": queue.contact_id,
            "accepted_attempt_id": persisted_attempt.id,
            "sequence_num": next_sequence,
            "delay_days": step["delay_days"],
        },
    )
    return True, row


def prior_sequence_acceptance(db: Session, contact_id: str, sequence_num: int) -> SendAttempt | None:
    if sequence_num <= 1:
        return None
    previous = db.query(SendQueue).filter_by(contact_id=contact_id, sequence_num=sequence_num - 1).first()
    if previous is None or previous.status != "provider_accepted":
        return None
    attempts = (
        db.query(SendAttempt)
        .filter(
            SendAttempt.queue_id == previous.id,
            SendAttempt.status == "provider_accepted",
            SendAttempt.provider_accepted.is_(True),
        )
        .order_by(SendAttempt.created_at.desc(), SendAttempt.id.desc())
        .all()
    )
    for attempt in attempts:
        if provider_acceptance_evidence_present(attempt):
            return attempt
    return None


def sequence_prerequisite_met(db: Session, contact_id: str, sequence_num: int) -> bool:
    return sequence_num <= 1 or prior_sequence_acceptance(db, contact_id, sequence_num) is not None
