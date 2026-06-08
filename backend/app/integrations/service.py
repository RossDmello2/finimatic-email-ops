from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import emit_event, redact_payload
from app.core.idempotency import sha256_key
from app.core.time import utcnow
from app.db.models import (
    Contact,
    ExternalWriteAttempt,
    ExternalWritePreview,
    IntegrationConnection,
    IntegrationMapping,
    SyncJournal,
)
from app.verification.service import get_verification_for_contact

PROVIDER_ORDER = ("google_sheets", "hubspot", "salesforce")
SUPPORTED_PROVIDERS = set(PROVIDER_ORDER)
INTEGRATION_LEASE_SECONDS = 30


@dataclass(frozen=True)
class ProviderAdapter:
    provider: str
    label: str
    object_type: str
    external_fields: frozenset[str]
    default_mappings: dict[str, str]


@dataclass(frozen=True)
class IntegrationExecutionResult:
    status: str
    response_code: str
    simulated: bool
    provider_contacted: bool
    provider_accepted: bool
    external_id: str | None = None
    error_code: str | None = None
    detail: str | None = None


ADAPTERS: dict[str, ProviderAdapter] = {
    "google_sheets": ProviderAdapter(
        provider="google_sheets",
        label="Google Sheets",
        object_type="sheet_row",
        external_fields=frozenset({"Email", "Name", "Company", "Website", "Lead Status", "Verification Status"}),
        default_mappings={
            "email": "Email",
            "name": "Name",
            "company": "Company",
            "website": "Website",
            "lead_status": "Lead Status",
            "verification_status": "Verification Status",
        },
    ),
    "hubspot": ProviderAdapter(
        provider="hubspot",
        label="HubSpot",
        object_type="contact",
        external_fields=frozenset({"email", "firstname", "company", "website", "lifecyclestage", "finimatic_verification_status"}),
        default_mappings={
            "email": "email",
            "name": "firstname",
            "company": "company",
            "website": "website",
            "lead_status": "lifecyclestage",
            "verification_status": "finimatic_verification_status",
        },
    ),
    "salesforce": ProviderAdapter(
        provider="salesforce",
        label="Salesforce",
        object_type="lead",
        external_fields=frozenset({"Email", "FirstName", "Company", "Website", "Status", "Finimatic_Verification_Status__c"}),
        default_mappings={
            "email": "Email",
            "name": "FirstName",
            "company": "Company",
            "website": "Website",
            "lead_status": "Status",
            "verification_status": "Finimatic_Verification_Status__c",
        },
    ),
}

LOCAL_FIELDS = frozenset({"email", "name", "company", "website", "lead_status", "verification_status"})


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _json(value: Any) -> str:
    return json.dumps(redact_payload(value), sort_keys=True)


def _provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ValueError("unsupported_provider")
    return normalized


def _adapter(provider: str) -> ProviderAdapter:
    return ADAPTERS[_provider(provider)]


def ensure_connections(db: Session) -> list[IntegrationConnection]:
    rows = []
    for provider in PROVIDER_ORDER:
        row = (
            db.query(IntegrationConnection)
            .filter(IntegrationConnection.provider == provider, IntegrationConnection.account_label == "default")
            .first()
        )
        if not row:
            row = IntegrationConnection(
                provider=provider,
                account_label="default",
                status="dry_run_ready",
                auth_mode="local_dry_run",
                scopes_redacted="No OAuth credential stored. Confirm records a local dry-run journal only.",
                last_checked_at=utcnow(),
            )
            db.add(row)
            db.flush()
        elif row.status == "not_connected" and row.auth_mode == "manual":
            row.status = "dry_run_ready"
            row.auth_mode = "local_dry_run"
            row.scopes_redacted = row.scopes_redacted or "No OAuth credential stored. Confirm records a local dry-run journal only."
            row.last_checked_at = row.last_checked_at or utcnow()
        elif not row.scopes_redacted:
            row.scopes_redacted = "Credential details are stored backend-side only and are not returned by this API."
        rows.append(row)
    return rows


