import json
from datetime import timedelta

from app.core.time import utcnow
from app.db.models import Contact, Draft, FollowUpSequence, SendQueue
from app.db.session import SessionLocal


def _create_contact(client, email="delete-me@example.com"):
    response = client.post("/api/contacts", json={"email": email, "creator_name": "Delete Me", "source": "manual"})
    assert response.status_code == 200
    return response.json()


def test_contact_delete_moves_to_recently_deleted_and_restore_returns_it(client):
    contact = _create_contact(client)

    deleted = client.delete(f"/api/contacts/{contact['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted_at"]

    active = client.get("/api/contacts").json()
    assert active["total"] == 0

    recently_deleted = client.get("/api/contacts/recently-deleted").json()
    assert recently_deleted["total"] == 1
    assert recently_deleted["items"][0]["email"] == contact["email"]

    restored = client.post(f"/api/contacts/{contact['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None

    active = client.get("/api/contacts").json()
    assert active["total"] == 1
    assert active["items"][0]["email"] == contact["email"]
    assert client.get("/api/contacts/recently-deleted").json()["total"] == 0


def test_recently_deleted_only_shows_last_seven_days(client):
    contact = _create_contact(client)
    assert client.delete(f"/api/contacts/{contact['id']}").status_code == 200

    with SessionLocal() as db:
        row = db.get(Contact, contact["id"])
        row.deleted_at = utcnow() - timedelta(days=8)
        db.commit()

    assert client.get("/api/contacts").json()["total"] == 0
    assert client.get("/api/contacts/recently-deleted").json()["total"] == 0


def test_contact_delete_cancels_pending_queue_and_due_followups(client):
    contact = _create_contact(client)
    draft = client.post(
        "/api/drafts",
        json={"contact_id": contact["id"], "subject": "Hello", "body": "Body"},
    ).json()
    with SessionLocal() as db:
        stored_draft = db.get(Draft, draft["id"])
        stored_draft.approved = True
        stored_draft.approved_at = utcnow()
        db.commit()
    queue = client.post(
        "/api/queue",
        json={"contact_id": contact["id"], "draft_id": draft["id"], "sequence_num": 1},
    ).json()

    with SessionLocal() as db:
        db.add(FollowUpSequence(contact_id=contact["id"], sequence_num=2, due_at=utcnow(), status="due"))
        db.add(FollowUpSequence(contact_id=contact["id"], sequence_num=3, due_at=utcnow(), status="pending_approval"))
        db.commit()

    response = client.delete(f"/api/contacts/{contact['id']}")
    assert response.status_code == 200

    with SessionLocal() as db:
        queue_row = db.get(SendQueue, queue["id"])
        assert queue_row.status == "cancelled"
        assert json.loads(queue_row.policy_block_reasons) == ["CONTACT_DELETED"]
        followups = db.query(FollowUpSequence).filter(FollowUpSequence.contact_id == contact["id"]).all()
        assert {followup.sequence_num: followup.status for followup in followups} == {2: "stopped", 3: "stopped"}
        assert all(followup.stop_reason == "CONTACT_DELETED" for followup in followups)
