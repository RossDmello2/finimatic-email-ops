from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Literal

from groq import Groq
from sqlalchemy.orm import Session

from app.db.models import Contact, ConversationMessage, Draft, FollowUpSequence, Reply, SendAttempt, SendQueue, Setting, Suppression
from app.settings.service import get_key_list
from app.agent.tools import sanitize_text


REPLY_PLAIN = {
    "reply": "replied",
    "unsubscribe": "asked to be removed",
    "bounce": "email bounced",
    "auto_reply": "auto out-of-office",
    "complaint": "marked as spam",
    "unknown": "unclear response",
    "positive_interest": "interested",
    "question": "asked a question",
    "objection": "had a concern",
}

STATUS_PLAIN = {
    "imported": "new, not yet emailed",
    "needs_review": "needs review",
    "draft_needed": "needs a draft",
    "draft_ready": "draft ready",
    "approved": "draft approved",
    "queued": "email queued",
    "sent": "email sent",
    "replied": "replied and may need your response",
    "conversation_active": "replied and needs your response",
    "bounced": "email bounced",
    "unsubscribed": "opted out",
    "suppressed": "opted out",
    "manually_paused": "paused by you",
    "blocked_by_policy": "blocked by a sending rule",
    "follow_up_due": "follow-up due",
    "follow_up_stopped": "no longer receiving follow-ups",
    "complete": "campaign complete",
}

CAMPAIGN_INTELLIGENCE_SYSTEM = """You answer questions about a cold email campaign using only the provided campaign snapshot.

Rules:
- Answer in conversational plain English
- NEVER output a database ID
- NEVER output an ISO timestamp
- NEVER say I cannot perform that for a read question
- If the answer is a number, state it first
- Maximum 5 items in any list
- Use short lines and numbered lists when naming contacts
- Distinguish clearly between replies received from contacts and replies sent by me
- Never invent contacts, email addresses, counts, or timestamps not present in the snapshot
- Start with the actual answer, not a preamble"""


def humanize_dt(dt: datetime | None) -> str:
    if dt is None:
        return "unknown time"
    value = dt.replace(tzinfo=None) if dt.tzinfo else dt
    delta = datetime.utcnow() - value
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    if seconds < 172800:
        return "yesterday"
    days = seconds // 86400
    return f"{days} days ago"


def _display_name(contact: Contact) -> str:
    return contact.creator_name or contact.business_name or contact.email.split("@")[0]


def _setting(db: Session, key: str, default: str) -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    return str(row.value) if row and row.value is not None else default


def _reply_line(reply: Reply, contact: Contact) -> str:
    classification = REPLY_PLAIN.get(reply.classified_as, reply.classified_as.replace("_", " "))
    when = humanize_dt(reply.received_at or reply.created_at)
    summary = _clean_message_preview(reply.raw_summary or "", limit=160)
    suffix = f" - {summary}" if summary else ""
    return f"  - {_display_name(contact)} ({contact.email}): {classification}, {when}{suffix}"