def ensure_mappings(db: Session, connections: list[IntegrationConnection] | None = None) -> list[IntegrationMapping]:
    rows: list[IntegrationMapping] = []
    for connection in connections or ensure_connections(db):
        adapter = _adapter(connection.provider)
        for local_field, external_field in adapter.default_mappings.items():
            row = (
                db.query(IntegrationMapping)
                .filter(
                    IntegrationMapping.connection_id == connection.id,
                    IntegrationMapping.local_field == local_field,
                    IntegrationMapping.direction == "push",
                )
                .first()
            )
            if not row:
                row = IntegrationMapping(
                    connection_id=connection.id,
                    local_field=local_field,
                    external_field=external_field,
                    direction="push",
                    transform_json=None,
                )
                db.add(row)
                db.flush()
            rows.append(row)
    return rows


def connection_to_dict(row: IntegrationConnection) -> dict:
    return {
        "id": row.id,
        "provider": row.provider,
        "account_label": row.account_label,
        "status": row.status,
        "auth_mode": row.auth_mode,
        "scopes_redacted": redact_payload(row.scopes_redacted),
        "last_checked_at": _iso(row.last_checked_at),
    }


def mapping_to_dict(row: IntegrationMapping, provider: str) -> dict:
    adapter = _adapter(provider)
    valid = row.local_field in LOCAL_FIELDS and row.external_field in adapter.external_fields and row.direction == "push"
    return {
        "id": row.id,
        "provider": provider,
        "connection_id": row.connection_id,
        "local_field": row.local_field,
        "external_field": row.external_field,
        "direction": row.direction,
        "status": "valid" if valid else "mapping_mismatch",
        "created_at": _iso(row.created_at),
    }


def preview_to_dict(row: ExternalWritePreview) -> dict:
    return {
        "id": row.id,
        "provider": row.provider,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "action": row.action,
        "diff": json.loads(row.diff_json_redacted or "{}"),
        "idempotency_key": row.idempotency_key,
        "status": row.status,
        "execution_hash": row.execution_hash,
        "lease_owner": row.lease_owner,
        "lease_expires_at": _iso(row.lease_expires_at),
        "heartbeat_at": _iso(row.heartbeat_at),
        "lease_generation": row.lease_generation,
        "expires_at": _iso(row.expires_at),
        "created_at": _iso(row.created_at),
    }


def journal_to_dict(row: SyncJournal) -> dict:
    return {
        "id": row.id,
        "provider": row.provider,
        "connection_id": row.connection_id,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "external_id": row.external_id,
        "action": row.action,
        "status": row.status,
        "idempotency_key": row.idempotency_key,
        "diff": json.loads(row.diff_json_redacted or "{}") if row.diff_json_redacted else {},
        "created_at": _iso(row.created_at),
    }


def attempt_to_dict(row: ExternalWriteAttempt) -> dict:
    return {
        "id": row.id,
        "preview_id": row.preview_id,
        "provider": row.provider,
        "status": row.status,
        "response_code": row.response_code,
        "external_id": row.external_id,
        "idempotency_key": row.idempotency_key,
        "execution_hash": row.execution_hash,
        "error_code": row.error_code,
        "details": json.loads(row.details_redacted or "{}") if row.details_redacted else {},
        "created_at": _iso(row.created_at),
    }


def integration_summary(db: Session, *, ensure_defaults: bool = True) -> dict:
    connections = (
        ensure_connections(db)
        if ensure_defaults
        else db.query(IntegrationConnection).order_by(IntegrationConnection.provider.asc()).all()
    )
    mappings = (
        ensure_mappings(db, connections)
        if ensure_defaults
        else db.query(IntegrationMapping).order_by(IntegrationMapping.local_field.asc()).all()
    )
    provider_by_connection = {row.id: row.provider for row in connections}
    previews = db.query(ExternalWritePreview).order_by(ExternalWritePreview.created_at.desc()).limit(20).all()
    journals = db.query(SyncJournal).order_by(SyncJournal.created_at.desc()).limit(20).all()
    attempts = db.query(ExternalWriteAttempt).order_by(ExternalWriteAttempt.created_at.desc()).limit(20).all()
    return {
        "connections": [connection_to_dict(row) for row in connections],
        "mappings": [mapping_to_dict(row, provider_by_connection[row.connection_id]) for row in mappings if row.connection_id in provider_by_connection],
        "previews": [preview_to_dict(row) for row in previews],
        "journals": [journal_to_dict(row) for row in journals],
        "attempts": [attempt_to_dict(row) for row in attempts],
    }


