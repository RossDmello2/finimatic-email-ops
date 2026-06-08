import json
import queue
import threading
from datetime import timedelta

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.idempotency import sha256_key
from app.core.time import utcnow
from app.db.models import (
    CellOutput,
    ExternalWriteAttempt,
    ExternalWritePreview,
    SyncJournal,
    Workbook,
    WorkbookColumn,
    WorkflowRun,
    WorkflowStepAttempt,
)
from app.db.session import SessionLocal
from app.integrations import service as integration_service
from app.workflows import service as workflow_service


def test_phase17_migration_adds_lease_columns_and_unique_attempt_key(tmp_path, monkeypatch):
    db_path = tmp_path / "phase17_leases.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    command.upgrade(Config("alembic.ini"), "head")

    inspector = inspect(create_engine(database_url))
    workflow_columns = {column["name"] for column in inspector.get_columns("workflow_runs")}
    preview_columns = {column["name"] for column in inspector.get_columns("external_write_previews")}
    attempt_columns = {column["name"] for column in inspector.get_columns("external_write_attempts")}
    attempt_indexes = {index["name"]: index for index in inspector.get_indexes("external_write_attempts")}
    assert {
        "active_claim_key",
        "execution_hash",
        "lease_token",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "lease_generation",
        "checkpoint_json",
    } <= workflow_columns
    assert {
        "active_claim_key",
        "execution_hash",
        "lease_token",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "lease_generation",
    } <= preview_columns
    assert {"execution_hash", "lease_token"} <= attempt_columns
    assert bool(attempt_indexes["ix_external_write_attempts_idempotency_key"]["unique"]) is True


