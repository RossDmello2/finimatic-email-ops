from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.idempotency import sha256_key
from app.core.time import utcnow
from app.db.models import (
    AccountFact,
    CellOutput,
    Contact,
    EmailVerification,
    LeadFact,
    Workbook,
    WorkbookColumn,
    WorkbookRow,
    WorkflowRun,
    WorkflowStep,
    WorkflowStepAttempt,
)
from app.enrichment.service import account_fact_to_dict, lead_fact_to_dict, neutral_copy_for_contact
from app.verification.service import run_local_verification, verification_policy_for_contact, verification_to_dict


DEFAULT_WORKFLOW_COST_CAP_UNITS = 100_000
WORKFLOW_LEASE_SECONDS = 30
ACTIVE_RUN_STATUSES = {"running"}
TERMINAL_CELL_STATUSES = {"completed", "failed", "blocked"}

DEFAULT_COLUMNS = [
    ("selected_contact", "Selected Contact", "import_select_contacts", 1),
    ("company_evidence", "Company Evidence", "enrich_company", 2),
    ("person_evidence", "Person Evidence", "enrich_person", 2),
    ("email_verification", "Email Verification", "verify_email", 3),
    ("lead_score", "Lead Score", "score_lead", 1),
    ("draft_readiness", "Draft Preview", "generate_draft", 5),
]


class WorkflowStepExecutionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class WorkflowLeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class StepResult:
    output: dict[str, Any]
    evidence_refs: list[str]
    cost_units: int


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _loads_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _column_config(column: WorkbookColumn) -> dict[str, Any]:
    parsed = _loads_json(column.config_json, {})
    return parsed if isinstance(parsed, dict) else {}


def _step_config_hash(column: WorkbookColumn) -> str:
    return sha256_key(column.key, column.step_type, _dumps(_column_config(column)))


def _evidence_ref(kind: str, row_id: str | None) -> str | None:
    return f"{kind}:{row_id}" if row_id else None