def _connection_for_provider(db: Session, provider: str) -> IntegrationConnection:
    connections = ensure_connections(db)
    for row in connections:
        if row.provider == provider:
            return row
    raise ValueError("connection_not_found")


def _contact_values(db: Session, contact: Contact) -> dict[str, str]:
    verification = get_verification_for_contact(db, contact)
    return {
        "email": contact.email,
        "name": contact.creator_name or contact.business_name or "",
        "company": contact.business_name or "",
        "website": contact.website_url or "",
        "lead_status": contact.status,
        "verification_status": verification.status if verification else "unknown",
    }


def _validated_mapping_rows(db: Session, connection: IntegrationConnection, adapter: ProviderAdapter) -> list[IntegrationMapping]:
    ensure_mappings(db, [connection])
    rows = (
        db.query(IntegrationMapping)
        .filter(IntegrationMapping.connection_id == connection.id, IntegrationMapping.direction == "push")
        .order_by(IntegrationMapping.local_field.asc())
        .all()
    )
    required = set(adapter.default_mappings)
    observed = {row.local_field for row in rows}
    if not required.issubset(observed):
        raise ValueError("mapping_mismatch")
    for row in rows:
        if row.local_field not in LOCAL_FIELDS or row.external_field not in adapter.external_fields:
            raise ValueError("mapping_mismatch")
    return rows


def _build_diff(db: Session, provider: str, contact: Contact) -> tuple[IntegrationConnection, dict[str, Any], dict[str, str]]:
    adapter = _adapter(provider)
    connection = _connection_for_provider(db, provider)
    rows = _validated_mapping_rows(db, connection, adapter)
    values = _contact_values(db, contact)
    mapped_after: dict[str, str] = {}
    fields = []
    for row in rows:
        value = values[row.local_field]
        mapped_after[row.external_field] = value
        fields.append(
            {
                "local_field": row.local_field,
                "external_field": row.external_field,
                "before": None,
                "after": value,
                "status": "create_or_update",
            }
        )
    mapping_hash = sha256_key(provider, connection.id, json.dumps([(row.local_field, row.external_field) for row in rows], sort_keys=True))
    diff = {
        "provider_label": adapter.label,
        "object_type": adapter.object_type,
        "connection": {
            "id": connection.id,
            "provider": provider,
            "account_label": connection.account_label,
            "status": connection.status,
            "auth_mode": connection.auth_mode,
        },
        "fields": fields,
        "policy": {
            "requires_preview": True,
            "requires_confirmation": True,
            "external_write": True,
            "idempotent": True,
            "dry_run_only": True,
            "external_write_attempted_by_preview": False,
            "credential_status": "not_configured_for_real_write" if connection.auth_mode == "local_dry_run" else "backend_managed",
            "mapping_version": mapping_hash[:12],
        },
    }
    return connection, diff, mapped_after


def create_contact_sync_preview(db: Session, provider: str, contact: Contact) -> ExternalWritePreview:
    provider = _provider(provider)
    connection, diff, mapped_after = _build_diff(db, provider, contact)
    content_key = sha256_key(provider, connection.id, contact.id, "upsert", json.dumps(mapped_after, sort_keys=True))
    active_claim_key = sha256_key("integration-active", provider, contact.id)
    existing = (
        db.query(ExternalWritePreview)
        .filter(
            ExternalWritePreview.provider == provider,
            ExternalWritePreview.entity_id == contact.id,
            ExternalWritePreview.status.in_({"pending_confirmation", "executing"}),
        )
        .order_by(ExternalWritePreview.created_at.desc())
        .first()
    )
    if existing and (existing.status == "executing" or not _preview_expired(existing)):
        return existing
    if existing:
        existing.status = "expired"
        existing.active_claim_key = None
        db.flush()
    key = sha256_key(content_key, uuid.uuid4().hex)
    row = ExternalWritePreview(
        provider=provider,
        entity_type="contact",
        entity_id=contact.id,
        action="upsert",
        diff_json_redacted=_json(diff),
        idempotency_key=key,
        execution_hash=content_key,
        active_claim_key=active_claim_key,
        status="pending_confirmation",
        expires_at=utcnow() + timedelta(minutes=15),
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(ExternalWritePreview)
            .filter(ExternalWritePreview.active_claim_key == active_claim_key)
            .one_or_none()
        )
        if existing:
            return existing
        raise
    emit_event(
        db,
        "integration.diff_preview_created",
        entity_type="contact",
        entity_id=contact.id,
        payload={"provider": provider, "connection_id": connection.id, "external_write_attempted": False},
    )
    return row


