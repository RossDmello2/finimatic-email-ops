from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import Draft, FollowUpSequence, SendAttempt, SendQueue
from app.db.session import SessionLocal
from app.followups.service import process_due_followups
from app.send.sequence import schedule_next_sequence_after_acceptance


def _contact(client, email: str, tags: str = "phase17-campaign") -> dict:
    return client.post(
        "/api/contacts",
        json={"email": email, "creator_name": "Campaign Contact", "source": "manual", "tags": tags},
    ).json()


def _campaign(client, *, step_2_delay: int = 0, step_3_delay: int = 2) -> dict:
    campaign = client.post(
        "/api/campaigns",
        json={"name": "Phase 17 sequence", "goal": "Exercise campaign sequence", "target_tags": "phase17-campaign"},
    ).json()
    response = client.patch(
        f"/api/campaigns/{campaign['id']}",
        json={
            "step_1_draft": {
                "subject": "Initial {{first_name}}",
                "body": "Initial campaign body",
                "purpose": "initial outreach",
                "delay_days": 0,
            },
            "step_2_draft": {
                "subject": "Second {{first_name}}",
                "body": "Campaign step two",
                "purpose": "value-add follow-up",
                "delay_days": step_2_delay,
            },
            "step_3_draft": {
                "subject": "Final {{first_name}}",
                "body": "Campaign step three",
                "purpose": "polite breakup email",
                "delay_days": step_3_delay,
            },
        },
    )
    assert response.status_code == 200
    return response.json()


def _accepted_queue(db, draft: Draft, sequence_num: int, accepted_at: datetime) -> tuple[SendQueue, SendAttempt]:
    queue = SendQueue(
        contact_id=draft.contact_id,
        draft_id=draft.id,
        sequence_num=sequence_num,
        scheduled_at=accepted_at,
        idempotency_key=f"phase17:{draft.id}:{sequence_num}",
        status="provider_accepted",
    )
    db.add(queue)
    db.flush()
    attempt = SendAttempt(
        queue_id=queue.id,
        contact_id=draft.contact_id,
        draft_id=draft.id,
        idempotency_key=queue.idempotency_key,
        provider_msg_id=f"test-provider-{draft.id}",
        tracking_message_id=f"attempt:{draft.id}",
        configured_transport="test_provider",
        effective_transport="test_provider",
        transport_source="test fixture",
        simulated=False,
        provider_contacted=True,
        provider_accepted=True,
        provider_response_classification="test_provider_accepted",
        status="provider_accepted",
        sender_identity="phase11-sender@finimatic.test",
        sent_at=accepted_at,
    )
    db.add(attempt)
    db.flush()
    return queue, attempt


def test_campaign_activation_validates_all_steps_and_creates_only_step_one(client):
    contact = _contact(client, "phase17-activate@recipient.test")
    campaign = client.post(
        "/api/campaigns",
        json={"name": "Incomplete", "goal": "Validate every step", "target_tags": "phase17-campaign"},
    ).json()
    client.patch(
        f"/api/campaigns/{campaign['id']}",
        json={"step_1_draft": {"subject": "Initial", "body": "Body", "delay_days": 0}},
    )

    incomplete = client.post(f"/api/campaigns/{campaign['id']}/activate")
    assert incomplete.status_code == 422
    assert incomplete.json()["detail"] == "campaign_step_2_incomplete"

    campaign = _campaign(client)
    first = client.post(f"/api/campaigns/{campaign['id']}/activate")
    second = client.post(f"/api/campaigns/{campaign['id']}/activate")
    assert first.status_code == 200
    assert first.json()["drafts_created"] == 1
    assert second.json()["drafts_created"] == 0
    with SessionLocal() as db:
        drafts = db.query(Draft).filter_by(contact_id=contact["id"]).all()
        assert [draft.notes for draft in drafts] == [f"campaign:{campaign['id']}:step1"]