def _create_contact(client, email: str) -> dict:
    response = client.post(
        "/api/contacts",
        json={
            "email": email,
            "creator_name": "Phase 17 Lease Test",
            "website_url": "https://phase17.example",
            "source": "test",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _workflow_id(client) -> str:
    response = client.get("/api/workflows")
    assert response.status_code == 200, response.text
    return response.json()["items"][0]["id"]


def test_workflow_claim_is_atomic_across_concurrent_workers(client):
    _create_contact(client, "phase17-workflow-claim@recipient.test")
    workbook_id = _workflow_id(client)
    barrier = threading.Barrier(2)
    results: queue.Queue[tuple[str, bool] | Exception] = queue.Queue()

    def claim() -> None:
        try:
            with SessionLocal() as db:
                workbook = db.get(Workbook, workbook_id)
                columns = (
                    db.query(WorkbookColumn)
                    .filter(WorkbookColumn.workbook_id == workbook_id)
                    .order_by(WorkbookColumn.position)
                    .all()
                )
                execution_hash = workflow_service._workflow_execution_hash(
                    workbook,
                    columns,
                    retry_failed_only=False,
                    cost_cap_units=workflow_service.DEFAULT_WORKFLOW_COST_CAP_UNITS,
                )
                barrier.wait(timeout=10)
                run, acquired = workflow_service._claim_workflow_run(
                    db,
                    workbook,
                    execution_hash=execution_hash,
                    owner=f"test-worker:{threading.get_ident()}",
                )
                results.put((run.id, acquired))
        except Exception as exc:
            results.put(exc)

    workers = [threading.Thread(target=claim) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)
        assert not worker.is_alive()

    observed = [results.get_nowait(), results.get_nowait()]
    errors = [value for value in observed if isinstance(value, Exception)]
    assert not errors
    claims = [value for value in observed if not isinstance(value, Exception)]
    assert len({value[0] for value in claims}) == 1
    assert sorted(value[1] for value in claims) == [False, True]
    with SessionLocal() as db:
        rows = db.query(WorkflowRun).filter_by(workbook_id=workbook_id, status="running").all()
        assert len(rows) == 1
        assert rows[0].lease_token
        assert rows[0].execution_hash
        assert rows[0].lease_generation == 1


def test_workflow_expired_lease_recovers_persisted_progress_without_duplicates(client):
    _create_contact(client, "phase17-workflow-recovery@recipient.test")
    workbook_id = _workflow_id(client)
    completed = client.post(f"/api/workflows/{workbook_id}/run")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"

    with SessionLocal() as db:
        run = db.query(WorkflowRun).filter_by(workbook_id=workbook_id).one()
        run_id = run.id
        output_count = db.query(CellOutput).count()
        attempt_count = db.query(WorkflowStepAttempt).count()
        original_generation = run.lease_generation
        run.status = "running"
        run.active_claim_key = sha256_key("workflow-active", workbook_id)
        run.lease_token = "expired-workflow-lease"
        run.lease_owner = "dead-worker"
        run.lease_expires_at = utcnow() - timedelta(minutes=5)
        run.completed_at = None
        db.commit()

    recovered = client.post(f"/api/workflows/{workbook_id}/run")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["id"] == run_id
    assert recovered.json()["status"] == "completed"
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        assert run.lease_generation == original_generation + 1
        assert run.active_claim_key is None
        assert db.query(CellOutput).count() == output_count
        assert db.query(WorkflowStepAttempt).count() == attempt_count


def test_integration_persists_attempt_before_execution_and_records_dry_run_truth(client, monkeypatch):
    contact = _create_contact(client, "phase17-integration-attempt@recipient.test")
    preview_response = client.post(
        "/api/integrations/google_sheets/preview-sync",
        json={"contact_id": contact["id"]},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    observed: dict[str, object] = {}

    def execute(connection, claimed_preview, diff):
        del connection, diff
        with SessionLocal() as db:
            attempt = (
                db.query(ExternalWriteAttempt)
                .filter_by(idempotency_key=claimed_preview.idempotency_key)
                .one()
            )
            observed["status"] = attempt.status
            observed["lease_token"] = attempt.lease_token
            observed["details"] = json.loads(attempt.details_redacted)
        return integration_service.IntegrationExecutionResult(
            status="confirmed_dry_run",
            response_code="local_dry_run",
            simulated=True,
            provider_contacted=False,
            provider_accepted=False,
            detail="test dry run",
        )

    monkeypatch.setattr(integration_service, "_execute_provider_write", execute)
    confirmed = client.post(
        "/api/integrations/google_sheets/confirm-sync",
        json={"preview_id": preview["id"]},
    )

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed_dry_run"
    assert confirmed.json()["external_id"] is None
    assert observed["status"] == "attempting"
    assert observed["lease_token"]
    assert observed["details"]["provider_contacted"] is False
    with SessionLocal() as db:
        attempt = db.query(ExternalWriteAttempt).filter_by(idempotency_key=preview["idempotency_key"]).one()
        preview_row = db.get(ExternalWritePreview, preview["id"])
        details = json.loads(attempt.details_redacted)
        assert preview_row.status == "simulated"
        assert preview_row.active_claim_key is None
        assert attempt.status == "confirmed_dry_run"
        assert attempt.external_id is None
        assert details["simulated"] is True
        assert details["provider_contacted"] is False
        assert details["provider_accepted"] is False
        assert db.query(SyncJournal).filter_by(idempotency_key=preview["idempotency_key"]).count() == 1


def test_integration_claim_is_atomic_across_concurrent_workers(client):
    contact = _create_contact(client, "phase17-integration-claim@recipient.test")
    preview_response = client.post(
        "/api/integrations/salesforce/preview-sync",
        json={"contact_id": contact["id"]},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview_id = preview_response.json()["id"]
    barrier = threading.Barrier(2)
    results: queue.Queue[str | Exception] = queue.Queue()

    def claim() -> None:
        try:
            with SessionLocal() as db:
                preview = db.get(ExternalWritePreview, preview_id)
                barrier.wait(timeout=10)
                claimed = integration_service._claim_preview(db, preview)
                results.put(claimed.lease_token)
        except Exception as exc:
            results.put(exc)

    workers = [threading.Thread(target=claim) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)
        assert not worker.is_alive()

    observed = [results.get_nowait(), results.get_nowait()]
    tokens = [value for value in observed if isinstance(value, str)]
    errors = [value for value in observed if isinstance(value, Exception)]
    assert len(tokens) == 1
    assert len(errors) == 1
    assert str(errors[0]) == "preview_in_progress"
    with SessionLocal() as db:
        preview = db.get(ExternalWritePreview, preview_id)
        assert preview.status == "executing"
        assert preview.lease_token == tokens[0]
        assert preview.lease_generation == 1
        assert db.query(ExternalWriteAttempt).count() == 0


def test_integration_expired_lease_reuses_attempt_without_duplicate_execution(client):
    contact = _create_contact(client, "phase17-integration-recovery@recipient.test")
    preview_response = client.post(
        "/api/integrations/hubspot/preview-sync",
        json={"contact_id": contact["id"]},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview_data = preview_response.json()

    with SessionLocal() as db:
        preview = db.get(ExternalWritePreview, preview_data["id"])
        preview.status = "executing"
        preview.lease_token = "expired-integration-lease"
        preview.lease_owner = "dead-worker"
        preview.lease_expires_at = utcnow() - timedelta(minutes=5)
        preview.heartbeat_at = utcnow() - timedelta(minutes=5)
        preview.lease_generation = 1
        db.add(
            ExternalWriteAttempt(
                preview_id=preview.id,
                provider=preview.provider,
                status="attempting",
                idempotency_key=preview.idempotency_key,
                execution_hash=preview.execution_hash,
                lease_token=preview.lease_token,
                details_redacted=json.dumps(
                    {
                        "simulated": False,
                        "provider_contacted": False,
                        "provider_accepted": False,
                    },
                    sort_keys=True,
                ),
            )
        )
        db.commit()

    recovered = client.post(
        "/api/integrations/hubspot/confirm-sync",
        json={"preview_id": preview_data["id"]},
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["status"] == "confirmed_dry_run"
    assert recovered.json()["external_id"] is None
    with SessionLocal() as db:
        preview = db.get(ExternalWritePreview, preview_data["id"])
        assert preview.status == "simulated"
        assert preview.active_claim_key is None
        assert preview.lease_generation == 2
        attempts = db.query(ExternalWriteAttempt).filter_by(idempotency_key=preview.idempotency_key).all()
        assert len(attempts) == 1
        assert attempts[0].status == "confirmed_dry_run"
        assert db.query(SyncJournal).filter_by(idempotency_key=preview.idempotency_key).count() == 1