def _preview_expired(preview: ExternalWritePreview) -> bool:
    expires_at = preview.expires_at.replace(tzinfo=timezone.utc) if preview.expires_at and preview.expires_at.tzinfo is None else preview.expires_at
    return bool(expires_at and expires_at < utcnow())


def _connection_from_preview(db: Session, preview: ExternalWritePreview) -> IntegrationConnection:
    diff = json.loads(preview.diff_json_redacted or "{}")
    connection_id = diff.get("connection", {}).get("id")
    if connection_id:
        row = db.get(IntegrationConnection, connection_id)
        if row:
            return row
    return _connection_for_provider(db, preview.provider)


def _existing_journal(db: Session, preview: ExternalWritePreview) -> SyncJournal | None:
    return db.query(SyncJournal).filter(SyncJournal.idempotency_key == preview.idempotency_key).first()


def _integration_owner() -> str:
    return f"integration:{os.getpid()}:{threading.get_ident()}"


def _claim_preview(db: Session, preview: ExternalWritePreview) -> ExternalWritePreview:
    now = utcnow()
    legacy_cutoff = now - timedelta(seconds=INTEGRATION_LEASE_SECONDS)
    token = uuid.uuid4().hex
    claimed = (
        db.query(ExternalWritePreview)
        .filter(
            ExternalWritePreview.id == preview.id,
            or_(
                ExternalWritePreview.status == "pending_confirmation",
                and_(
                    ExternalWritePreview.status == "executing",
                    or_(
                        ExternalWritePreview.lease_expires_at <= now,
                        and_(
                            ExternalWritePreview.lease_expires_at.is_(None),
                            ExternalWritePreview.created_at <= legacy_cutoff,
                        ),
                    ),
                ),
            ),
        )
        .update(
            {
                "status": "executing",
                "lease_token": token,
                "lease_owner": _integration_owner(),
                "lease_expires_at": now + timedelta(seconds=INTEGRATION_LEASE_SECONDS),
                "heartbeat_at": now,
                "lease_generation": ExternalWritePreview.lease_generation + 1,
            },
            synchronize_session=False,
        )
    )
    if claimed != 1:
        db.rollback()
        refreshed = db.get(ExternalWritePreview, preview.id)
        if refreshed and refreshed.status in {"executed", "simulated"} and _existing_journal(db, refreshed):
            return refreshed
        if refreshed and refreshed.status == "executing":
            raise ValueError("preview_in_progress")
        raise ValueError("preview_not_pending")
    db.commit()
    db.expire_all()
    return db.get(ExternalWritePreview, preview.id)


def _renew_preview_lease(db: Session, preview: ExternalWritePreview) -> None:
    now = utcnow()
    renewed = (
        db.query(ExternalWritePreview)
        .filter(
            ExternalWritePreview.id == preview.id,
            ExternalWritePreview.status == "executing",
            ExternalWritePreview.lease_token == preview.lease_token,
        )
        .update(
            {
                "heartbeat_at": now,
                "lease_expires_at": now + timedelta(seconds=INTEGRATION_LEASE_SECONDS),
            },
            synchronize_session=False,
        )
    )
    if renewed != 1:
        raise ValueError("preview_lease_lost")


def _attempt_truth(result: IntegrationExecutionResult) -> dict[str, Any]:
    return {
        "simulated": result.simulated,
        "provider_contacted": result.provider_contacted,
        "provider_accepted": result.provider_accepted,
        "external_write_attempted": result.provider_contacted,
        "response_classification": result.response_code,
        "error_detail_redacted": result.detail,
    }