def _clean_message_preview(text: str, limit: int = 160) -> str:
    cleaned = sanitize_text(text or "", limit=800).strip()
    cleaned = (
        cleaned.replace("â", "-")
        .replace("â", "-")
        .replace("â", "'")
        .replace("â", '"')
        .replace("â", '"')
    )
    for source, replacement in {
        "\u2014": "-",
        "\u2013": "-",
        "\u2019": "'",
        "\u2018": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00e2\u0080\u0094": "-",
        "\u00e2\u0080\u0093": "-",
        "\u00e2\u0080\u0099": "'",
        "\u00e2\u0080\u0098": "'",
        "\u00e2\u0080\u009c": '"',
        "\u00e2\u0080\u009d": '"',
    }.items():
        cleaned = cleaned.replace(source, replacement)
    cleaned = re.split(r"\s+On\s+.{0,160}?\bwrote:\s*", cleaned, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    cleaned = re.sub(r"\s+>.*$", "", cleaned).strip()
    return sanitize_text(cleaned, limit=limit).strip()


def _conversation_line(message: ConversationMessage, contact: Contact, label: str) -> str:
    when = humanize_dt(message.occurred_at or message.created_at)
    subject = sanitize_text(message.subject or "", limit=80).strip()
    body = _clean_message_preview(message.body or "", limit=160)
    preview = subject or body
    suffix = f"\n   {label}: {preview}" if preview else ""
    return f"{_display_name(contact)} ({contact.email}) - {when}{suffix}"


def _question_window(question: str, default_hours: int = 72) -> tuple[datetime, str]:
    lowered = question.lower()
    match = re.search(r"(?:last|past)\s+(\d+)\s*(minute|minutes|hour|hours|day|days)", lowered)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("minute"):
            return datetime.utcnow() - timedelta(minutes=amount), f"the last {amount} minute{'s' if amount != 1 else ''}"
        if unit.startswith("hour"):
            return datetime.utcnow() - timedelta(hours=amount), f"the last {amount} hour{'s' if amount != 1 else ''}"
        return datetime.utcnow() - timedelta(days=amount), f"the last {amount} day{'s' if amount != 1 else ''}"
    if any(word in lowered for word in ("today", "tonight")):
        now = datetime.utcnow()
        return now.replace(hour=0, minute=0, second=0, microsecond=0), "today"
    if any(word in lowered for word in ("recent", "recently", "latest", "new")):
        return datetime.utcnow() - timedelta(hours=default_hours), f"the last {default_hours} hours"
    return datetime.utcnow() - timedelta(hours=default_hours), f"the last {default_hours} hours"


def _is_outbound_reply_question(question: str) -> bool:
    lowered = question.lower()
    outbound_terms = (
        "i replied",
        "i have replied",
        "have i replied",
        "whom all have i replied",
        "who have i replied",
        "who did i reply",
        "who all did i reply",
        "who did i send",
        "who all did i send",
        "whom did i send",
        "which contacts did i send",
        "whom did i reply",
        "i responded",
        "i have responded",
        "who have i responded",
        "who did i respond",
        "messages i sent",
        "i sent",
    )
    return any(term in lowered for term in outbound_terms)


def _is_inbound_reply_question(question: str) -> bool:
    lowered = question.lower()
    if _is_outbound_reply_question(question):
        return False
    inbound_terms = (
        "who all replied",
        "who replied",
        "who has replied",
        "have i received any reply",
        "have i received replies",
        "received any reply",
        "received any replies",
        "replies recently",
        "reply recently",
        "new replies",
        "latest replies",
    )
    return any(term in lowered for term in inbound_terms)


def _direct_outbound_answer(question: str, db: Session) -> str | None:
    if not _is_outbound_reply_question(question):
        return None
    since, label = _question_window(question, default_hours=24)
    since_naive = since.replace(tzinfo=None)
    rows = (
        db.query(ConversationMessage, Contact)
        .join(Contact, Contact.id == ConversationMessage.contact_id)
        .filter(
            Contact.deleted_at.is_(None),
            ConversationMessage.direction == "outbound",
            ConversationMessage.occurred_at >= since_naive,
        )
        .order_by(ConversationMessage.occurred_at.desc(), ConversationMessage.created_at.desc())
        .limit(30)
        .all()
    )
    latest_by_contact: dict[str, tuple[ConversationMessage, Contact]] = {}
    for message, contact in rows:
        latest_by_contact.setdefault(contact.id, (message, contact))
    items = list(latest_by_contact.values())
    if not items:
        return f"0 contacts - I do not see any replies you sent in {label} for this campaign."
    shown = items[:5]
    lines = [f"{len(items)} contact{'s' if len(items) != 1 else ''} you replied to in {label}:"]
    lines.extend(f"{index}. {_conversation_line(message, contact, 'You wrote')}" for index, (message, contact) in enumerate(shown, start=1))
    if len(items) > len(shown):
        lines.append(f"...and {len(items) - len(shown)} more.")
    return "\n".join(lines)


def _direct_inbound_answer(question: str, db: Session) -> str | None:
    if not _is_inbound_reply_question(question):
        return None
    since, label = _question_window(question, default_hours=72)
    since_naive = since.replace(tzinfo=None)
    rows = (
        db.query(Reply, Contact)
        .join(Contact, Contact.id == Reply.contact_id)
        .filter(Contact.deleted_at.is_(None), Reply.received_at >= since_naive)
        .order_by(Reply.received_at.desc(), Reply.created_at.desc())
        .limit(30)
        .all()
    )
    latest_by_contact: dict[str, tuple[Reply, Contact]] = {}
    for reply, contact in rows:
        latest_by_contact.setdefault(contact.id, (reply, contact))
    items = list(latest_by_contact.values())
    if not items:
        return f"0 contacts - I do not see any replies received in {label} for this campaign."
    shown = items[:5]
    lines = [f"{len(items)} contact{'s' if len(items) != 1 else ''} replied in {label}:"]
    for index, (reply, contact) in enumerate(shown, start=1):
        classification = REPLY_PLAIN.get(reply.classified_as, reply.classified_as.replace("_", " "))
        when = humanize_dt(reply.received_at or reply.created_at)
        summary = _clean_message_preview(reply.raw_summary or "", limit=160)
        lines.append(f"{index}. {_display_name(contact)} ({contact.email}) - {classification}, {when}")
        if summary:
            lines.append(f"   They wrote: {summary}")
    if len(items) > len(shown):
        lines.append(f"...and {len(items) - len(shown)} more.")
    return "\n".join(lines)


def _parse_reasons(raw: str | None) -> str:
    if not raw:
        return "unknown reason"
    try:
        parsed = json.loads(raw)
    except Exception:
        return sanitize_text(raw, limit=180)
    if isinstance(parsed, list):
        return ", ".join(sanitize_text(str(item), limit=80) for item in parsed[:4]) or "unknown reason"
    return sanitize_text(str(parsed), limit=180)


def build_campaign_snapshot(db: Session) -> str:
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    recent_start = now - timedelta(hours=72)

    contacts = db.query(Contact).filter(Contact.deleted_at.is_(None)).all()
    status_counts: dict[str, int] = {}
    for contact in contacts:
        status_counts[contact.status] = status_counts.get(contact.status, 0) + 1
    status_breakdown = ", ".join(
        f"{STATUS_PLAIN.get(status, status.replace('_', ' '))}: {count}"
        for status, count in sorted(status_counts.items(), key=lambda item: (-item[1], item[0]))
    )

    all_reply_rows = (
        db.query(Reply, Contact)
        .join(Contact, Contact.id == Reply.contact_id)
        .filter(Contact.deleted_at.is_(None))
        .order_by(Reply.received_at.desc(), Reply.created_at.desc())
        .limit(30)
        .all()
    )
    recent_reply_rows = (
        db.query(Reply, Contact)
        .join(Contact, Contact.id == Reply.contact_id)
        .filter(Contact.deleted_at.is_(None), Reply.received_at >= recent_start)
        .order_by(Reply.received_at.desc(), Reply.created_at.desc())
        .limit(30)
        .all()
    )
    needs_response = (
        db.query(Contact)
        .filter(Contact.deleted_at.is_(None), Contact.status.in_(["replied", "conversation_active"]))
        .order_by(Contact.updated_at.desc())
        .limit(10)
        .all()
    )

    today_sends = db.query(SendAttempt).filter(SendAttempt.status == "success", SendAttempt.sent_at >= today_start).count()
    week_sends = db.query(SendAttempt).filter(SendAttempt.status == "success", SendAttempt.sent_at >= week_start).count()
    pending_queue = db.query(SendQueue).filter(SendQueue.status == "pending").count()
    blocked_queue = db.query(SendQueue).filter(SendQueue.status == "blocked").count()
    blocked_rows = (
        db.query(SendQueue, Contact)
        .join(Contact, Contact.id == SendQueue.contact_id)
        .filter(Contact.deleted_at.is_(None), SendQueue.status == "blocked")
        .limit(10)
        .all()
    )
    followups_due = db.query(FollowUpSequence).filter(FollowUpSequence.status == "due").count()
    followups_stopped = db.query(FollowUpSequence).filter(FollowUpSequence.status == "stopped").count()
    pending_drafts = db.query(Draft).filter(Draft.approved.is_(False)).count()
    approved_drafts = db.query(Draft).filter(Draft.approved.is_(True)).count()
    daily_cap = _setting(db, "daily_send_cap", "50")
    dry_run = _setting(db, "dry_run", "false").lower() == "true"
    canary_verified = _setting(db, "canary_verified", "false").lower() == "true"
    mode = "DRY-RUN" if dry_run else ("LIVE" if canary_verified else "CANARY")
    not_emailed = db.query(Contact).filter(Contact.deleted_at.is_(None), Contact.status.in_(["imported", "needs_review", "draft_needed"])).count()
    suppression_count = db.query(Suppression).count()

    lines = [
        f"TOTAL CONTACTS: {len(contacts)} total",
        f"STATUS BREAKDOWN: {status_breakdown or 'no contacts yet'}",
        "ALL REPLIES:",
    ]
    lines.extend(_reply_line(reply, contact) for reply, contact in all_reply_rows)
    if not all_reply_rows:
        lines.append("  No replies yet.")

    lines.append("RECENT REPLIES (last 72 hours):")
    lines.extend(_reply_line(reply, contact) for reply, contact in recent_reply_rows)
    if not recent_reply_rows:
        lines.append("  No recent replies.")

    names = ", ".join(f"{_display_name(contact)} ({contact.email})" for contact in needs_response)
    lines.extend(
        [
            "CONTACTS WHO REPLIED AND MAY NEED RESPONSE:",
            f"  {names if names else 'None'}",
            f"TODAY'S SENDING: {today_sends} sent",
            f"THIS WEEK'S SENDING: {week_sends} sent",
            f"QUEUE STATUS: {pending_queue} pending, {blocked_queue} blocked",
            "BLOCKED QUEUE DETAILS:",
        ]
    )
    if blocked_rows:
        lines.extend(f"  - {_display_name(contact)}: {_parse_reasons(queue.policy_block_reasons)}" for queue, contact in blocked_rows)
    else:
        lines.append("  None")

    lines.extend(
        [
            f"FOLLOW-UPS: {followups_due} due, {followups_stopped} stopped",
            f"DRAFTS: {pending_drafts} pending approval, {approved_drafts} approved",
            f"DAILY CAP: {daily_cap}",
            f"MODE: {mode}",
            f"CONTACTS NOT YET EMAILED: {not_emailed}",
            f"SUPPRESSIONS: {suppression_count} opted-out addresses",
        ]
    )
    return "\n".join(lines)[:3200]


async def answer_awareness_query(
    question: str,
    db: Session,
    session_context: str = "",
    turn_history: list | None = None,
) -> str:
    try:
        snapshot = build_campaign_snapshot(db)
    except Exception as exc:
        snapshot = f"TOTAL CONTACTS: unknown\nSTATUS BREAKDOWN: unavailable\nSNAPSHOT ERROR: {type(exc).__name__}"

    direct_answer = _direct_outbound_answer(question, db) or _direct_inbound_answer(question, db)
    if direct_answer:
        return direct_answer

    turn_context = ""
    if turn_history:
        recent = turn_history[-6:]
        turn_context = "\n".join(
            f"{item.get('role', 'turn')}: {str(item.get('text', ''))[:150]}"
            for item in recent
            if isinstance(item, dict)
        )

    prompt_parts = [f"CAMPAIGN SNAPSHOT:\n{snapshot}"]
    if session_context:
        prompt_parts.append(f"SESSION CONTEXT:\n{session_context[:300]}")
    if turn_context:
        prompt_parts.append(f"RECENT TURNS:\n{turn_context}")
    prompt_parts.append(f"USER QUESTION: {question}")

    try:
        response = await _call_with_fallback(
            system=CAMPAIGN_INTELLIGENCE_SYSTEM,
            user="\n\n".join(prompt_parts),
            db=db,
            max_tokens=500,
            temperature=0.3,
        )
    except Exception:
        return _safe_fallback_summary(snapshot)
    return _limit_numbered_list(_grounding_check(response, snapshot))


async def _call_with_fallback(
    *,
    system: str,
    user: str,
    db: Session,
    max_tokens: int,
    temperature: float,
) -> str:
    try:
        return await _call_single_provider("gemini", "gemini-2.0-flash", system, user, db, max_tokens, temperature)
    except Exception:
        return await _call_single_provider("groq", "llama-3.3-70b-versatile", system, user, db, max_tokens, temperature)


async def _call_single_provider(
    provider: Literal["gemini", "groq"],
    model: str,
    system: str,
    user: str,
    db: Session,
    max_tokens: int,
    temperature: float,
) -> str:
    loop = asyncio.get_event_loop()
    if provider == "gemini":
        keys = get_key_list(db, "gemini_keys")
        if not keys:
            raise ValueError("missing_gemini_keys")

        def _call():
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=keys[0])
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=user,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        max_output_tokens=max_tokens,
                        temperature=temperature,
                    ),
                )
                return response.text or ""
            finally:
                close = getattr(client, "close", None)
                if close:
                    close()

        return await loop.run_in_executor(None, _call)

    keys = get_key_list(db, "groq_keys")
    if not keys:
        raise ValueError("missing_groq_keys")
    client = Groq(api_key=keys[0])

    def _call():
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    return await loop.run_in_executor(None, _call)


