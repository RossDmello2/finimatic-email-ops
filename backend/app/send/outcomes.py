from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Literal


AttemptStatus = Literal[
    "blocked",
    "simulated",
    "provider_accepted",
    "failed",
    "reconciliation_required",
]


@dataclass(frozen=True)
class SendOutcome:
    attempt_status: AttemptStatus
    configured_transport: str
    effective_transport: str
    transport_source: str
    simulated: bool
    provider_contacted: bool
    provider_accepted: bool
    provider_message_id: str | None = None
    tracking_message_id: str | None = None
    provider_response_classification: str | None = None
    error_code: str | None = None
    error_detail_redacted: str | None = None
    idempotency_key: str | None = None
    attempt_id: str | None = None

    def with_context(self, *, idempotency_key: str | None = None, attempt_id: str | None = None) -> "SendOutcome":
        return replace(
            self,
            idempotency_key=idempotency_key if idempotency_key is not None else self.idempotency_key,
            attempt_id=attempt_id if attempt_id is not None else self.attempt_id,
        )

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["status"] = self.attempt_status
        return payload


@dataclass(frozen=True)
class TransportResolution:
    configured_transport: str
    effective_transport: str
    transport_source: str
    simulated: bool

    def to_dict(self) -> dict:
        return asdict(self)


_LOCAL_PROVIDER_ID_PREFIXES = ("fake-", "attempt:", "local-", "tracking:", "smtp-")


def valid_gmail_message_id(value: str | None) -> bool:
    candidate = (value or "").strip()
    if not candidate or candidate.lower().startswith(_LOCAL_PROVIDER_ID_PREFIXES):
        return False
    return bool(re.fullmatch(r"[0-9a-fA-F]{12,32}", candidate))


def validate_send_outcome(
    outcome: SendOutcome,
    resolution: TransportResolution,
    *,
    trusted_provider_adapter: bool,
) -> SendOutcome:
    """Bind adapter output to the resolved transport and reject unsupported success claims."""
    outcome = replace(
        outcome,
        configured_transport=resolution.configured_transport,
        effective_transport=resolution.effective_transport,
        transport_source=resolution.transport_source,
    )
    if resolution.simulated:
        return simulated_outcome(
            resolution,
            error_code="effective_transport_simulated",
            detail="Effective transport is simulated; live provider execution is blocked",
            blocked=True,
        ).with_context(idempotency_key=outcome.idempotency_key, attempt_id=outcome.attempt_id)

    invalid_reason = None
    if outcome.provider_accepted:
        if outcome.attempt_status != "provider_accepted":
            invalid_reason = "accepted_status_mismatch"
        elif outcome.simulated or not outcome.provider_contacted:
            invalid_reason = "accepted_without_provider_contact"
        elif not trusted_provider_adapter:
            invalid_reason = "untrusted_provider_adapter"
        elif resolution.effective_transport == "gmail_api" and not valid_gmail_message_id(outcome.provider_message_id):
            invalid_reason = "gmail_api_missing_native_message_id"
        elif (
            resolution.effective_transport == "smtp"
            and outcome.provider_response_classification != "smtp_transaction_completed"
        ):
            invalid_reason = "smtp_acceptance_unverified"
        elif (
            resolution.effective_transport not in {"gmail_api", "smtp"}
            and not (outcome.provider_message_id or "").strip()
        ):
            invalid_reason = "provider_message_id_missing"
    elif outcome.attempt_status == "provider_accepted":
        invalid_reason = "provider_accepted_status_without_acceptance"

    if invalid_reason is None:
        return outcome

    return replace(
        outcome,
        attempt_status="reconciliation_required" if outcome.provider_contacted else "failed",
        provider_accepted=False,
        provider_message_id=None,
        provider_response_classification="provider_acceptance_evidence_invalid",
        error_code=invalid_reason,
        error_detail_redacted="Provider acceptance evidence failed the transport-specific truth contract",
    )


def simulated_outcome(
    resolution: TransportResolution,
    *,
    error_code: str,
    detail: str,
    blocked: bool = False,
) -> SendOutcome:
    return SendOutcome(
        attempt_status="blocked" if blocked else "simulated",
        configured_transport=resolution.configured_transport,
        effective_transport=resolution.effective_transport,
        transport_source=resolution.transport_source,
        simulated=True,
        provider_contacted=False,
        provider_accepted=False,
        provider_response_classification="not_contacted",
        error_code=error_code,
        error_detail_redacted=detail,
    )