def _execute_provider_write(
    connection: IntegrationConnection,
    preview: ExternalWritePreview,
    diff: dict[str, Any],
) -> IntegrationExecutionResult:
    del preview, diff
    if connection.status == "provider_error":
        return IntegrationExecutionResult(
            status="provider_error",
            response_code="local_provider_error",
            simulated=False,
            provider_contacted=False,
            provider_accepted=False,
            error_code="provider_error",
            detail="Connection is marked provider_error; no provider request was made.",
        )
    if connection.auth_mode == "local_dry_run" or connection.status == "dry_run_ready":
        return IntegrationExecutionResult(
            status="confirmed_dry_run",
            response_code="local_dry_run",
            simulated=True,
            provider_contacted=False,
            provider_accepted=False,
            detail="Dry-run confirmation persisted locally; no provider was contacted.",
        )
    return IntegrationExecutionResult(
        status="blocked",
        response_code="provider_adapter_unavailable",
        simulated=False,
        provider_contacted=False,
        provider_accepted=False,
        error_code="provider_adapter_unavailable",
        detail="Real provider execution is not implemented for this integration.",
    )


def _attempt_for_preview(db: Session, preview: ExternalWritePreview) -> ExternalWriteAttempt:
    existing = (
        db.query(ExternalWriteAttempt)
        .filter(ExternalWriteAttempt.idempotency_key == preview.idempotency_key)
        .first()
    )
    if existing:
        details = json.loads(existing.details_redacted or "{}")
        if existing.status == "attempting" and details.get("provider_contacted"):
            existing.status = "reconciliation_required"
            existing.error_code = "ambiguous_provider_outcome"
            preview.status = "reconciliation_required"
            preview.active_claim_key = None
            preview.lease_expires_at = None
            db.commit()
            raise ValueError("integration_reconciliation_required")
        existing.lease_token = preview.lease_token
        return existing

    attempt = ExternalWriteAttempt(
        preview_id=preview.id,
        provider=preview.provider,
        status="attempting",
        response_code=None,
        external_id=None,
        idempotency_key=preview.idempotency_key,
        execution_hash=preview.execution_hash,
        lease_token=preview.lease_token,
        error_code=None,
        details_redacted=_json(
            {
                "simulated": False,
                "provider_contacted": False,
                "provider_accepted": False,
                "external_write_attempted": False,
                "phase": "attempt_persisted_before_execution",
            }
        ),
    )
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        attempt = (
            db.query(ExternalWriteAttempt)
            .filter(ExternalWriteAttempt.idempotency_key == preview.idempotency_key)
            .one()
        )
    return attempt


def _finalize_integration_execution(
    db: Session,
    *,
    preview: ExternalWritePreview,
    connection: IntegrationConnection,
    diff: dict[str, Any],
    attempt: ExternalWriteAttempt,
    result: IntegrationExecutionResult,
) -> SyncJournal:
    _renew_preview_lease(db, preview)
    attempt = db.get(ExternalWriteAttempt, attempt.id)
    attempt.status = result.status
    attempt.response_code = result.response_code
    attempt.external_id = result.external_id if result.provider_accepted else None
    attempt.error_code = result.error_code
    attempt.details_redacted = _json(_attempt_truth(result))

    journal = _existing_journal(db, preview)
    if journal is None:
        journal = SyncJournal(
            provider=preview.provider,
            connection_id=connection.id,
            entity_type=preview.entity_type,
            entity_id=preview.entity_id,
            external_id=result.external_id if result.provider_accepted else None,
            action=preview.action,
            status=result.status,
            idempotency_key=preview.idempotency_key,
            diff_json_redacted=_json({**diff, "execution": _attempt_truth(result)}),
        )
        db.add(journal)
    preview.status = (
        "simulated"
        if result.simulated
        else "executed"
        if result.provider_accepted
        else "failed"
    )
    preview.active_claim_key = None
    preview.lease_expires_at = None
    preview.heartbeat_at = utcnow()
    emit_event(
        db,
        "integration.sync_confirmed" if result.simulated or result.provider_accepted else "integration.sync_failed",
        entity_type=preview.entity_type,
        entity_id=preview.entity_id,
        payload={
            "provider": preview.provider,
            "connection_id": connection.id,
            **_attempt_truth(result),
        },
    )
    db.commit()
    return journal


