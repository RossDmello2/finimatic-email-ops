from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models import Contact, Draft, Reply, SendAttempt


CONTEXT_STALE_MINUTES = 30


def build_context_card(db: Session) -> str:
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    last_24h = now - timedelta(hours=24)

    sent_today = db.query(SendAttempt).filter(SendAttempt.provider_accepted.is_(True), SendAttempt.sent_at >= today_start).count()
    new_replies = db.query(Reply).filter(Reply.received_at >= last_24h).count()
    needs_response = (
        db.query(Contact)
        .filter(Contact.deleted_at.is_(None), Contact.status.in_(["replied", "conversation_active"]))
        .order_by(Contact.updated_at.desc())
        .limit(5)
        .all()
    )
    names = [contact.creator_name or contact.business_name or contact.email.split("@")[0] for contact in needs_response]
    pending_drafts = db.query(Draft).filter(Draft.approved.is_(False)).count()
    optouts_today = db.query(Reply).filter(Reply.classified_as == "unsubscribe", Reply.received_at >= today_start).count()

    response_names = ", ".join(names[:3]) if names else "None"
    if len(names) > 3:
        response_names += f" +{len(names) - 3} more"
    card = (
        f"TODAY: {sent_today} sent, {new_replies} new replies | "
        f"NEEDS RESPONSE: {response_names} | "
        f"PENDING APPROVALS: {pending_drafts} drafts | "
        f"OPT-OUTS TODAY: {optouts_today}"
    )
    return card[:500]


def generate_proactive_opening(context_card: str, db: Session) -> str:
    attention_items: list[str] = []

    needs_response = _segment(context_card, "NEEDS RESPONSE:")
    if needs_response and needs_response.lower() != "none":
        attention_items.append(f"{needs_response} may need a response")

    pending = _leading_int(_segment(context_card, "PENDING APPROVALS:"))
    if pending:
        attention_items.append(f"{pending} draft{'s' if pending != 1 else ''} waiting for approval")

    optouts = _leading_int(_segment(context_card, "OPT-OUTS TODAY:"))
    if optouts:
        attention_items.append(f"{optouts} opt-out{'s' if optouts != 1 else ''} today")

    if attention_items:
        return f"Welcome back. A few things need your attention: {'; '.join(attention_items)}. What would you like to do?"
    return "Your campaign is running smoothly. What would you like to do — check replies, generate a draft, or send an update?"


def is_context_stale(loaded_at_iso: str | None) -> bool:
    if not loaded_at_iso:
        return True
    try:
        loaded_at = datetime.fromisoformat(loaded_at_iso)
    except Exception:
        return True
    if loaded_at.tzinfo is not None:
        loaded_at = loaded_at.replace(tzinfo=None)
    return (datetime.utcnow() - loaded_at).total_seconds() > CONTEXT_STALE_MINUTES * 60


def _segment(card: str, label: str) -> str:
    if label not in card:
        return ""
    return card.split(label, 1)[1].split("|", 1)[0].strip()


def _leading_int(value: str) -> int:
    try:
        return int(value.split()[0])
    except Exception:
        return 0
