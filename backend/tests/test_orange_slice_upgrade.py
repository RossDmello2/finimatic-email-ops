import json
from datetime import timedelta

from app.ai.schema import DraftSuggestion
from app.core.time import utcnow
from app.db.models import (
    CellOutput,
    Contact,
    DraftEvidenceCheck,
    EmailVerification,
    EmailVerificationAttempt,
    EvidenceSource,
    ExternalWriteAttempt,
    ExternalWritePreview,
    InboxPlacementTest,
    IntegrationConnection,
    IntegrationMapping,
    LeadFact,
    RecipientDomainCap,
    Reply,
    SendAttempt,
    SenderDomainHealth,
    SenderMailbox,
    SyncJournal,
    WorkbookColumn,
    WorkflowRun,
    WorkflowStepAttempt,
)
from app.db.session import SessionLocal
from app.send.policy import prequeue_block_reasons
from app.verification.service import run_verification_waterfall, verification_policy_for_contact
from conftest import configure_sender


def _create_contact(client, email="valid@example.com"):
    response = client.post(
        "/api/contacts",
        json={
            "email": email,
            "creator_name": "Evidence Lead",
            "website_url": "https://example.com",
            "personalization": "Runs a course platform and may need student support automation.",
            "source": "manual",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_draft(client, contact_id):
    response = client.post(
        "/api/drafts",
        json={
            "contact_id": contact_id,
            "subject": "Practical automation idea",
            "body": "Hi, I noticed your course platform and wanted to share a practical automation idea.",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approved_queue_for_deliverability(client, email="deliverability-target@example.com"):
    configure_sender(client, canary_verified=True, dry_run=True)
    contact = _create_contact(client, email=email)
    draft = _create_draft(client, contact["id"])
    verification = client.post(f"/api/verification/contact/{contact['id']}/run")
    assert verification.status_code == 200, verification.text
    seed = client.post(f"/api/enrichment/contacts/{contact['id']}/seed")
    assert seed.status_code == 200, seed.text
    approve = client.post("/api/drafts/approve-bulk", json={"draft_ids": [draft["id"]]})
    assert approve.status_code == 200, approve.text
    assert approve.json()["queued"] == 1
    queue = client.get("/api/queue").json()["items"][-1]
    return contact, draft, queue["id"]


def _insert_success_attempt(db, contact_id: str, draft_id: str, *, when, sender="sender@example.com", suffix="success") -> None:
    db.add(
        SendAttempt(
            queue_id=f"historical-{suffix}",
            contact_id=contact_id,
            draft_id=draft_id,
            idempotency_key=f"historical-{suffix}",
            status="provider_accepted",
            configured_transport="test_provider",
            effective_transport="test_provider",
            transport_source="test_fixture",
            simulated=False,
            provider_contacted=True,
            provider_accepted=True,
            provider_response_classification="test_provider_accepted",
            sender_identity=sender,
            sent_at=when,
        )
    )


def test_enrichment_seed_and_evidence_check(client):
    contact = _create_contact(client)

    seed = client.post(f"/api/enrichment/contacts/{contact['id']}/seed")
    assert seed.status_code == 200, seed.text
    assert seed.json()["total"] >= 1

    evidence = client.get(f"/api/enrichment/contacts/{contact['id']}")
    assert evidence.status_code == 200, evidence.text
    payload = evidence.json()
    assert payload["evidence_check"]["neutral_copy_required"] is False
    assert payload["lead_facts"][0]["source_label"]

    workbook = client.get("/api/enrichment/workbook")
    assert workbook.status_code == 200, workbook.text
    assert workbook.json()["summary"]["contacts_with_evidence"] >= 1


def test_verification_blocks_invalid_draft_approval(client):
    configure_sender(client, canary_verified=True, dry_run=True)
    contact = _create_contact(client, email="not-an-email")
    draft = _create_draft(client, contact["id"])

    verification = client.post(f"/api/verification/contact/{contact['id']}/run")
    assert verification.status_code == 200, verification.text
    assert verification.json()["verification"]["status"] == "invalid"

    approve = client.post(f"/api/drafts/{draft['id']}/approve")
    assert approve.status_code == 422, approve.text
    assert "RECIPIENT_EMAIL_INVALID" in approve.text


def test_verification_allow_statuses_proceed_through_prequeue_policy(client):
    contact = _create_contact(client, email="verified-proceeds@example.com")

    verification = client.post(f"/api/verification/contact/{contact['id']}/run")
    assert verification.status_code == 200, verification.text
    assert verification.json()["verification"]["status"] == "unknown"
    assert verification.json()["verification"]["mx_present"] is False
    assert verification.json()["verification"]["provider_status"] == "syntax_passed_domain_unverified"

    with SessionLocal() as db:
        db_contact = db.get(Contact, contact["id"])
        policy = verification_policy_for_contact(db, db_contact)
        assert policy["status"] == "unknown"
        assert policy["allowed"] is True
        assert prequeue_block_reasons(db_contact, db) == []

        row = db.query(EmailVerification).filter_by(email=contact["email"]).one()
        row.status = "verified"
        row.provider = "test_provider"
        row.provider_status = "mailbox_verified"
        row.confidence = 0.99
        row.last_verified_at = utcnow()
        row.expires_at = utcnow() + timedelta(days=30)
        db.commit()

    with SessionLocal() as db:
        db_contact = db.get(Contact, contact["id"])
        policy = verification_policy_for_contact(db, db_contact)
        assert policy["status"] == "verified"
        assert policy["allowed"] is True
        assert prequeue_block_reasons(db_contact, db) == []


def test_verification_warn_statuses_are_allowed_with_warnings(client):
    role_contact = _create_contact(client, email="support@example.com")
    catch_all_contact = _create_contact(client, email="catch-all@example.com")
    unknown_contact = _create_contact(client, email="unknown-warning@example.com")

    role = client.post(f"/api/verification/contact/{role_contact['id']}/run")
    assert role.status_code == 200, role.text
    assert role.json()["verification"]["status"] == "role_based"

    with SessionLocal() as db:
        now = utcnow()
        db.add(
            EmailVerification(
                contact_id=catch_all_contact["id"],
                email=catch_all_contact["email"],
                status="catch_all",
                confidence=0.6,
                provider="test_provider",
                provider_status="catch_all_domain",
                is_catch_all=True,
                mx_present=True,
                last_verified_at=now,
                expires_at=now + timedelta(days=30),
            )
        )
        db.commit()

    with SessionLocal() as db:
        policies = {}
        for row in [role_contact, catch_all_contact, unknown_contact]:
            db_contact = db.get(Contact, row["id"])
            policies[row["email"]] = verification_policy_for_contact(db, db_contact)

        assert policies[role_contact["email"]] == {
            "status": "role_based",
            "severity": "warn",
            "reason_code": "RECIPIENT_EMAIL_ROLE_BASED",
            "allowed": True,
        }
        assert policies[catch_all_contact["email"]] == {
            "status": "catch_all",
            "severity": "warn",
            "reason_code": "RECIPIENT_EMAIL_CATCH_ALL",
            "allowed": True,
        }
        assert policies[unknown_contact["email"]] == {
            "status": "unknown",
            "severity": "warn",
            "reason_code": "RECIPIENT_EMAIL_UNKNOWN",
            "allowed": True,
        }


def test_verification_block_statuses_are_blocked(client):
    invalid_contact = _create_contact(client, email="not-an-email")
    disposable_contact = _create_contact(client, email="lead@mailinator.com")
    stale_contact = _create_contact(client, email="stale-verification@example.com")
    bounce_contact = _create_contact(client, email="hard-bounce@example.com")
    suppressed_contact = _create_contact(client, email="suppressed@example.com")

    assert client.post(f"/api/verification/contact/{invalid_contact['id']}/run").json()["verification"]["status"] == "invalid"
    assert client.post(f"/api/verification/contact/{disposable_contact['id']}/run").json()["verification"]["status"] == "disposable"
    assert client.post(f"/api/verification/contact/{stale_contact['id']}/run").json()["verification"]["status"] == "unknown"
    assert client.post("/api/replies", json={"contact_id": bounce_contact["id"], "classified_as": "bounce", "raw_summary": "Delivery failed."}).status_code == 200
    assert client.post(f"/api/verification/contact/{bounce_contact['id']}/run").json()["verification"]["status"] == "hard_bounce"
    assert client.post("/api/suppressions", json={"email": suppressed_contact["email"], "reason": "manual"}).status_code == 200
    assert client.post(f"/api/verification/contact/{suppressed_contact['id']}/run").json()["verification"]["status"] == "suppressed"

    with SessionLocal() as db:
        stale_row = db.query(EmailVerification).filter_by(email=stale_contact["email"]).one()
        stale_row.expires_at = utcnow() - timedelta(days=1)
        db.commit()

    expected = {
        invalid_contact["id"]: ("invalid", "RECIPIENT_EMAIL_INVALID"),
        disposable_contact["id"]: ("disposable", "RECIPIENT_EMAIL_DISPOSABLE"),
        stale_contact["id"]: ("stale", "RECIPIENT_EMAIL_STALE"),
        bounce_contact["id"]: ("hard_bounce", "RECIPIENT_EMAIL_HARD_BOUNCE"),
        suppressed_contact["id"]: ("suppressed", "RECIPIENT_EMAIL_SUPPRESSED"),
    }
    with SessionLocal() as db:
        for contact_id, (status, reason) in expected.items():
            db_contact = db.get(Contact, contact_id)
            policy = verification_policy_for_contact(db, db_contact)
            assert policy["status"] == status
            assert policy["severity"] == "block"
            assert policy["allowed"] is False
            assert policy["reason_code"] == reason
            assert prequeue_block_reasons(db_contact, db) == [reason]


def test_provider_failure_records_attempt_without_fake_success(client):
    contact = _create_contact(client, email="provider-failure@example.com")

    class FailingProvider:
        name = "failing_provider"

        def verify(self, db, contact):
            raise RuntimeError("provider secret failure text")

    with SessionLocal() as db:
        db_contact = db.get(Contact, contact["id"])
        row = run_verification_waterfall(db, db_contact, adapters=[FailingProvider()])
        db.commit()
        verification_id = row.id

    with SessionLocal() as db:
        row = db.get(EmailVerification, verification_id)
        attempts = db.query(EmailVerificationAttempt).filter_by(verification_id=verification_id).all()
        assert row.status == "unknown"
        assert row.provider == "none"
        assert row.provider_status == "provider_unavailable"
        assert row.status not in {"verified", "valid"}
        assert len(attempts) == 1
        assert attempts[0].provider == "failing_provider"
        assert attempts[0].status == "provider_error"
        assert attempts[0].response_code == "RuntimeError"
        assert "provider secret failure text" not in (attempts[0].raw_response_redacted or "")


def test_draft_approval_and_queue_entry_respect_verification_gate(client):
    configure_sender(client, canary_verified=True, dry_run=True)
    contact = _create_contact(client, email="queue-blocked-invalid")
    draft = _create_draft(client, contact["id"])

    verification = client.post(f"/api/verification/contact/{contact['id']}/run")
    assert verification.status_code == 200, verification.text
    assert verification.json()["verification"]["status"] == "invalid"

    approve = client.post(f"/api/drafts/{draft['id']}/approve")
    assert approve.status_code == 422, approve.text
    assert "RECIPIENT_EMAIL_INVALID" in approve.text

    queue = client.post("/api/queue", json={"contact_id": contact["id"], "draft_id": draft["id"]})
    assert queue.status_code == 422, queue.text
    assert "RECIPIENT_EMAIL_INVALID" in queue.text


def test_deliverability_domain_health_blocks_queue_processing(client):
    configure_sender(client, canary_verified=True, dry_run=True)
    contact = _create_contact(client, email="valid@example.com")
    draft = _create_draft(client, contact["id"])
    verification = client.post(f"/api/verification/contact/{contact['id']}/run")
    assert verification.status_code == 200, verification.text
    seed = client.post(f"/api/enrichment/contacts/{contact['id']}/seed")
    assert seed.status_code == 200, seed.text

    approve = client.post("/api/drafts/approve-bulk", json={"draft_ids": [draft["id"]]})
    assert approve.status_code == 200, approve.text
    assert approve.json()["queued"] == 1
    queue_id = client.get("/api/queue").json()["items"][-1]["id"]

    with SessionLocal() as db:
        health = db.query(SenderDomainHealth).filter(SenderDomainHealth.domain == "example.com").first()
        if health is None:
            health = SenderDomainHealth(domain="example.com")
            db.add(health)
        health.dmarc_status = "failed"
        db.commit()

    processed = client.post("/api/queue/process")
    assert processed.status_code == 200, processed.text
    assert processed.json()["blocked"] == 1

    queue = client.get(f"/api/queue/{queue_id}")
    assert queue.status_code == 200, queue.text
    assert "SENDER_DOMAIN_UNHEALTHY" in queue.json()["policy_block_reasons"]


def test_deliverability_recipient_domain_cap_blocks_queue_processing(client):
    contact, draft, queue_id = _approved_queue_for_deliverability(client, email="cap-target@recipient.test")
    historical = _create_contact(client, email="already-sent@recipient.test")

    with SessionLocal() as db:
        _insert_success_attempt(db, historical["id"], draft["id"], when=utcnow(), suffix="recipient-cap")
        db.add(
            RecipientDomainCap(
                sender_email="sender@example.com",
                recipient_domain="recipient.test",
                daily_cap=1,
                sent_today=1,
                window_date=utcnow().date().isoformat(),
                status="active",
            )
        )
        db.commit()

    processed = client.post("/api/queue/process")
    assert processed.status_code == 200, processed.text
    assert processed.json()["blocked"] == 1

    queue = client.get(f"/api/queue/{queue_id}")
    assert queue.status_code == 200, queue.text
    payload = queue.json()
    assert "RECIPIENT_DOMAIN_DAILY_CAP_EXCEEDED" in payload["policy_block_reasons"]
    assert any(
        gate["gate"] == "per_domain_daily_cap"
        and gate["passed"] is False
        and gate["details"]["daily_cap"] == 1
        for gate in payload["policy_trace"]
    )


def test_deliverability_mailbox_ramp_cap_blocks_queue_processing(client):
    contact, draft, queue_id = _approved_queue_for_deliverability(client, email="ramp-target@recipient.test")

    with SessionLocal() as db:
        db.add(
            SenderMailbox(
                email="sender@example.com",
                domain="example.com",
                provider="gmail",
                transport="gmail_api",
                status="ready",
                daily_cap=10,
                hourly_cap=10,
                min_delay_s=0,
                ramp_stage="low_volume",
            )
        )
        sent_at = utcnow() - timedelta(hours=2)
        for index in range(10):
            sent_contact = Contact(email=f"ramp-history-{index}@history{index}.test", creator_name="History", source="test")
            db.add(sent_contact)
            db.flush()
            _insert_success_attempt(db, sent_contact.id, draft["id"], when=sent_at, suffix=f"ramp-{index}")
        db.commit()

    processed = client.post("/api/queue/process")
    assert processed.status_code == 200, processed.text
    assert processed.json()["blocked"] == 1

    payload = client.get(f"/api/queue/{queue_id}").json()
    assert "MAILBOX_RAMP_CAP_EXCEEDED" in payload["policy_block_reasons"]
    assert any(
        gate["gate"] == "per_mailbox_ramp_stage"
        and gate["passed"] is False
        and gate["details"]["effective_cap"] == 10
        for gate in payload["policy_trace"]
    )


def test_deliverability_bounce_rate_blocks_queue_processing(client):
    contact, draft, queue_id = _approved_queue_for_deliverability(client, email="bounce-rate-target@recipient.test")

    with SessionLocal() as db:
        sent_at = utcnow() - timedelta(days=2)
        for index in range(10):
            _insert_success_attempt(db, contact["id"], draft["id"], when=sent_at, suffix=f"bounce-rate-{index}")
        bounced_contact = Contact(email="historical-bounce@example.test", creator_name="History", source="test")
        db.add(bounced_contact)
        db.flush()
        db.add(Reply(contact_id=bounced_contact.id, received_at=utcnow() - timedelta(days=1), classified_as="bounce", raw_summary="Delivery failed."))
        db.commit()

    processed = client.post("/api/queue/process")
    assert processed.status_code == 200, processed.text
    assert processed.json()["blocked"] == 1

    payload = client.get(f"/api/queue/{queue_id}").json()
    assert "BOUNCE_RATE_HIGH" in payload["policy_block_reasons"]
    assert any(
        gate["gate"] == "bounce_rate_under_threshold"
        and gate["passed"] is False
        and gate["details"]["count"] == 1
        for gate in payload["policy_trace"]
    )


def test_deliverability_complaint_rate_blocks_queue_processing(client):
    contact, draft, queue_id = _approved_queue_for_deliverability(client, email="complaint-rate-target@recipient.test")

    with SessionLocal() as db:
        sent_at = utcnow() - timedelta(days=2)
        for index in range(10):
            _insert_success_attempt(db, contact["id"], draft["id"], when=sent_at, suffix=f"complaint-rate-{index}")
        complaint_contact = Contact(email="historical-complaint@example.test", creator_name="History", source="test")
        db.add(complaint_contact)
        db.flush()
        db.add(Reply(contact_id=complaint_contact.id, received_at=utcnow() - timedelta(days=1), classified_as="complaint", raw_summary="Spam complaint telemetry."))
        db.commit()

    processed = client.post("/api/queue/process")
    assert processed.status_code == 200, processed.text
    assert processed.json()["blocked"] == 1

    payload = client.get(f"/api/queue/{queue_id}").json()
    assert "COMPLAINT_RATE_HIGH" in payload["policy_block_reasons"]
    assert any(
        gate["gate"] == "complaint_rate_under_threshold"
        and gate["passed"] is False
        and gate["details"]["count"] == 1
        for gate in payload["policy_trace"]
    )


def test_deliverability_deferral_spike_blocks_queue_processing(client):
    contact, draft, queue_id = _approved_queue_for_deliverability(client, email="deferral-target@recipient.test")

    with SessionLocal() as db:
        for index in range(3):
            db.add(
                SendAttempt(
                    queue_id=f"deferral-{index}",
                    contact_id=contact["id"],
                    draft_id=draft["id"],
                    idempotency_key=f"deferral-{index}",
                    status="failed",
                    sender_identity="sender@example.com",
                    sent_at=utcnow(),
                    error_code="gmail_deferral",
                    error_detail="Temporary provider deferral.",
                )
            )
        db.commit()

    processed = client.post("/api/queue/process")
    assert processed.status_code == 200, processed.text
    assert processed.json()["blocked"] == 1

    payload = client.get(f"/api/queue/{queue_id}").json()
    assert "RECENT_DEFERRAL_SPIKE" in payload["policy_block_reasons"]
    assert any(
        gate["gate"] == "no_recent_deferral_spike"
        and gate["passed"] is False
        and gate["details"]["count"] == 3
        for gate in payload["policy_trace"]
    )


def test_gmail_api_acceptance_is_not_inbox_placement_proof(client):
    configure_sender(client, canary_verified=True, dry_run=True)
    with SessionLocal() as db:
        db.add(
            InboxPlacementTest(
                sender_email="sender@example.com",
                seed_email="seed@example.net",
                recipient_domain="example.net",
                subject="Provider accepted seed",
                status="provider_accepted",
                placement="unknown",
                provider_msg_id="gmail-api-message-id",
                details_redacted="Provider accepted the message. Inbox placement has not been checked.",
            )
        )
        db.commit()

    summary = client.get("/api/deliverability/summary")
    assert summary.status_code == 200, summary.text
    seed_row = summary.json()["inbox_tests"][0]
    assert seed_row["status"] == "provider_accepted"
    assert seed_row["provider_msg_id"] == "gmail-api-message-id"
    assert seed_row["placement"] == "unknown"
    assert "not been checked" in seed_row["details_redacted"]


def test_integration_preview_is_diff_only(client):
    contact = _create_contact(client)
    preview = client.post("/api/integrations/google_sheets/preview-sync", json={"contact_id": contact["id"]})
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert payload["status"] == "pending_confirmation"
    assert payload["diff"]["fields"]
    assert payload["diff"]["policy"]["dry_run_only"] is True
    assert payload["diff"]["policy"]["external_write_attempted_by_preview"] is False
    assert payload["diff"]["fields"][0]["local_field"]
    assert payload["diff"]["fields"][0]["external_field"]
    serialized = json.dumps(payload)
    assert "gsk_" not in serialized
    assert "AIza" not in serialized
    with SessionLocal() as db:
        assert db.query(SyncJournal).count() == 0
        assert db.query(ExternalWriteAttempt).count() == 0


def test_integration_preview_reuses_idempotent_pending_preview(client):
    contact = _create_contact(client, email="sheets-idempotent-preview@example.com")
    first = client.post("/api/integrations/google_sheets/preview-sync", json={"contact_id": contact["id"]})
    assert first.status_code == 200, first.text
    second = client.post("/api/integrations/google_sheets/preview-sync", json={"contact_id": contact["id"]})
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["idempotency_key"] == first.json()["idempotency_key"]
    with SessionLocal() as db:
        assert db.query(ExternalWritePreview).filter_by(idempotency_key=first.json()["idempotency_key"]).count() == 1


def test_integration_confirm_requires_existing_preview(client):
    confirm = client.post("/api/integrations/google_sheets/confirm-sync", json={"preview_id": "missing-preview"})
    assert confirm.status_code == 422
    assert "preview_not_found" in confirm.text
    with SessionLocal() as db:
        assert db.query(SyncJournal).count() == 0
        assert db.query(ExternalWriteAttempt).count() == 0


def test_integration_cancel_preview_blocks_confirmation(client):
    contact = _create_contact(client, email="cancel-preview@example.com")
    preview = client.post("/api/integrations/google_sheets/preview-sync", json={"contact_id": contact["id"]})
    assert preview.status_code == 200, preview.text
    preview_payload = preview.json()

    cancel = client.post("/api/integrations/google_sheets/cancel-sync", json={"preview_id": preview_payload["id"]})
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "cancelled"
    confirm = client.post("/api/integrations/google_sheets/confirm-sync", json={"preview_id": preview_payload["id"]})
    assert confirm.status_code == 422
    assert "preview_cancelled" in confirm.text
    with SessionLocal() as db:
        assert db.query(SyncJournal).count() == 0
        assert db.query(ExternalWriteAttempt).count() == 0


def test_integration_terminal_preview_can_be_recreated(client):
    contact = _create_contact(client, email="recreate-preview@example.com")
    first = client.post("/api/integrations/google_sheets/preview-sync", json={"contact_id": contact["id"]}).json()
    assert client.post(
        "/api/integrations/google_sheets/cancel-sync",
        json={"preview_id": first["id"]},
    ).status_code == 200

    second = client.post("/api/integrations/google_sheets/preview-sync", json={"contact_id": contact["id"]})

    assert second.status_code == 200
    assert second.json()["status"] == "pending_confirmation"
    assert second.json()["id"] != first["id"]
    assert second.json()["idempotency_key"] != first["idempotency_key"]


def test_integration_confirmation_rejects_stale_contact_diff(client):
    contact = _create_contact(client, email="stale-preview@example.com")
    preview = client.post(
        "/api/integrations/google_sheets/preview-sync",
        json={"contact_id": contact["id"]},
    ).json()
    assert client.patch(
        f"/api/contacts/{contact['id']}",
        json={"status": "conversation_active"},
    ).status_code == 200

    confirm = client.post(
        "/api/integrations/google_sheets/confirm-sync",
        json={"preview_id": preview["id"]},
    )

    assert confirm.status_code == 422
    assert "preview_stale" in confirm.text
    with SessionLocal() as db:
        assert db.get(ExternalWritePreview, preview["id"]).status == "stale"
        assert db.query(SyncJournal).filter_by(idempotency_key=preview["idempotency_key"]).count() == 0
        assert db.query(ExternalWriteAttempt).filter_by(idempotency_key=preview["idempotency_key"]).count() == 0


def test_integration_mapping_mismatch_blocks_preview(client):
    contact = _create_contact(client, email="mapping-mismatch@example.com")
    assert client.get("/api/integrations").status_code == 200
    with SessionLocal() as db:
        connection = db.query(IntegrationConnection).filter_by(provider="google_sheets").one()
        mapping = db.query(IntegrationMapping).filter_by(connection_id=connection.id, local_field="email").one()
        mapping.external_field = "Not A Sheet Column"
        db.commit()

    preview = client.post("/api/integrations/google_sheets/preview-sync", json={"contact_id": contact["id"]})
    assert preview.status_code == 422
    assert "mapping_mismatch" in preview.text
    with SessionLocal() as db:
        assert db.query(ExternalWritePreview).count() == 0
        assert db.query(SyncJournal).count() == 0
        assert db.query(ExternalWriteAttempt).count() == 0


def test_workflow_run_resumes_without_duplicate_outputs(client):
    _create_contact(client, email="workflow-one@example.com")
    _create_contact(client, email="workflow-two@example.com")
    workflows = client.get("/api/workflows")
    assert workflows.status_code == 200, workflows.text
    workflow_id = workflows.json()["items"][0]["id"]

    first = client.post(f"/api/workflows/{workflow_id}/run")
    assert first.status_code == 200, first.text
    with SessionLocal() as db:
        first_output_count = db.query(CellOutput).count()
    assert first_output_count > 0

    second = client.post(f"/api/workflows/{workflow_id}/run")
    assert second.status_code == 200, second.text
    with SessionLocal() as db:
        second_output_count = db.query(CellOutput).count()
    assert second_output_count == first_output_count

    detail = client.get(f"/api/workflows/{workflow_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["runs"][0]["status"] == "completed"
    assert detail.json()["attempts"]
    first_row = detail.json()["rows"][0]
    selected_cell = first_row["cells"]["selected_contact"]
    assert selected_cell["status"] == "completed"
    assert selected_cell["evidence_refs"]
    assert selected_cell["cost_units"] >= 0


def test_workflow_retry_reuses_completed_cell_outputs(client):
    _create_contact(client, email="workflow-retry-reuse@example.com")
    workflow_id = client.get("/api/workflows").json()["items"][0]["id"]
    first = client.post(f"/api/workflows/{workflow_id}/run")
    assert first.status_code == 200, first.text
    with SessionLocal() as db:
        first_output_count = db.query(CellOutput).count()
        first_attempt_count = db.query(WorkflowStepAttempt).count()

    retry = client.post(f"/api/workflows/{workflow_id}/retry-failed")
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "completed"
    with SessionLocal() as db:
        assert db.query(CellOutput).count() == first_output_count
        assert db.query(WorkflowStepAttempt).count() == first_attempt_count


def test_workflow_failed_run_resumes_from_failed_step(client):
    _create_contact(client, email="workflow-resume@example.com")
    workflow_id = client.get("/api/workflows").json()["items"][0]["id"]
    with SessionLocal() as db:
        column = db.query(WorkbookColumn).filter_by(workbook_id=workflow_id, key="company_evidence").one()
        column.config_json = json.dumps({"version": 1, "cost_units": 2, "force_error": True}, sort_keys=True)
        db.commit()

    failed = client.post(f"/api/workflows/{workflow_id}/run")
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "failed"
    with SessionLocal() as db:
        selected_count = db.query(CellOutput).join(WorkbookColumn, CellOutput.workbook_column_id == WorkbookColumn.id).filter(WorkbookColumn.key == "selected_contact").count()
        failed_attempt = db.query(WorkflowStepAttempt).filter_by(status="failed", error_code="FORCED_WORKFLOW_STEP_ERROR").first()
        assert selected_count == 1
        assert failed_attempt is not None
        column = db.query(WorkbookColumn).filter_by(workbook_id=workflow_id, key="company_evidence").one()
        column.config_json = json.dumps({"version": 2, "cost_units": 2}, sort_keys=True)
        db.commit()

    resumed = client.post(f"/api/workflows/{workflow_id}/retry-failed")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "completed"
    with SessionLocal() as db:
        assert db.query(CellOutput).join(WorkbookColumn, CellOutput.workbook_column_id == WorkbookColumn.id).filter(WorkbookColumn.key == "selected_contact").count() == selected_count
    detail = client.get(f"/api/workflows/{workflow_id}")
    assert detail.status_code == 200, detail.text
    row = detail.json()["rows"][0]
    assert row["cells"]["company_evidence"]["status"] == "completed"
    assert row["cells"]["draft_readiness"]["output"]["status"] == "neutral_preview"


def test_workflow_hashes_reuse_same_output_and_create_new_output_on_config_change(client):
    _create_contact(client, email="workflow-hash@example.com")
    workflow_id = client.get("/api/workflows").json()["items"][0]["id"]
    assert client.post(f"/api/workflows/{workflow_id}/run").status_code == 200
    with SessionLocal() as db:
        output_count = db.query(CellOutput).count()
        score_column = db.query(WorkbookColumn).filter_by(workbook_id=workflow_id, key="lead_score").one()
        original_hashes = {
            (row.input_hash, row.step_config_hash)
            for row in db.query(CellOutput).filter(CellOutput.workbook_column_id == score_column.id).all()
        }
    assert client.post(f"/api/workflows/{workflow_id}/run").status_code == 200
    with SessionLocal() as db:
        assert db.query(CellOutput).count() == output_count
        score_column = db.query(WorkbookColumn).filter_by(workbook_id=workflow_id, key="lead_score").one()
        score_column.config_json = json.dumps({"version": 2, "cost_units": 1}, sort_keys=True)
        db.commit()

    rerun = client.post(f"/api/workflows/{workflow_id}/run")
    assert rerun.status_code == 200, rerun.text
    with SessionLocal() as db:
        score_column = db.query(WorkbookColumn).filter_by(workbook_id=workflow_id, key="lead_score").one()
        changed_hashes = {
            (row.input_hash, row.step_config_hash)
            for row in db.query(CellOutput).filter(CellOutput.workbook_column_id == score_column.id).all()
        }
        assert original_hashes < changed_hashes


def test_workflow_run_lock_prevents_duplicate_worker_execution(client):
    _create_contact(client, email="workflow-lock@example.com")
    workflow_id = client.get("/api/workflows").json()["items"][0]["id"]
    with SessionLocal() as db:
        active = WorkflowRun(workbook_id=workflow_id, status="running", started_at=utcnow(), created_by="test")
        db.add(active)
        db.commit()
        active_id = active.id

    response = client.post(f"/api/workflows/{workflow_id}/run")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == active_id
    assert response.json()["status"] == "running"
    with SessionLocal() as db:
        assert db.query(CellOutput).count() == 0


def test_workflow_cost_cap_blocks_run_with_policy_trace(client):
    _create_contact(client, email="workflow-cost-cap@example.com")
    workflow_id = client.get("/api/workflows").json()["items"][0]["id"]
    response = client.post(f"/api/workflows/{workflow_id}/run", json={"cost_cap_units": 0})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"

    detail = client.get(f"/api/workflows/{workflow_id}")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    blocked_attempt = next(row for row in payload["attempts"] if row["status"] == "blocked")
    assert blocked_attempt["error_code"] == "WORKFLOW_COST_CAP_EXCEEDED"
    first_cell = payload["rows"][0]["cells"]["selected_contact"]
    assert first_cell["status"] == "blocked"
    assert first_cell["evidence_refs"]
    assert first_cell["output"]["cost_cap_units"] == 0


def test_workflow_tool_error_records_attempt_and_detail(client):
    _create_contact(client, email="workflow-tool-error@example.com")
    workflow_id = client.get("/api/workflows").json()["items"][0]["id"]
    with SessionLocal() as db:
        column = db.query(WorkbookColumn).filter_by(workbook_id=workflow_id, key="person_evidence").one()
        column.config_json = json.dumps({"version": 1, "cost_units": 2, "force_error": True}, sort_keys=True)
        db.commit()

    response = client.post(f"/api/workflows/{workflow_id}/run")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"
    detail = client.get(f"/api/workflows/{workflow_id}")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    failed_attempt = next(row for row in payload["attempts"] if row["status"] == "failed")
    assert failed_attempt["error_code"] == "FORCED_WORKFLOW_STEP_ERROR"
    assert payload["rows"][0]["cells"]["person_evidence"]["status"] == "failed"


def test_integration_confirm_writes_idempotent_journal(client):
    contact = _create_contact(client, email="crm-sync@example.com")
    preview = client.post("/api/integrations/google_sheets/preview-sync", json={"contact_id": contact["id"]})
    assert preview.status_code == 200, preview.text
    preview_payload = preview.json()

    confirm = client.post("/api/integrations/google_sheets/confirm-sync", json={"preview_id": preview_payload["id"]})
    assert confirm.status_code == 200, confirm.text
    journal_payload = confirm.json()
    assert journal_payload["status"] == "confirmed_dry_run"
    assert journal_payload["external_id"] is None
    assert journal_payload["idempotency_key"] == preview_payload["idempotency_key"]

    repeat = client.post("/api/integrations/google_sheets/confirm-sync", json={"preview_id": preview_payload["id"]})
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["id"] == journal_payload["id"]
    with SessionLocal() as db:
        assert db.query(SyncJournal).filter(SyncJournal.idempotency_key == preview_payload["idempotency_key"]).count() == 1
        attempt = db.query(ExternalWriteAttempt).filter(ExternalWriteAttempt.idempotency_key == preview_payload["idempotency_key"]).one()
        assert attempt.external_id is None
        assert json.loads(attempt.details_redacted)["simulated"] is True
        assert json.loads(attempt.details_redacted)["provider_contacted"] is False
        assert json.loads(attempt.details_redacted)["provider_accepted"] is False


def test_integration_provider_error_records_attempt_and_journal(client):
    contact = _create_contact(client, email="provider-error@example.com")
    assert client.get("/api/integrations").status_code == 200
    with SessionLocal() as db:
        connection = db.query(IntegrationConnection).filter_by(provider="hubspot").one()
        connection.status = "provider_error"
        db.commit()

    preview = client.post("/api/integrations/hubspot/preview-sync", json={"contact_id": contact["id"]})
    assert preview.status_code == 200, preview.text
    confirm = client.post("/api/integrations/hubspot/confirm-sync", json={"preview_id": preview.json()["id"]})
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["status"] == "provider_error"
    with SessionLocal() as db:
        preview_row = db.get(ExternalWritePreview, preview.json()["id"])
        assert preview_row.status == "failed"
        attempt = db.query(ExternalWriteAttempt).filter_by(idempotency_key=preview.json()["idempotency_key"]).one()
        assert attempt.status == "provider_error"
        assert attempt.error_code == "provider_error"
        journal = db.query(SyncJournal).filter_by(idempotency_key=preview.json()["idempotency_key"]).one()
        assert journal.status == "provider_error"


def test_integration_summary_redacts_token_values(client):
    assert client.get("/api/integrations").status_code == 200
    with SessionLocal() as db:
        connection = db.query(IntegrationConnection).filter_by(provider="salesforce").one()
        connection.scopes_redacted = "credential token=super-sensitive-value should be redacted"
        db.commit()

    summary = client.get("/api/integrations")
    assert summary.status_code == 200, summary.text
    serialized = json.dumps(summary.json())
    assert "super-sensitive-value" not in serialized
    assert "<redacted>" in serialized


def test_evidence_source_required_for_manual_fact(client):
    contact = _create_contact(client, email="source-required@example.com")

    response = client.post(
        f"/api/enrichment/contacts/{contact['id']}/facts",
        json={"field_key": "personalization_note", "field_value": "Runs a useful course platform."},
    )

    assert response.status_code == 422


def test_unsourced_personalization_blocks_draft_approval(client):
    configure_sender(client, canary_verified=True, dry_run=True)
    contact = _create_contact(client, email="unsupported-personalization@example.com")
    draft = _create_draft(client, contact["id"])

    approve = client.post(f"/api/drafts/{draft['id']}/approve")

    assert approve.status_code == 422, approve.text
    assert "UNSUPPORTED_PERSONALIZATION" in approve.text


def test_stale_fact_visible_not_usable(client):
    contact = _create_contact(client, email="stale-fact@example.com")
    fact_id = _insert_fact(contact["id"], status="active", expires_delta=timedelta(days=-1))

    response = client.post(
        f"/api/enrichment/contacts/{contact['id']}/evidence-check",
        json={"body": "Hi, I noticed your course platform and wanted to share an idea."},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["neutral_copy_required"] is True
    assert payload["supported_claims"] == []
    assert [fact["id"] for fact in payload["stale_facts"]] == [fact_id]
    assert payload["stale_facts"][0]["freshness"] == "stale"


def test_rejected_fact_visible_not_usable(client):
    contact = _create_contact(client, email="rejected-fact@example.com")
    fact_id = _insert_fact(contact["id"], status="rejected", expires_delta=timedelta(days=30))

    response = client.post(
        f"/api/enrichment/contacts/{contact['id']}/evidence-check",
        json={"body": "Hi, I noticed your course platform and wanted to share an idea."},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["neutral_copy_required"] is True
    assert payload["supported_claims"] == []
    assert [fact["id"] for fact in payload["stale_facts"]] == [fact_id]
    assert payload["stale_facts"][0]["freshness"] == "rejected"


def test_draft_evidence_check_persists_neutral_copy_requirement(client):
    contact = _create_contact(client, email="persist-check@example.com")
    draft = _create_draft(client, contact["id"])

    response = client.post(
        f"/api/enrichment/contacts/{contact['id']}/evidence-check",
        json={"draft_id": draft["id"]},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["neutral_copy_required"] is True
    assert payload["neutral_subject"]
    assert payload["neutral_body"]
    with SessionLocal() as db:
        row = db.query(DraftEvidenceCheck).filter(DraftEvidenceCheck.draft_id == draft["id"]).first()
        assert row is not None
        assert row.status == "neutral_required"
        assert row.neutral_copy_required is True


def test_source_snippet_redacted_and_bounded(client):
    contact = _create_contact(client, email="redacted-snippet@example.com")
    long_secret_snippet = (
        "gsk_test_secret_value AIzaFakeGoogleKey gAAAAverylongfernetvaluethatmustnotappear "
        "password: raw-password " + ("safe context " * 80)
    )

    response = client.post(
        f"/api/enrichment/contacts/{contact['id']}/facts",
        json={
            "field_key": "personalization_note",
            "field_value": "Uses password: raw-password and gsk_test_secret_value in copied notes.",
            "source_label": "operator_note",
            "raw_snippet": long_secret_snippet,
        },
    )

    assert response.status_code == 200, response.text
    evidence = client.get(f"/api/enrichment/contacts/{contact['id']}").json()
    rendered = json.dumps(evidence)
    assert "gsk_test_secret_value" not in rendered
    assert "AIzaFakeGoogleKey" not in rendered
    assert "gAAAAverylongfernetvalue" not in rendered
    assert "raw-password" not in rendered
    snippet = evidence["lead_facts"][0]["raw_snippet_redacted"]
    assert len(snippet) <= 240


def test_subject_personalization_requires_evidence(client):
    configure_sender(client, canary_verified=True, dry_run=True)
    contact = _create_contact(client, email="subject-evidence@example.com")
    draft = client.post(
        "/api/drafts",
        json={
            "contact_id": contact["id"],
            "subject": "I noticed your course platform",
            "body": "Hi, I wanted to share a practical outreach idea.",
        },
    ).json()

    approve = client.post(f"/api/drafts/{draft['id']}/approve")

    assert approve.status_code == 422
    assert "UNSUPPORTED_PERSONALIZATION" in approve.text


def test_generated_draft_uses_neutral_copy_when_evidence_missing(client, monkeypatch):
    monkeypatch.setenv("FINIMATIC_FAKE_AI", "1")
    configure_sender(client, canary_verified=True, dry_run=True)
    contact = _create_contact(client, email="neutral-generation@example.com")

    response = client.post("/api/drafts/generate", json={"contact_id": contact["id"], "provider": "groq"})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "I noticed your work" not in payload["body"]
    assert "Neutral copy required" in " ".join(payload["warnings"])


def test_generated_subject_personalization_uses_neutral_copy_when_evidence_missing(client, monkeypatch):
    class SubjectOnlyPersonalizationGateway:
        async def generate_draft(self, contact, provider, tone, length, instruction):
            return DraftSuggestion(
                subject="Personalized RAG chatbot for your content",
                body="Hi there,\n\nI wanted to share a practical outreach idea.\n\nBest regards",
                warnings=[],
            )

        def model_for_provider(self, provider):
            return "synthetic-test-model"

    contact = client.post(
        "/api/contacts",
        json={"email": "subject-fallback@example.com", "creator_name": "Ramesh", "source": "manual"},
    ).json()
    monkeypatch.setattr("app.drafts.router.build_gateway", lambda db: SubjectOnlyPersonalizationGateway())

    response = client.post("/api/drafts/generate", json={"contact_id": contact["id"], "provider": "gemini"})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["subject"] == "Practical outreach idea"
    assert "Neutral copy required" in " ".join(payload["warnings"])


def test_evidence_preview_checks_subject_and_body_together(client):
    contact = client.post(
        "/api/contacts",
        json={"email": "subject-preview@example.com", "creator_name": "Ramesh", "source": "manual"},
    ).json()

    response = client.post(
        f"/api/enrichment/contacts/{contact['id']}/evidence-check",
        json={
            "subject": "Personalized RAG chatbot for your content",
            "body": "Hi Ramesh, I wanted to share a practical outreach idea.",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["neutral_copy_required"] is True


def _insert_fact(contact_id: str, *, status: str, expires_delta: timedelta) -> str:
    now = utcnow()
    with SessionLocal() as db:
        source = EvidenceSource(
            publisher="verified_source",
            source_type="manual",
            retrieved_at=now,
            reliability_tier="operator_supplied",
            allowed_use="draft_context",
            raw_excerpt_redacted="Verified source excerpt.",
        )
        db.add(source)
        db.flush()
        fact = LeadFact(
            contact_id=contact_id,
            field_key="personalization_note",
            field_value="Runs a course platform.",
            source_id=source.id,
            source_label="verified_source",
            source_type="manual",
            confidence=0.8,
            status=status,
            fetched_at=now,
            expires_at=now + expires_delta,
            extractor="test",
            raw_snippet_redacted="Runs a course platform.",
        )
        db.add(fact)
        db.commit()
        return fact.id