def confirm_contact_sync_preview(db: Session, provider: str, preview_id: str) -> SyncJournal:
    provider = _provider(provider)
    preview = db.get(ExternalWritePreview, preview_id)
    if not preview or preview.provider != provider:
        raise ValueError("preview_not_found")
    if preview.status in {"executed", "simulated"}:
        existing = _existing_journal(db, preview)
        if existing:
            return existing
        raise ValueError("preview_already_executed")
    if preview.status == "cancelled":
        raise ValueError("preview_cancelled")
    if preview.status == "failed":
        existing = _existing_journal(db, preview)
        if existing:
            return existing
        raise ValueError("preview_failed")
    if preview.status not in {"pending_confirmation", "executing"}:
        raise ValueError("preview_not_pending")
    if preview.status == "pending_confirmation" and _preview_expired(preview):
        preview.status = "expired"
        preview.active_claim_key = None
        emit_event(
            db,
            "integration.confirmation_invalid",
            entity_type=preview.entity_type,
            entity_id=preview.entity_id,
            payload={"provider": provider, "reason": "expired"},
        )
        db.commit()
        raise ValueError("preview_expired")

    existing = _existing_journal(db, preview)
    if existing:
        preview.status = "simulated" if existing.status == "confirmed_dry_run" else "executed"
        preview.active_claim_key = None
        preview.lease_expires_at = None
        db.commit()
        return existing

    preview = _claim_preview(db, preview)
    existing = _existing_journal(db, preview)
    if existing:
        return existing

    contact = db.get(Contact, preview.entity_id)
    if not contact or contact.deleted_at is not None:
        preview.status = "stale"
        preview.active_claim_key = None
        preview.lease_expires_at = None
        db.commit()
        raise ValueError("preview_stale")
    _current_connection, current_diff, _mapped_after = _build_diff(db, provider, contact)
    stored_diff = json.loads(preview.diff_json_redacted or "{}")
    if (
        stored_diff.get("fields") != current_diff.get("fields")
        or stored_diff.get("policy", {}).get("mapping_version")
        != current_diff.get("policy", {}).get("mapping_version")
    ):
        preview.status = "stale"
        preview.active_claim_key = None
        preview.lease_expires_at = None
        emit_event(
            db,
            "integration.confirmation_invalid",
            entity_type=preview.entity_type,
            entity_id=preview.entity_id,
            payload={"provider": provider, "reason": "stale_diff"},
        )
        db.commit()
        raise ValueError("preview_stale")

    connection = _connection_from_preview(db, preview)
    diff = stored_diff
    attempt = _attempt_for_preview(db, preview)
    _renew_preview_lease(db, preview)
    db.commit()
    try:
        result = _execute_provider_write(connection, preview, diff)
    except Exception as exc:
        attempt = db.get(ExternalWriteAttempt, attempt.id)
        preview = db.get(ExternalWritePreview, preview.id)
        attempt.status = "reconciliation_required"
        attempt.error_code = "ambiguous_execution_exception"
        attempt.details_redacted = _json(
            {
                "simulated": False,
                "provider_contacted": None,
                "provider_accepted": False,
                "error_type": type(exc).__name__,
            }
        )
        preview.status = "reconciliation_required"
        preview.active_claim_key = None
        preview.lease_expires_at = None
        db.commit()
        raise ValueError("integration_reconciliation_required") from exc
    return _finalize_integration_execution(
        db,
        preview=preview,
        connection=connection,
        diff=diff,
        attempt=attempt,
        result=result,
    )


def cancel_contact_sync_preview(db: Session, provider: str, preview_id: str) -> ExternalWritePreview:
    provider = _provider(provider)
    preview = db.get(ExternalWritePreview, preview_id)
    if not preview or preview.provider != provider:
        raise ValueError("preview_not_found")
    if preview.status in {"executed", "simulated"}:
        raise ValueError("preview_already_executed")
    if preview.status == "pending_confirmation":
        preview.status = "cancelled"
        preview.active_claim_key = None
        emit_event(
            db,
            "integration.preview_cancelled",
            entity_type=preview.entity_type,
            entity_id=preview.entity_id,
            payload={"provider": provider},
        )
    return preview