def _grounding_check(response: str, snapshot: str) -> str:
    for email in re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", response):
        if email not in snapshot:
            return _safe_fallback_summary(snapshot)
    return sanitize_text(response, limit=2000)


def _safe_fallback_summary(snapshot: str) -> str:
    lines = [line.strip() for line in snapshot.splitlines() if line.strip()]
    if not lines:
        return "No campaign details are available yet."
    total = next((line for line in lines if line.startswith("TOTAL CONTACTS:")), "")
    status = next((line for line in lines if line.startswith("STATUS BREAKDOWN:")), "")
    reply_items = [line.lstrip("- ").strip() for line in lines if line.startswith("- ") or line.startswith("  - ")]
    response: list[str] = []
    if total:
        response.append(total.replace("TOTAL CONTACTS:", "Total contacts:").strip())
    if status:
        response.append(status.replace("STATUS BREAKDOWN:", "Status:").strip())
    if reply_items:
        response.append("Recent reply details:")
        response.extend(f"{index}. {item}" for index, item in enumerate(reply_items[:3], start=1))
    if not response:
        response = [line for line in lines if not line.endswith(":")][:3]
    return "\n".join(response)


def _limit_numbered_list(response: str, max_items: int = 5) -> str:
    lines = response.splitlines()
    numbered_indexes = [index for index, line in enumerate(lines) if re.match(r"\s*\d+\.", line)]
    bullet_indexes = [index for index, line in enumerate(lines) if re.match(r"\s*-\s+\S", line)]
    list_indexes = numbered_indexes if len(numbered_indexes) > max_items else bullet_indexes
    if len(list_indexes) <= max_items:
        return response
    keep = set(list_indexes[:max_items])
    hidden = len(list_indexes) - max_items
    result: list[str] = []
    inserted = False
    for index, line in enumerate(lines):
        if index in list_indexes and index not in keep:
            if not inserted:
                result.append(f"...and {hidden} more.")
                inserted = True
            continue
        result.append(line)
    return "\n".join(result)
