import json
from datetime import datetime, timedelta, timezone

from app.core.time import utcnow
from app.db.models import AuditEvent, ConversationMessage, SendAttempt, SendQueue
from app.db.session import SessionLocal
from conftest import configure_sender


def _contact_and_draft(client, email: str, *, subject: str = "Queue operation") -> tuple[dict, dict]:
    contact = client.post(
        "/api/contacts",
        json={"email": email, "creator_name": "Queue Operator", "source": "test"},
    ).json()
    draft = client.post(
        "/api/drafts",
        json={"contact_id": contact["id"], "subject": subject, "body": "Synthetic queue operation."},
    ).json()
    return contact, draft


def _closed_window() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return (now + timedelta(hours=2)).strftime("%H:%M"), (now + timedelta(hours=3)).strftime("%H:%M")


def _queued(client, email: str) -> dict:
    configure_sender(client, canary_verified=True, dry_run=False)
    contact, draft = _contact_and_draft(client, email)
    approved = client.post("/api/drafts/approve-bulk", json={"draft_ids": [draft["id"]]})
    assert approved.status_code == 200
    return client.get("/api/queue").json()["items"][-1]


def test_operator_can_cancel_pending_queue_without_provider_call(client):
    queue = _queued(client, "phase17-cancel@recipient.test")

    response = client.post(f"/api/queue/{queue['id']}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert client.post("/api/queue/process").json()["processed"] == 0
    assert client.app.state.transport.sent == []
    with SessionLocal() as db:
        assert db.get(SendQueue, queue["id"]).status == "cancelled"
        assert db.query(AuditEvent).filter_by(event_type="queue.cancelled", entity_id=queue["id"]).count() == 1


def test_operator_cannot_cancel_provider_accepted_queue(client):
    queue = _queued(client, "phase17-accepted-cancel@recipient.test")
    assert client.post("/api/queue/process").json()["provider_accepted"] == 1

    response = client.post(f"/api/queue/{queue['id']}/cancel")

    assert response.status_code == 409
    assert response.json()["detail"] == "queue_already_provider_accepted"
    assert len(client.app.state.transport.sent) == 1


def test_reconciliation_cancel_rejects_provider_accepted_evidence(client):
    queue = _queued(client, "phase17-reconciliation-accepted-cancel@recipient.test")
    assert client.post("/api/queue/process").json()["provider_accepted"] == 1
    with SessionLocal() as db:
        row = db.get(SendQueue, queue["id"])
        row.status = "reconciliation_required"
        db.query(SendAttempt).filter_by(queue_id=row.id).update({"status": "reconciliation_required"})
        db.commit()

    response = client.post(f"/api/queue/{queue['id']}/reconcile", json={"action": "cancel"})

    assert response.status_code == 409
    assert response.json()["detail"] == "provider_acceptance_reconciliation_requires_finalize"
    with SessionLocal() as db:
        assert db.get(SendQueue, queue["id"]).status == "reconciliation_required"
        attempt = db.query(SendAttempt).filter_by(queue_id=queue["id"]).one()
        assert attempt.provider_accepted is True
        assert attempt.provider_msg_id


def test_delete_queue_entry_cancels_never_attempted_work(client):
    queue = _queued(client, "phase17-delete-pending@recipient.test")

    response = client.delete(f"/api/queue/{queue['id']}")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert client.app.state.transport.sent == []
    with SessionLocal() as db:
        assert db.get(SendQueue, queue["id"]).status == "cancelled"
        assert db.query(AuditEvent).filter_by(event_type="queue.cancelled", entity_id=queue["id"]).count() == 1


def test_clear_queue_cancels_actionable_work_and_preserves_evidence(client):
    accepted = _queued(client, "phase17-clear-accepted@recipient.test")
    assert client.post("/api/queue/process").json()["provider_accepted"] == 1
    pending = _queued(client, "phase17-clear-pending@recipient.test")
    uncertain = _queued(client, "phase17-clear-uncertain@recipient.test")
    with SessionLocal() as db:
        row = db.get(SendQueue, uncertain["id"])
        row.status = "reconciliation_required"
        db.add(
            SendAttempt(
                queue_id=row.id,
                contact_id=row.contact_id,
                draft_id=row.draft_id,
                idempotency_key=row.idempotency_key,
                dispatch_lock_key=row.idempotency_key,
                status="reconciliation_required",
                sender_identity="synthetic-sender@finimatic.test",
                simulated=False,
                provider_contacted=True,
                provider_accepted=False,
                error_code="ambiguous_provider_result",
                created_at=utcnow(),
            )
        )
        db.commit()

    response = client.delete("/api/queue")

    assert response.status_code == 200
    assert response.json() == {
        "cancelled": 1,
        "already_cancelled": 0,
        "preserved_accepted": 1,
        "preserved_uncertain": 1,
        "skipped": 0,
    }
    with SessionLocal() as db:
        assert db.get(SendQueue, accepted["id"]).status == "provider_accepted"
        assert db.get(SendQueue, pending["id"]).status == "cancelled"
        assert db.get(SendQueue, uncertain["id"]).status == "reconciliation_required"
        event = db.query(AuditEvent).filter_by(event_type="queue.cleared").one()
        payload = json.loads(event.payload)
        assert payload["preserved_accepted"] == 1
        assert payload["preserved_uncertain"] == 1
    assert len(client.app.state.transport.sent) == 1


def test_historical_high_sequence_does_not_create_large_followup_number(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact, initial = _contact_and_draft(client, "phase17-sequence-gap@recipient.test", subject="Initial")
    accepted = client.post(f"/api/drafts/{initial['id']}/approve")
    assert accepted.status_code == 200
    assert accepted.json()["queue"]["sequence_num"] == 1
    high_draft = client.post(
        "/api/drafts",
        json={"contact_id": contact["id"], "subject": "Historical artifact", "body": "Not actionable."},
    ).json()
    with SessionLocal() as db:
        db.add(
            SendQueue(
                contact_id=contact["id"],
                draft_id=high_draft["id"],
                sequence_num=992919,
                scheduled_at=utcnow(),
                schedule_source="historical_import",
                status="provider_accepted",
                idempotency_key="phase17-historical-high-sequence",
            )
        )
        db.commit()
    next_draft = client.post(
        "/api/drafts",
        json={"contact_id": contact["id"], "subject": "Normal follow-up", "body": "Synthetic follow-up."},
    ).json()

    conflict = client.post(f"/api/drafts/{next_draft['id']}/approve")

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["next_sequence_num"] == 2

    sent = client.post(f"/api/drafts/{next_draft['id']}/approve", json={"sequence_num": 2})

    assert sent.status_code == 200
    assert sent.json()["queue"]["sequence_num"] == 2
    assert len(client.app.state.transport.sent) == 2


def test_settings_window_change_releases_existing_policy_deferred_row(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    start, end = _closed_window()
    assert client.post(
        "/api/settings",
        json={"send_window_start": start, "send_window_end": end, "send_timezone": "UTC"},
    ).status_code == 200
    _, draft = _contact_and_draft(client, "phase17-window-release@recipient.test")

    approved = client.post(f"/api/drafts/{draft['id']}/approve")
    assert approved.status_code == 200
    queue_id = approved.json()["queue_id"]
    deferred = client.get(f"/api/queue/{queue_id}").json()
    assert deferred["status"] == "pending"
    assert deferred["schedule_source"] == "policy_deferral"
    assert "SEND_WINDOW_NOT_ELAPSED" in deferred["policy_block_reasons"]
    assert client.app.state.transport.sent == []

    updated = client.post(
        "/api/settings",
        json={"send_window_start": "00:00", "send_window_end": "23:59", "send_timezone": "UTC"},
    )
    assert updated.status_code == 200
    released = client.get(f"/api/queue/{queue_id}").json()
    assert released["status"] == "pending"
    assert released["schedule_source"] == "policy_released"
    assert datetime.fromisoformat(released["scheduled_at"]) <= utcnow()
    assert client.app.state.transport.sent == []

    processed = client.post("/api/queue/process").json()
    assert processed["provider_accepted"] == 1
    assert len(client.app.state.transport.sent) == 1


def test_reapproving_same_deferred_draft_dispatches_selected_row(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    start, end = _closed_window()
    client.post(
        "/api/settings",
        json={"send_window_start": start, "send_window_end": end, "send_timezone": "UTC"},
    )
    _, draft = _contact_and_draft(client, "phase17-reapprove@recipient.test")
    first = client.post(f"/api/drafts/{draft['id']}/approve")
    assert first.status_code == 200
    assert first.json()["delivery_status"] == "deferred"

    client.post(
        "/api/settings",
        json={"send_window_start": "00:00", "send_window_end": "23:59", "send_timezone": "UTC"},
    )
    second = client.post(f"/api/drafts/{draft['id']}/approve")

    assert second.status_code == 200
    assert second.json()["delivery_status"] == "provider_accepted"
    assert second.json()["queue_id"] == first.json()["queue_id"]
    assert len(client.app.state.transport.sent) == 1


def test_replacement_draft_supersedes_never_attempted_sequence(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact, first_draft = _contact_and_draft(
        client,
        "phase17-replacement@recipient.test",
        subject="Obsolete subject",
    )
    queued = client.post("/api/drafts/approve-bulk", json={"draft_ids": [first_draft["id"]]})
    assert queued.status_code == 200
    queue_id = client.get("/api/queue").json()["items"][-1]["id"]
    second_draft = client.post(
        "/api/drafts",
        json={
            "contact_id": contact["id"],
            "subject": "Replacement subject",
            "body": "Replacement synthetic queue operation.",
        },
    ).json()

    approved = client.post(f"/api/drafts/{second_draft['id']}/approve")

    assert approved.status_code == 200
    assert approved.json()["delivery_status"] == "provider_accepted"
    assert approved.json()["queue_id"] == queue_id
    queue = client.get(f"/api/queue/{queue_id}").json()
    assert queue["draft_id"] == second_draft["id"]
    assert len(client.get("/api/queue").json()["items"]) == 1
    assert [item["subject"] for item in client.app.state.transport.sent] == ["Replacement subject"]


def test_replacement_is_rejected_after_provider_acceptance(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    contact, first_draft = _contact_and_draft(client, "phase17-replacement-accepted@recipient.test")
    accepted = client.post(f"/api/drafts/{first_draft['id']}/approve")
    assert accepted.status_code == 200
    assert accepted.json()["delivery_status"] == "provider_accepted"
    replacement = client.post(
        "/api/drafts",
        json={"contact_id": contact["id"], "subject": "Replacement", "body": "Replacement body."},
    ).json()

    rejected = client.post(f"/api/drafts/{replacement['id']}/approve")

    assert rejected.status_code == 409
    assert rejected.json()["detail"]["reason"] == "sequence_already_sent"
    assert rejected.json()["detail"]["next_sequence_num"] == 2
    assert len(client.app.state.transport.sent) == 1


def test_replacement_is_rejected_when_provider_contact_is_uncertain(client):
    queue = _queued(client, "phase17-replacement-uncertain@recipient.test")
    with SessionLocal() as db:
        row = db.get(SendQueue, queue["id"])
        db.add(
            SendAttempt(
                queue_id=row.id,
                contact_id=row.contact_id,
                draft_id=row.draft_id,
                idempotency_key=row.idempotency_key,
                dispatch_lock_key=row.idempotency_key,
                status="reconciliation_required",
                sender_identity="synthetic-sender@finimatic.test",
                simulated=False,
                provider_contacted=True,
                provider_accepted=False,
                error_code="ambiguous_provider_result",
                created_at=utcnow(),
            )
        )
        row.status = "reconciliation_required"
        db.commit()
        contact_id = row.contact_id
    replacement = client.post(
        "/api/drafts",
        json={"contact_id": contact_id, "subject": "Unsafe replacement", "body": "Must not dispatch."},
    ).json()

    rejected = client.post(f"/api/drafts/{replacement['id']}/approve")

    assert rejected.status_code == 409
    assert rejected.json()["detail"]["reason"] == "queue_reconciliation_required"
    assert rejected.json()["detail"]["queue_id"] == queue["id"]
    assert len(client.app.state.transport.sent) == 0


def test_conversation_reads_do_not_backfill_provider_messages(client):
    contact, draft = _contact_and_draft(client, "phase17-readonly-conversation@recipient.test")
    with SessionLocal() as db:
        db.add(
            SendAttempt(
                queue_id="manual-read-only",
                contact_id=contact["id"],
                draft_id=draft["id"],
                idempotency_key="phase17-readonly-conversation",
                dispatch_lock_key="phase17-readonly-conversation",
                status="provider_accepted",
                sender_identity="synthetic-sender@finimatic.test",
                configured_transport="gmail_api",
                effective_transport="gmail_api",
                transport_source="test",
                simulated=False,
                provider_contacted=True,
                provider_accepted=True,
                provider_msg_id="provider-native-readonly-proof",
                provider_response_classification="gmail_api_accepted",
                sent_at=utcnow(),
                created_at=utcnow(),
            )
        )
        db.commit()
        assert db.query(ConversationMessage).count() == 0

    listed = client.get("/api/conversations")
    shown = client.get(f"/api/conversations/{contact['id']}")

    assert listed.status_code == 200
    assert shown.status_code == 200
    with SessionLocal() as db:
        assert db.query(ConversationMessage).count() == 0


def test_reevaluate_and_send_now_dispatches_only_selected_future_row(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    client.post("/api/settings", json={"send_delay_s": 3600})
    _, selected_draft = _contact_and_draft(client, "phase17-selected-now@recipient.test")
    _, other_draft = _contact_and_draft(client, "phase17-selected-other@recipient.test")
    approved = client.post(
        "/api/drafts/approve-bulk",
        json={"draft_ids": [selected_draft["id"], other_draft["id"]]},
    )
    assert approved.status_code == 200
    queue = client.get("/api/queue").json()["items"]
    selected = next(row for row in queue if row["draft_id"] == selected_draft["id"])
    other = next(row for row in queue if row["draft_id"] == other_draft["id"])

    sent = client.post(f"/api/queue/{selected['id']}/send-now")

    assert sent.status_code == 200
    payload = sent.json()
    assert payload["queue"]["status"] == "provider_accepted"
    assert payload["result"]["provider_accepted"] == 1
    assert payload["result"]["processed"] == 1
    assert [item["to"] for item in client.app.state.transport.sent] == ["phase17-selected-now@recipient.test"]
    assert client.get(f"/api/queue/{other['id']}").json()["status"] == "pending"


def test_bulk_approval_queues_without_implying_scheduler_dispatch(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    _, first = _contact_and_draft(client, "phase17-bulk-queue-one@recipient.test")
    _, second = _contact_and_draft(client, "phase17-bulk-queue-two@recipient.test")

    response = client.post("/api/drafts/approve-bulk", json={"draft_ids": [first["id"], second["id"]]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected"] == 2
    assert payload["queued"] == 2
    assert payload["scheduler_effective"] is False
    assert payload["dispatch_requested"] is False
    assert client.app.state.transport.sent == []


def test_bulk_approve_and_send_dispatches_only_selected_rows_with_scheduler_disabled(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    _, first = _contact_and_draft(client, "phase17-bulk-send-one@recipient.test")
    _, second = _contact_and_draft(client, "phase17-bulk-send-two@recipient.test")
    _, unselected = _contact_and_draft(client, "phase17-bulk-send-unselected@recipient.test")

    response = client.post(
        "/api/drafts/approve-bulk-and-send",
        json={"draft_ids": [first["id"], second["id"]]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected"] == 2
    assert payload["processed"] == 2
    assert payload["provider_accepted"] == 2
    assert payload["scheduler_effective"] is False
    assert {item["to"] for item in client.app.state.transport.sent} == {
        "phase17-bulk-send-one@recipient.test",
        "phase17-bulk-send-two@recipient.test",
    }
    assert all(row["draft_id"] != unselected["id"] for row in client.get("/api/queue").json()["items"])


def test_zero_eligible_process_reports_future_work_and_scheduler_state(client):
    configure_sender(client, canary_verified=True, dry_run=False)
    client.post("/api/settings", json={"send_delay_s": 3600})
    _, draft = _contact_and_draft(client, "phase17-future-process@recipient.test")
    queued = client.post("/api/drafts/approve-bulk", json={"draft_ids": [draft["id"]]})
    assert queued.status_code == 200

    processed = client.post("/api/queue/process")

    assert processed.status_code == 200
    payload = processed.json()
    assert payload["processed"] == 0
    assert payload["eligible_count"] == 0
    assert payload["future_scheduled_count"] == 1
    assert payload["next_due_at"]
    assert payload["scheduler_effective"] is False
    assert payload["zero_work_reason"] == "future_scheduled"
    assert client.app.state.transport.sent == []