def _unique_refs(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    refs: list[str] = []
    for value in values:
        if value and value not in seen:
            refs.append(value)
            seen.add(value)
    return refs


def _contact_payload(contact: Contact | None) -> dict[str, Any]:
    if not contact:
        return {"missing_contact": True}
    return {
        "id": contact.id,
        "email": contact.email,
        "creator_name": contact.creator_name,
        "business_name": contact.business_name,
        "website_url": contact.website_url,
        "lead_category": contact.lead_category,
        "status": contact.status,
        "updated_at": _iso(contact.updated_at),
    }


def _input_hash(row: WorkbookRow, column: WorkbookColumn, prior_cells: dict[str, CellOutput]) -> str:
    dependencies = [
        {
            "column": key,
            "status": cell.status,
            "output_hash": sha256_key(cell.output_json),
        }
        for key, cell in sorted(prior_cells.items())
    ]
    return sha256_key(row.row_key, column.key, _dumps(_contact_payload(row.contact)), _dumps(dependencies))


def _latest_output_map(db: Session, workbook_id: str) -> dict[tuple[str, str], CellOutput]:
    outputs = (
        db.query(CellOutput)
        .outerjoin(WorkflowStep, CellOutput.workflow_step_id == WorkflowStep.id)
        .outerjoin(WorkflowRun, WorkflowStep.workflow_run_id == WorkflowRun.id)
        .filter(CellOutput.workbook_id == workbook_id)
        .order_by(WorkflowRun.started_at.desc(), WorkflowRun.created_at.desc(), WorkflowStep.created_at.desc(), CellOutput.created_at.desc(), CellOutput.id.desc())
        .all()
    )
    output_map: dict[tuple[str, str], CellOutput] = {}
    for output in outputs:
        output_map.setdefault((output.workbook_row_id, output.workbook_column_id), output)
    return output_map


def _latest_cell_for_column(db: Session, row_id: str, column_id: str) -> CellOutput | None:
    return (
        db.query(CellOutput)
        .outerjoin(WorkflowStep, CellOutput.workflow_step_id == WorkflowStep.id)
        .outerjoin(WorkflowRun, WorkflowStep.workflow_run_id == WorkflowRun.id)
        .filter(CellOutput.workbook_row_id == row_id, CellOutput.workbook_column_id == column_id)
        .order_by(WorkflowRun.started_at.desc(), WorkflowRun.created_at.desc(), WorkflowStep.created_at.desc(), CellOutput.created_at.desc(), CellOutput.id.desc())
        .first()
    )


def _cell_for_hash(db: Session, row_id: str, column_id: str, input_hash: str, config_hash: str) -> CellOutput | None:
    return (
        db.query(CellOutput)
        .filter(
            CellOutput.workbook_row_id == row_id,
            CellOutput.workbook_column_id == column_id,
            CellOutput.input_hash == input_hash,
            CellOutput.step_config_hash == config_hash,
        )
        .first()
    )


def _next_attempt_num(db: Session, row_id: str, input_hash: str, config_hash: str) -> int:
    return (
        db.query(WorkflowStepAttempt)
        .filter(
            WorkflowStepAttempt.workbook_row_id == row_id,
            WorkflowStepAttempt.input_hash == input_hash,
            WorkflowStepAttempt.step_config_hash == config_hash,
        )
        .count()
        + 1
    )


def _upsert_cell(
    db: Session,
    *,
    workbook: Workbook,
    row: WorkbookRow,
    column: WorkbookColumn,
    step: WorkflowStep,
    input_hash: str,
    config_hash: str,
    status: str,
    output: dict[str, Any],
    evidence_refs: list[str],
    cost_units: int,
) -> CellOutput:
    cell = _cell_for_hash(db, row.id, column.id, input_hash, config_hash)
    payload = _dumps(output)
    refs = _dumps(_unique_refs(evidence_refs))
    if cell is None:
        cell = CellOutput(
            workbook_id=workbook.id,
            workbook_row_id=row.id,
            workbook_column_id=column.id,
            workflow_step_id=step.id,
            input_hash=input_hash,
            step_config_hash=config_hash,
            output_json=payload,
            evidence_refs=refs,
            cost_units=cost_units,
            status=status,
        )
        db.add(cell)
    else:
        cell.workflow_step_id = step.id
        cell.output_json = payload
        cell.evidence_refs = refs
        cell.cost_units = cost_units
        cell.status = status
    return cell


def _sync_workbook_columns(db: Session, workbook: Workbook) -> None:
    existing = {row.key: row for row in db.query(WorkbookColumn).filter(WorkbookColumn.workbook_id == workbook.id).all()}
    for index, (key, label, step_type, cost_units) in enumerate(DEFAULT_COLUMNS, start=1):
        config = {"version": 1, "cost_units": cost_units}
        if key in existing:
            column = existing[key]
            column.label = label
            column.step_type = step_type
            column.position = index
            current_config = _column_config(column)
            if "cost_units" not in current_config:
                current_config["cost_units"] = cost_units
                current_config.setdefault("version", 1)
                column.config_json = _dumps(current_config)
            continue
        db.add(
            WorkbookColumn(
                workbook_id=workbook.id,
                key=key,
                label=label,
                step_type=step_type,
                position=index,
                config_json=_dumps(config),
            )
        )


def _sync_workbook_rows(db: Session, workbook: Workbook) -> None:
    existing = {
        row.contact_id
        for row in db.query(WorkbookRow).filter(WorkbookRow.workbook_id == workbook.id, WorkbookRow.contact_id.is_not(None)).all()
    }
    contacts = db.query(Contact).filter(Contact.deleted_at.is_(None)).order_by(Contact.created_at.asc()).all()
    for contact in contacts:
        if contact.id not in existing:
            db.add(WorkbookRow(workbook_id=workbook.id, contact_id=contact.id, row_key=contact.email, status="pending"))


def ensure_default_workbook(db: Session) -> Workbook:
    workbook = db.query(Workbook).filter(Workbook.name == "Default Enrichment Workbook").first()
    if workbook is None:
        workbook = Workbook(
            name="Default Enrichment Workbook",
            description="Orange Slice-style contact selection, enrichment, verification, scoring, and draft preview workflow.",
            status="active",
        )
        db.add(workbook)
        db.flush()
    _sync_workbook_columns(db, workbook)
    _sync_workbook_rows(db, workbook)
    db.flush()
    return workbook


def _cell_payload(cell: CellOutput | None) -> dict:
    if cell is None:
        return {
            "status": "pending",
            "output": None,
            "evidence_refs": [],
            "cost_units": 0,
            "input_hash": None,
            "step_config_hash": None,
        }
    return {
        "status": cell.status,
        "output": _loads_json(cell.output_json, {}),
        "evidence_refs": _loads_json(cell.evidence_refs, []),
        "cost_units": cell.cost_units,
        "input_hash": cell.input_hash,
        "step_config_hash": cell.step_config_hash,
        "created_at": _iso(cell.created_at),
    }


def _attempt_to_dict(row: WorkflowStepAttempt) -> dict:
    step = row.step
    column = step.column if step else None
    workbook_row = row.row
    contact = workbook_row.contact if workbook_row else None
    return {
        "id": row.id,
        "run_id": step.workflow_run_id if step else None,
        "step_type": step.step_type if step else None,
        "column_key": column.key if column else None,
        "column_label": column.label if column else None,
        "row_id": row.workbook_row_id,
        "contact_email": contact.email if contact else (workbook_row.row_key if workbook_row else None),
        "status": row.status,
        "attempt_num": row.attempt_num,
        "input_hash": row.input_hash,
        "step_config_hash": row.step_config_hash,
        "latency_ms": row.latency_ms,
        "cost_units": row.cost_units,
        "error_code": row.error_code,
        "created_at": _iso(row.created_at),
    }


def _step_to_dict(row: WorkflowStep) -> dict:
    return {
        "id": row.id,
        "run_id": row.workflow_run_id,
        "column_id": row.workbook_column_id,
        "column_key": row.column.key if row.column else None,
        "step_type": row.step_type,
        "position": row.position,
        "status": row.status,
        "config_hash": row.config_hash,
        "created_at": _iso(row.created_at),
    }


def run_to_dict(row: WorkflowRun, db: Session | None = None) -> dict:
    total_cost = 0
    steps: list[dict] = []
    if db is not None:
        step_rows = (
            db.query(WorkflowStep)
            .filter(WorkflowStep.workflow_run_id == row.id)
            .order_by(WorkflowStep.position.asc(), WorkflowStep.created_at.asc())
            .all()
        )
        steps = [_step_to_dict(step) for step in step_rows]
        step_ids = [step.id for step in step_rows]
        if step_ids:
            attempts = db.query(WorkflowStepAttempt).filter(WorkflowStepAttempt.workflow_step_id.in_(step_ids)).all()
            total_cost = sum(attempt.cost_units for attempt in attempts)
    return {
        "id": row.id,
        "workbook_id": row.workbook_id,
        "status": row.status,
        "execution_hash": row.execution_hash,
        "lease_owner": row.lease_owner,
        "lease_expires_at": _iso(row.lease_expires_at),
        "heartbeat_at": _iso(row.heartbeat_at),
        "lease_generation": row.lease_generation,
        "checkpoint": _loads_json(row.checkpoint_json, {}),
        "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
        "created_by": row.created_by,
        "created_at": _iso(row.created_at),
        "total_cost_units": total_cost,
        "steps": steps,
    }


def workbook_payload(db: Session, workbook: Workbook) -> dict:
    _sync_workbook_columns(db, workbook)
    _sync_workbook_rows(db, workbook)
    db.flush()
    rows = db.query(WorkbookRow).filter(WorkbookRow.workbook_id == workbook.id).order_by(WorkbookRow.created_at.asc()).all()
    columns = db.query(WorkbookColumn).filter(WorkbookColumn.workbook_id == workbook.id).order_by(WorkbookColumn.position.asc()).all()
    output_map = _latest_output_map(db, workbook.id)
    runs = db.query(WorkflowRun).filter(WorkflowRun.workbook_id == workbook.id).order_by(WorkflowRun.created_at.desc()).limit(10).all()
    attempts = (
        db.query(WorkflowStepAttempt)
        .join(WorkflowStep, WorkflowStepAttempt.workflow_step_id == WorkflowStep.id)
        .filter(WorkflowStep.workflow_run_id.in_([run.id for run in runs]))
        .order_by(WorkflowStepAttempt.created_at.desc(), WorkflowStepAttempt.id.desc())
        .limit(50)
        .all()
        if runs
        else []
    )
    return {
        "id": workbook.id,
        "name": workbook.name,
        "description": workbook.description,
        "status": workbook.status,
        "columns": [
            {"id": column.id, "key": column.key, "label": column.label, "step_type": column.step_type, "position": column.position}
            for column in columns
        ],
        "rows": [
            {
                "id": row.id,
                "contact_id": row.contact_id,
                "email": row.contact.email if row.contact else row.row_key,
                "name": (row.contact.creator_name or row.contact.business_name or row.contact.email) if row.contact else row.row_key,
                "status": row.status,
                "cells": {
                    column.key: _cell_payload(output_map.get((row.id, column.id)))
                    for column in columns
                },
            }
            for row in rows
        ],
        "runs": [run_to_dict(run, db) for run in runs],
        "attempts": [_attempt_to_dict(row) for row in attempts],
    }


def _lead_facts(db: Session, contact: Contact) -> list[LeadFact]:
    return db.query(LeadFact).filter(LeadFact.contact_id == contact.id).order_by(LeadFact.created_at.desc()).all()


def _account_facts(db: Session, contact: Contact) -> list[AccountFact]:
    return db.query(AccountFact).filter(AccountFact.contact_id == contact.id).order_by(AccountFact.created_at.desc()).all()


def _verification_for_contact(db: Session, contact: Contact) -> EmailVerification | None:
    return db.query(EmailVerification).filter(EmailVerification.email == contact.email.strip().lower()).order_by(EmailVerification.updated_at.desc()).first()


def _cost_for_column(column: WorkbookColumn) -> int:
    configured = _column_config(column).get("cost_units")
    try:
        return max(int(configured), 0)
    except (TypeError, ValueError):
        return 1


def _execute_step(db: Session, row: WorkbookRow, column: WorkbookColumn) -> StepResult:
    contact = row.contact
    config = _column_config(column)
    if config.get("force_error"):
        raise WorkflowStepExecutionError("FORCED_WORKFLOW_STEP_ERROR", "Step was configured to fail for retry/resume validation.")
    if contact is None:
        raise WorkflowStepExecutionError("CONTACT_MISSING", "Workbook row no longer has a contact.")

    contact_ref = _evidence_ref("contact", contact.id)
    cost_units = _cost_for_column(column)
    if column.step_type == "import_select_contacts":
        return StepResult(
            output={
                "selected": True,
                "email": contact.email,
                "name": contact.creator_name or contact.business_name or contact.email,
                "source": contact.source,
                "status": contact.status,
            },
            evidence_refs=_unique_refs([contact_ref]),
            cost_units=cost_units,
        )

    if column.step_type == "enrich_company":
        facts = _account_facts(db, contact)
        usable = [account_fact_to_dict(fact) for fact in facts if account_fact_to_dict(fact)["usable"]]
        refs = [contact_ref]
        for fact in facts:
            refs.extend([_evidence_ref("account_fact", fact.id), _evidence_ref("evidence_source", fact.source_id)])
        return StepResult(
            output={
                "facts_found": len(facts),
                "usable_facts": len(usable),
                "company": contact.business_name,
                "website": contact.website_url,
                "status": "evidence_found" if usable else "no_approved_account_facts",
            },
            evidence_refs=_unique_refs(refs),
            cost_units=cost_units,
        )

    if column.step_type == "enrich_person":
        facts = _lead_facts(db, contact)
        usable = [lead_fact_to_dict(fact) for fact in facts if lead_fact_to_dict(fact)["usable"]]
        refs = [contact_ref]
        for fact in facts:
            refs.extend([_evidence_ref("lead_fact", fact.id), _evidence_ref("evidence_source", fact.source_id)])
        return StepResult(
            output={
                "facts_found": len(facts),
                "usable_facts": len(usable),
                "name": contact.creator_name,
                "category": contact.lead_category,
                "status": "evidence_found" if usable else "no_approved_lead_facts",
            },
            evidence_refs=_unique_refs(refs),
            cost_units=cost_units,
        )

    if column.step_type == "verify_email":
        verification = run_local_verification(db, contact)
        policy = verification_policy_for_contact(db, contact)
        return StepResult(
            output={
                "verification": verification_to_dict(verification),
                "policy": policy,
                "status": verification.status,
            },
            evidence_refs=_unique_refs([contact_ref, _evidence_ref("email_verification", verification.id)]),
            cost_units=cost_units,
        )

    if column.step_type == "score_lead":
        facts = _lead_facts(db, contact)
        usable_facts = [fact for fact in facts if lead_fact_to_dict(fact)["usable"]]
        verification = _verification_for_contact(db, contact)
        policy = verification_policy_for_contact(db, contact)
        score = 20
        if contact.website_url:
            score += 15
        if usable_facts:
            score += min(len(usable_facts) * 15, 35)
        if policy["severity"] == "allow":
            score += 20
        elif policy["severity"] == "warn":
            score += 5
        else:
            score -= 20
        score = max(0, min(score, 100))
        refs = [contact_ref, _evidence_ref("email_verification", verification.id if verification else None)]
        refs.extend(_evidence_ref("lead_fact", fact.id) for fact in usable_facts[:5])
        return StepResult(
            output={
                "score": score,
                "grade": "high" if score >= 70 else "medium" if score >= 45 else "low",
                "verification_severity": policy["severity"],
                "usable_evidence_count": len(usable_facts),
            },
            evidence_refs=_unique_refs(refs),
            cost_units=cost_units,
        )

    if column.step_type == "generate_draft":
        neutral = neutral_copy_for_contact(contact)
        verification = _verification_for_contact(db, contact)
        facts = _lead_facts(db, contact)
        usable_facts = [fact for fact in facts if lead_fact_to_dict(fact)["usable"]]
        refs = [contact_ref, _evidence_ref("email_verification", verification.id if verification else None)]
        refs.extend(_evidence_ref("lead_fact", fact.id) for fact in usable_facts[:5])
        return StepResult(
            output={
                "draft_preview": {
                    "to": contact.email,
                    "subject": neutral["subject"],
                    "body": neutral["body"],
                    "persisted_draft_id": None,
                },
                "status": "neutral_preview",
                "personalization_policy": "neutral_copy_until_source_backed_claims_are_available",
            },
            evidence_refs=_unique_refs(refs),
            cost_units=cost_units,
        )

    raise WorkflowStepExecutionError("UNKNOWN_STEP_TYPE", f"Workflow step type {column.step_type!r} is not supported.")


def _record_attempt(
    db: Session,
    *,
    step: WorkflowStep,
    row: WorkbookRow,
    status: str,
    input_hash: str,
    config_hash: str,
    latency_ms: int,
    cost_units: int,
    error_code: str | None = None,
) -> WorkflowStepAttempt:
    attempt = WorkflowStepAttempt(
        workflow_step_id=step.id,
        workbook_row_id=row.id,
        status=status,
        attempt_num=_next_attempt_num(db, row.id, input_hash, config_hash),
        input_hash=input_hash,
        step_config_hash=config_hash,
        latency_ms=latency_ms,
        cost_units=cost_units,
        error_code=error_code,
    )
    db.add(attempt)
    return attempt


def _refresh_row_status(db: Session, row: WorkbookRow, columns: list[WorkbookColumn]) -> None:
    statuses = []
    for column in columns:
        cell = _latest_cell_for_column(db, row.id, column.id)
        statuses.append(cell.status if cell else "pending")
    if any(status == "failed" for status in statuses):
        row.status = "failed"
    elif any(status == "blocked" for status in statuses):
        row.status = "blocked"
    elif statuses and all(status == "completed" for status in statuses):
        row.status = "completed"
    elif any(status in TERMINAL_CELL_STATUSES for status in statuses):
        row.status = "partial"
    else:
        row.status = "pending"


def _active_run(db: Session, workbook_id: str) -> WorkflowRun | None:
    return (
        db.query(WorkflowRun)
        .filter(WorkflowRun.workbook_id == workbook_id, WorkflowRun.status.in_(ACTIVE_RUN_STATUSES))
        .order_by(WorkflowRun.created_at.desc())
        .first()
    )


def _as_utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _workflow_lease_is_live(row: WorkflowRun, now) -> bool:
    lease_expires_at = _as_utc(row.lease_expires_at)
    if lease_expires_at is not None:
        return lease_expires_at > now
    started_at = _as_utc(row.started_at)
    return bool(started_at and started_at + timedelta(seconds=WORKFLOW_LEASE_SECONDS) > now)


def _workflow_owner() -> str:
    return f"workflow:{os.getpid()}:{threading.get_ident()}"


def _workflow_execution_hash(
    workbook: Workbook,
    columns: list[WorkbookColumn],
    *,
    retry_failed_only: bool,
    cost_cap_units: int,
) -> str:
    return sha256_key(
        "workflow-execution",
        workbook.id,
        retry_failed_only,
        cost_cap_units,
        _dumps([(column.id, _step_config_hash(column)) for column in columns]),
    )


def _claim_workflow_run(
    db: Session,
    workbook: Workbook,
    *,
    execution_hash: str,
    owner: str,
) -> tuple[WorkflowRun, bool]:
    now = utcnow()
    lease_expires_at = now + timedelta(seconds=WORKFLOW_LEASE_SECONDS)
    token = uuid.uuid4().hex
    claim_key = sha256_key("workflow-active", workbook.id)
    active = _active_run(db, workbook.id)
    if active and _workflow_lease_is_live(active, now):
        return active, False

    if active:
        legacy_cutoff = now - timedelta(seconds=WORKFLOW_LEASE_SECONDS)
        claimed = (
            db.query(WorkflowRun)
            .filter(
                WorkflowRun.id == active.id,
                WorkflowRun.status == "running",
                or_(
                    WorkflowRun.lease_expires_at <= now,
                    and_(
                        WorkflowRun.lease_expires_at.is_(None),
                        or_(
                            WorkflowRun.started_at.is_(None),
                            WorkflowRun.started_at <= legacy_cutoff,
                        ),
                    ),
                ),
            )
            .update(
                {
                    "active_claim_key": claim_key,
                    "execution_hash": execution_hash,
                    "lease_token": token,
                    "lease_owner": owner,
                    "lease_expires_at": lease_expires_at,
                    "heartbeat_at": now,
                    "lease_generation": WorkflowRun.lease_generation + 1,
                },
                synchronize_session=False,
            )
        )
        if claimed == 1:
            db.commit()
            db.expire_all()
            return db.get(WorkflowRun, active.id), True
        db.rollback()
        refreshed = _active_run(db, workbook.id)
        if refreshed:
            return refreshed, False

    run = WorkflowRun(
        workbook_id=workbook.id,
        status="running",
        active_claim_key=claim_key,
        execution_hash=execution_hash,
        lease_token=token,
        lease_owner=owner,
        lease_expires_at=lease_expires_at,
        heartbeat_at=now,
        lease_generation=1,
        checkpoint_json=_dumps({"status": "claimed"}),
        started_at=now,
        created_by="operator",
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        active = _active_run(db, workbook.id)
        if active:
            return active, False
        raise
    return run, True


def _renew_workflow_lease(
    db: Session,
    run: WorkflowRun,
    token: str,
    checkpoint: dict[str, Any],
) -> None:
    now = utcnow()
    renewed = (
        db.query(WorkflowRun)
        .filter(
            WorkflowRun.id == run.id,
            WorkflowRun.status == "running",
            WorkflowRun.lease_token == token,
        )
        .update(
            {
                "heartbeat_at": now,
                "lease_expires_at": now + timedelta(seconds=WORKFLOW_LEASE_SECONDS),
                "checkpoint_json": _dumps(checkpoint),
            },
            synchronize_session=False,
        )
    )
    if renewed != 1:
        raise WorkflowLeaseLost(run.id)


def _step_for_column(
    db: Session,
    run: WorkflowRun,
    column: WorkbookColumn,
    config_hash: str,
) -> WorkflowStep:
    step = (
        db.query(WorkflowStep)
        .filter(
            WorkflowStep.workflow_run_id == run.id,
            WorkflowStep.workbook_column_id == column.id,
        )
        .order_by(WorkflowStep.created_at.desc())
        .first()
    )
    if step is None:
        step = WorkflowStep(
            workflow_run_id=run.id,
            workbook_column_id=column.id,
            step_type=column.step_type,
            position=column.position,
            status="running",
            config_hash=config_hash,
        )
        db.add(step)
        db.flush()
    else:
        step.step_type = column.step_type
        step.position = column.position
        step.status = "running"
        step.config_hash = config_hash
    return step


def _finish_workflow_run(db: Session, run: WorkflowRun, token: str, status: str) -> WorkflowRun:
    now = utcnow()
    finished = (
        db.query(WorkflowRun)
        .filter(
            WorkflowRun.id == run.id,
            WorkflowRun.status == "running",
            WorkflowRun.lease_token == token,
        )
        .update(
            {
                "status": status,
                "active_claim_key": None,
                "lease_expires_at": None,
                "heartbeat_at": now,
                "completed_at": now,
                "checkpoint_json": _dumps({"status": status, "completed_at": now.isoformat()}),
            },
            synchronize_session=False,
        )
    )
    if finished != 1:
        raise WorkflowLeaseLost(run.id)
    db.commit()
    db.expire_all()
    return db.get(WorkflowRun, run.id)


def run_workflow(
    db: Session,
    workbook: Workbook,
    *,
    retry_failed_only: bool = False,
    cost_cap_units: int | None = None,
) -> WorkflowRun:
    _sync_workbook_columns(db, workbook)
    _sync_workbook_rows(db, workbook)
    db.commit()

    cap = DEFAULT_WORKFLOW_COST_CAP_UNITS if cost_cap_units is None else max(cost_cap_units, 0)
    columns = db.query(WorkbookColumn).filter(WorkbookColumn.workbook_id == workbook.id).order_by(WorkbookColumn.position.asc()).all()
    rows = db.query(WorkbookRow).filter(WorkbookRow.workbook_id == workbook.id).order_by(WorkbookRow.created_at.asc()).all()
    execution_hash = _workflow_execution_hash(
        workbook,
        columns,
        retry_failed_only=retry_failed_only,
        cost_cap_units=cap,
    )
    run, acquired = _claim_workflow_run(
        db,
        workbook,
        execution_hash=execution_hash,
        owner=_workflow_owner(),
    )
    if not acquired:
        return run

    token = run.lease_token
    if not token:
        raise WorkflowLeaseLost(run.id)
    spent = sum(
        attempt.cost_units
        for attempt in (
            db.query(WorkflowStepAttempt)
            .join(WorkflowStep, WorkflowStepAttempt.workflow_step_id == WorkflowStep.id)
            .filter(WorkflowStep.workflow_run_id == run.id)
            .all()
        )
    )
    run_status = "completed"

    try:
        for column in columns:
            config_hash = _step_config_hash(column)
            step = _step_for_column(db, run, column, config_hash)
            _renew_workflow_lease(
                db,
                run,
                token,
                {"column_id": column.id, "column_key": column.key, "status": "running"},
            )
            db.commit()
            step_status = "completed"

            for row in rows:
                prior_cells = {
                    prior.key: latest
                    for prior in columns
                    if prior.position < column.position
                    for latest in [_latest_cell_for_column(db, row.id, prior.id)]
                    if latest and latest.status == "completed"
                }
                input_hash = _input_hash(row, column, prior_cells)
                existing = _cell_for_hash(db, row.id, column.id, input_hash, config_hash)
                if existing and existing.status == "completed":
                    continue
                if retry_failed_only and existing and existing.status not in {"failed", "blocked"}:
                    continue

                _renew_workflow_lease(
                    db,
                    run,
                    token,
                    {
                        "column_id": column.id,
                        "column_key": column.key,
                        "row_id": row.id,
                        "status": "executing",
                    },
                )
                db.commit()
                projected_cost = _cost_for_column(column)
                if spent + projected_cost > cap:
                    output = {
                        "error_code": "WORKFLOW_COST_CAP_EXCEEDED",
                        "cost_cap_units": cap,
                        "spent_units": spent,
                        "projected_step_cost_units": projected_cost,
                    }
                    _record_attempt(
                        db,
                        step=step,
                        row=row,
                        status="blocked",
                        input_hash=input_hash,
                        config_hash=config_hash,
                        latency_ms=0,
                        cost_units=0,
                        error_code="WORKFLOW_COST_CAP_EXCEEDED",
                    )
                    _upsert_cell(
                        db,
                        workbook=workbook,
                        row=row,
                        column=column,
                        step=step,
                        input_hash=input_hash,
                        config_hash=config_hash,
                        status="blocked",
                        output=output,
                        evidence_refs=_unique_refs([_evidence_ref("contact", row.contact_id)]),
                        cost_units=0,
                    )
                    _renew_workflow_lease(
                        db,
                        run,
                        token,
                        {"column_id": column.id, "row_id": row.id, "status": "blocked"},
                    )
                    db.commit()
                    step_status = "blocked"
                    run_status = "blocked"
                    break

                started = time.perf_counter()
                try:
                    result = _execute_step(db, row, column)
                except WorkflowStepExecutionError as exc:
                    latency_ms = max(int((time.perf_counter() - started) * 1000), 0)
                    _record_attempt(
                        db,
                        step=step,
                        row=row,
                        status="failed",
                        input_hash=input_hash,
                        config_hash=config_hash,
                        latency_ms=latency_ms,
                        cost_units=0,
                        error_code=exc.code,
                    )
                    _upsert_cell(
                        db,
                        workbook=workbook,
                        row=row,
                        column=column,
                        step=step,
                        input_hash=input_hash,
                        config_hash=config_hash,
                        status="failed",
                        output={"error_code": exc.code, "message": str(exc)},
                        evidence_refs=_unique_refs([_evidence_ref("contact", row.contact_id)]),
                        cost_units=0,
                    )
                    _renew_workflow_lease(
                        db,
                        run,
                        token,
                        {"column_id": column.id, "row_id": row.id, "status": "failed"},
                    )
                    db.commit()
                    step_status = "failed"
                    run_status = "failed"
                    break

                latency_ms = max(int((time.perf_counter() - started) * 1000), 0)
                spent += result.cost_units
                _record_attempt(
                    db,
                    step=step,
                    row=row,
                    status="completed",
                    input_hash=input_hash,
                    config_hash=config_hash,
                    latency_ms=latency_ms,
                    cost_units=result.cost_units,
                )
                _upsert_cell(
                    db,
                    workbook=workbook,
                    row=row,
                    column=column,
                    step=step,
                    input_hash=input_hash,
                    config_hash=config_hash,
                    status="completed",
                    output=result.output,
                    evidence_refs=result.evidence_refs,
                    cost_units=result.cost_units,
                )
                _renew_workflow_lease(
                    db,
                    run,
                    token,
                    {"column_id": column.id, "row_id": row.id, "status": "completed"},
                )
                db.commit()

            step = db.get(WorkflowStep, step.id)
            step.status = step_status
            for workbook_row in rows:
                _refresh_row_status(db, workbook_row, columns)
            _renew_workflow_lease(
                db,
                run,
                token,
                {"column_id": column.id, "column_key": column.key, "status": step_status},
            )
            db.commit()
            if step_status in {"failed", "blocked"}:
                break

        return _finish_workflow_run(db, run, token, run_status)
    except WorkflowLeaseLost:
        db.rollback()
        return db.get(WorkflowRun, run.id)