def test_durable_acceptance_schedules_campaign_content_once_with_relative_delay(client):
    contact = _contact(client, "phase17-advance@recipient.test")
    campaign = _campaign(client, step_2_delay=0, step_3_delay=2)
    assert client.post(f"/api/campaigns/{campaign['id']}/activate").status_code == 200
    accepted_at = datetime.now(timezone.utc) - timedelta(minutes=1)

    with SessionLocal() as db:
        step_one = db.query(Draft).filter_by(contact_id=contact["id"]).one()
        queue_one, attempt_one = _accepted_queue(db, step_one, 1, accepted_at)
        handled, sequence_two = schedule_next_sequence_after_acceptance(db, queue_one, attempt_one)
        duplicate_handled, duplicate_two = schedule_next_sequence_after_acceptance(db, queue_one, attempt_one)
        db.commit()
        assert handled is True
        assert duplicate_handled is True
        assert duplicate_two.id == sequence_two.id
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=2).count() == 1

    with SessionLocal() as db:
        result = process_due_followups(db)
    assert result["processed"] == 1
    with SessionLocal() as db:
        sequence_two = db.query(FollowUpSequence).filter_by(contact_id=contact["id"], sequence_num=2).one()
        step_two = db.get(Draft, sequence_two.pending_draft_id)
        assert sequence_two.status == "pending_approval"
        assert step_two.subject == "Second Campaign"
        assert step_two.body == "Campaign step two"
        assert step_two.notes == f"campaign:{campaign['id']}:step2"
        assert step_two.approved is False

        queue_two, attempt_two = _accepted_queue(db, step_two, 2, accepted_at)
        handled, sequence_three = schedule_next_sequence_after_acceptance(db, queue_two, attempt_two)
        db.commit()
        assert handled is True
        due_at = sequence_three.due_at
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        assert due_at == accepted_at + timedelta(days=2)


def test_campaign_advance_requires_matching_provider_acceptance(client):
    contact = _contact(client, "phase17-reject@recipient.test")
    campaign = _campaign(client)
    client.post(f"/api/campaigns/{campaign['id']}/activate")
    with SessionLocal() as db:
        draft = db.query(Draft).filter_by(contact_id=contact["id"]).one()
        queue, attempt = _accepted_queue(db, draft, 1, datetime.now(timezone.utc))
        attempt.provider_accepted = False
        attempt.status = "failed"
        db.flush()
        try:
            schedule_next_sequence_after_acceptance(db, queue, attempt)
        except ValueError as exc:
            assert str(exc) == "durable_provider_acceptance_required"
        else:
            raise AssertionError("campaign advanced without durable provider acceptance")
        assert db.query(FollowUpSequence).filter_by(contact_id=contact["id"]).count() == 0


def test_pause_fence_stops_only_campaign_work_and_blocks_future_advance(client):
    contact = _contact(client, "phase17-pause@recipient.test")
    campaign = _campaign(client)
    client.post(f"/api/campaigns/{campaign['id']}/activate")
    accepted_at = datetime.now(timezone.utc)
    with SessionLocal() as db:
        campaign_draft = db.query(Draft).filter_by(contact_id=contact["id"]).one()
        queue, attempt = _accepted_queue(db, campaign_draft, 1, accepted_at)
        handled, campaign_followup = schedule_next_sequence_after_acceptance(db, queue, attempt)
        direct_draft = Draft(
            contact_id=contact["id"],
            subject="Direct subject",
            body="Direct body",
            notes="direct_send",
            approved=False,
        )
        db.add(direct_draft)
        db.flush()
        direct_followup = FollowUpSequence(
            contact_id=contact["id"],
            draft_id=direct_draft.id,
            sequence_num=3,
            due_at=accepted_at + timedelta(days=1),
            status="due",
        )
        db.add(direct_followup)
        db.commit()
        campaign_followup_id = campaign_followup.id
        direct_followup_id = direct_followup.id
        assert handled is True

    paused = client.patch(f"/api/campaigns/{campaign['id']}", json={"status": "paused"})
    assert paused.status_code == 200
    with SessionLocal() as db:
        assert db.get(FollowUpSequence, campaign_followup_id).status == "stopped"
        assert db.get(FollowUpSequence, direct_followup_id).status == "due"
        queue = db.query(SendQueue).filter_by(contact_id=contact["id"], sequence_num=1).one()
        attempt = db.query(SendAttempt).filter_by(queue_id=queue.id).one()
        handled, future = schedule_next_sequence_after_acceptance(db, queue, attempt)
        assert handled is True
        assert future is None
