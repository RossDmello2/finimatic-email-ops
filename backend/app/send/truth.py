from __future__ import annotations

from app.core.time import utcnow
from app.db.models import SendAttempt
from app.send.outcomes import SendOutcome


def attempt_from_outcome(
    outcome: SendOutcome,
    *,
    queue_id: str,
    contact_id: str,
    draft_id: str,
    idempotency_key: str | None,
    sender_identity: str,
    sent_at=None,
) -> SendAttempt:
    return SendAttempt(
        queue_id=queue_id,
        contact_id=contact_id,
        draft_id=draft_id,
        idempotency_key=idempotency_key,
        provider_msg_id=outcome.provider_message_id,
        tracking_message_id=outcome.tracking_message_id,
        smtp_response=outcome.provider_response_classification,
        configured_transport=outcome.configured_transport,
        effective_transport=outcome.effective_transport,
        transport_source=outcome.transport_source,
        simulated=outcome.simulated,
        provider_contacted=outcome.provider_contacted,
        provider_accepted=outcome.provider_accepted,
        provider_response_classification=outcome.provider_response_classification,
        status=outcome.attempt_status,
        sender_identity=sender_identity,
        sent_at=sent_at,
        error_code=outcome.error_code,
        error_detail=outcome.error_detail_redacted,
        created_at=utcnow(),
    )


def outcome_audit_payload(outcome: SendOutcome) -> dict:
    return {
        "attempt_status": outcome.attempt_status,
        "configured_transport": outcome.configured_transport,
        "effective_transport": outcome.effective_transport,
        "transport_source": outcome.transport_source,
        "simulated": outcome.simulated,
        "provider_contacted": outcome.provider_contacted,
        "provider_accepted": outcome.provider_accepted,
        "provider_response_classification": outcome.provider_response_classification,
        "error_code": outcome.error_code,
        "idempotency_key": outcome.idempotency_key,
        "attempt_id": outcome.attempt_id,
    }


def apply_outcome_to_attempt(attempt: SendAttempt, outcome: SendOutcome, *, sent_at=None) -> None:
    attempt.provider_msg_id = outcome.provider_message_id
    attempt.tracking_message_id = outcome.tracking_message_id
    attempt.smtp_response = outcome.provider_response_classification
    attempt.configured_transport = outcome.configured_transport
    attempt.effective_transport = outcome.effective_transport
    attempt.transport_source = outcome.transport_source
    attempt.simulated = outcome.simulated
    attempt.provider_contacted = outcome.provider_contacted
    attempt.provider_accepted = outcome.provider_accepted
    attempt.provider_response_classification = outcome.provider_response_classification
    attempt.status = outcome.attempt_status
    attempt.sent_at = sent_at
    attempt.error_code = outcome.error_code
    attempt.error_detail = outcome.error_detail_redacted
