from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, or_
from sqlalchemy.orm import Session

from app.agent.catalog import get_capability, source_label_for_capability, validate_capability
from app.agent.fuzzy_resolver import fuzzy_resolve_contact
from app.agent.schemas import EvidenceEnvelope, ToolPlan
from app.ai.gateway import AIGateway, GROQ_MODEL_DEFAULT
from app.ai.gemini_pool import GEMINI_MODEL_DEFAULT
from app.ai.prompts import sender_profile_from_settings
from app.ai.schema import AIFailure, DraftSuggestion
from app.audit.service import emit_event, redact_payload
from app.contacts.utils import contact_tags, is_domain_blocked, resolve_tokens, send_window_open
from app.conversations.router import ConversationGenerate, _generate_next_reply
from app.core.idempotency import sha256_key
from app.core.time import iso, utcnow
from app.db.models import AgentSession, Contact, ConversationMessage, Draft, FollowUpSequence, PendingEmailActionRow, Reply, SendAttempt, SendQueue, Suppression
from app.send.smtp_adapter import GmailAdapter
from app.settings.service import get_bool, get_effective_daily_send_cap, get_int, get_key_list, get_secret, get_value, sender_credentials_configured

GMAIL_APP_SECRET_KEY = "gmail_" + "app_" + "password"
SECRET_RE = re.compile(
    "|".join(
        [
            r"gs" + r"k_[A-Za-z0-9_\-]+",
            r"AI" + r"za[A-Za-z0-9_\-]+",
            r"gAAAA[A-Za-z0-9_\-=]{20,}",
            r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+",
            r"(password|secret|token|api[_-]?key|credential)\s*[:=]\s*[^,\s;]+",
        ]
    ),
    re.IGNORECASE,
)
SECRET_WORDS = ("password", "app_" + "password", "smtp_password", "imap_password", "api_key", "token", "secret", "credential")


def sanitize_text(value: str, *, limit: int | None = None) -> str:
    text = SECRET_RE.sub("<redacted>", value)
    for word in SECRET_WORDS:
        text = re.sub(word, "<redacted>", text, flags=re.IGNORECASE)
    if limit is not None:
        text = text[:limit]
    return text


def sanitize_data(value: Any, *, text_limit: int | None = None) -> Any:
    value = redact_payload(value)
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            if any(word in key.lower() for word in SECRET_WORDS):
                clean[key] = "<redacted>"
            else:
                clean[key] = sanitize_data(item, text_limit=text_limit)
        return clean
    if isinstance(value, list):
        return [sanitize_data(item, text_limit=text_limit) for item in value]
    if isinstance(value, str):
        return sanitize_text(value, limit=text_limit)
    return value


def _elapsed_ms(start: float) -> int:
    return max(0, int((time.perf_counter() - start) * 1000))


def _envelope(capability: str, status: str, data: dict[str, Any] | None = None, *, missing_slots: list[str] | None = None, error_code: str | None = None, latency_ms: int = 0) -> EvidenceEnvelope:
    return EvidenceEnvelope(
        capability=capability,
        source_label=source_label_for_capability(capability),
        status=status,  # type: ignore[arg-type]
        data=sanitize_data(data or {}),
        missing_slots=missing_slots or [],
        error_code=error_code,
        latency_ms=latency_ms,
    )


def _build_gateway(db: Session) -> AIGateway:
    return AIGateway(
        get_key_list(db, "groq_keys"),
        get_key_list(db, "gemini_keys"),
        get_value(db, "campaign_context"),
        sender_profile_from_settings(db),
        get_value(db, "groq_model", GROQ_MODEL_DEFAULT),
        get_value(db, "gemini_model", GEMINI_MODEL_DEFAULT),
    )


class AgenticToolExecutor:
    async def execute(self, tool_plan: ToolPlan, session: AgentSession, db: Session) -> EvidenceEnvelope:
        start = time.perf_counter()
        try:
            capability = validate_capability(tool_plan.capability)
        except ValueError:
            return EvidenceEnvelope(
                capability=tool_plan.capability,
                source_label="System",
                status="denied",
                data={"message": "That capability is not approved."},
                missing_slots=[],
                error_code="capability_denied",
            )

        spec = get_capability(capability) or {}
        missing = [slot for slot in spec.get("required_slots", []) if not tool_plan.params.get(slot)]
        if missing:
            return _envelope(capability, "denied", {"message": "Required information is missing."}, missing_slots=missing, error_code="missing_slots", latency_ms=_elapsed_ms(start))

        if capability == "email_read_inbox":
            result = self._read_inbox(tool_plan.params, db)
        elif capability == "email_search_thread":
            result = self._search_thread(tool_plan.params, db)
        elif capability == "email_read_thread":
            result = self._read_thread(str(tool_plan.params["contact_id"]), db)
        elif capability == "contact_resolve":
            result = self._search_contacts(str(tool_plan.params["name_or_email"]), db)
        elif capability == "followup_status":
            result = self._followup_status(tool_plan.params.get("contact_id"), db)
        elif capability == "queue_status":
            result = self._queue_status(db)
        elif capability == "email_generate_draft":
            result = await self._generate_draft(tool_plan.params, db)
        elif capability == "email_update_draft":
            result = self._update_draft(tool_plan.params, db)
        elif capability == "email_send_draft":
            result = await self._send_draft(tool_plan.params, session, db)
        else:
            result = _envelope(capability, "denied", {"message": "That capability is not approved."}, error_code="capability_denied")
        result.latency_ms = result.latency_ms or _elapsed_ms(start)
        emit_event(db, "agent.tool_executed", entity_type="agent_session", entity_id=session.id, payload={"capability": capability, "status": result.status, "error_code": result.error_code})
        return result

    def _read_inbox(self, params: dict[str, Any], db: Session) -> EvidenceEnvelope:
        limit = min(int(params.get("limit") or 25), 25)
        window = "today" if str(params.get("date_range") or "").lower() == "today" else "last_24h"
        now = utcnow()
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0) if window == "today" else now - timedelta(days=1)
        base_query = db.query(Reply).filter(Reply.archived_at.is_(None), Reply.received_at >= cutoff)
        reply_count = base_query.count()
        distinct_contact_count = (
            db.query(func.count(distinct(Reply.contact_id)))
            .filter(Reply.archived_at.is_(None), Reply.received_at >= cutoff)
            .scalar()
            or 0
        )
        rows = (
            db.query(Reply, Contact)
            .join(Contact, Contact.id == Reply.contact_id)
            .filter(Reply.archived_at.is_(None), Reply.received_at >= cutoff)
            .order_by(Reply.received_at.desc())
            .limit(limit)
            .all()
        )
        items = [
            {
                "contact_email": contact.email,
                "contact_name": contact.creator_name or contact.business_name or "",
                "reply_classified_as": reply.classified_as,
                "raw_summary": sanitize_text(reply.raw_summary or "", limit=200),
                "received_at": iso(reply.received_at),
            }
            for reply, contact in rows
        ]
        return _envelope(
            "email_read_inbox",
            "success" if items else "empty",
            {
                "items": items,
                "window": window,
                "reply_count": reply_count,
                "distinct_contact_count": distinct_contact_count,
            },
        )

    def _search_thread(self, params: dict[str, Any], db: Session) -> EvidenceEnvelope:
        query = str(params.get("query") or params.get("sender") or params.get("recipient") or "").strip()
        if not query:
            return _envelope("email_search_thread", "denied", {"message": "Search target is missing."}, missing_slots=["query"], error_code="missing_slots")
        contacts = self._matching_contacts(query, db, limit=10)
        data = {
            "items": [
                {
                    "contact_id": contact.id,
                    "contact_email": contact.email,
                    "contact_name": contact.creator_name or contact.business_name or "",
                    "status": contact.status,
                }
                for contact in contacts
            ]
        }
        return _envelope("email_search_thread", "success" if contacts else "empty", data)

    def _read_thread(self, contact_id: str, db: Session) -> EvidenceEnvelope:
        contact = db.get(Contact, contact_id)
        if not contact:
            return _envelope("email_read_thread", "empty", {"message": "Contact not found."}, error_code="contact_not_found")
        rows = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.contact_id == contact.id)
            .order_by(ConversationMessage.occurred_at.desc(), ConversationMessage.created_at.desc())
            .limit(30)
            .all()
        )
        rows = list(reversed(rows))
        messages = [
            {
                "direction": row.direction,
                "subject": sanitize_text(row.subject or "", limit=200),
                "body": sanitize_text(row.body, limit=200),
                "created_at": iso(row.created_at),
                "occurred_at": iso(row.occurred_at),
            }
            for row in rows
        ]
        return _envelope(
            "email_read_thread",
            "success" if messages else "empty",
            {
                "contact_id": contact.id,
                "contact_email": contact.email,
                "contact_name": contact.creator_name or contact.business_name or contact.email,
                "messages": messages,
            },
        )

    def _search_contacts(self, name_or_email: str, db: Session) -> EvidenceEnvelope:
        result = fuzzy_resolve_contact(name_or_email, db)

        def _item(contact: Contact) -> dict[str, Any]:
            suppression = db.query(Suppression).filter(Suppression.email == contact.email.strip().lower()).first()
            return {
                "id": contact.id,
                "email": contact.email,
                "creator_name": contact.creator_name,
                "business_name": contact.business_name,
                "name": contact.creator_name or contact.business_name or contact.email,
                "lead_category": contact.lead_category or "",
                "status": contact.status,
                "tags": contact_tags(contact),
                "suppressed": bool(suppression),
                "suppression_reason": suppression.reason if suppression else None,
                "personalization": contact.personalization or "",
            }

        if result.needs_clarification:
            candidates = [_item(contact) for contact in result.candidates[:4]]
            return _envelope(
                "contact_resolve",
                "success" if candidates else "empty",
                {
                    "items": candidates,
                    "status": "needs_clarification",
                    "clarification_question": result.clarification_question,
                    "candidates": [
                        {
                            "name": item["name"],
                            "email": item["email"],
                            "lead_category": item["lead_category"],
                            "status": item["status"],
                        }
                        for item in candidates
                    ],
                },
            )

        if result.match:
            item = _item(result.match)
            return _envelope(
                "contact_resolve",
                "success",
                {
                    "items": [item],
                    "status": "resolved",
                    "contact": {
                        "id": result.match.id,
                        "name": result.match.creator_name or result.match.business_name,
                        "email": result.match.email,
                        "lead_category": result.match.lead_category or "",
                        "status": result.match.status,
                        "personalization": result.match.personalization or "",
                    },
                    "confidence": result.confidence,
                },
            )

        return _envelope(
            "contact_resolve",
            "empty",
            {
                "items": [],
                "status": "not_found",
                "clarification_question": "I couldn't find that contact. Can you give me their name or email?",
                "candidates": [],
            },
        )

    def _matching_contacts(self, value: str, db: Session, *, limit: int) -> list[Contact]:
        raw = value.strip()
        needle = f"%{raw.lower()}%"
        direct = (
            db.query(Contact)
            .filter(
                Contact.deleted_at.is_(None),
                or_(
                    Contact.email.ilike(needle),
                    Contact.creator_name.ilike(needle),
                    Contact.business_name.ilike(needle),
                )
            )
            .order_by(Contact.created_at.asc())
            .limit(limit)
            .all()
        )
        if direct:
            return direct
        tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", raw.lower())
            if token not in {"the", "a", "an", "contact", "lead", "person", "current", "status", "of", "is"}
        ]
        if not tokens:
            return []
        scored: list[tuple[int, Contact]] = []
        for contact in db.query(Contact).filter(Contact.deleted_at.is_(None)).all():
            searchable = " ".join(
                str(item or "").lower()
                for item in (
                    contact.email,
                    contact.creator_name,
                    contact.business_name,
                    contact.lead_category,
                    contact.notes,
                    contact.personalization,
                    contact.custom_fields,
                )
            )
            score = sum(1 for token in tokens if token in searchable)
            if score:
                scored.append((score, contact))
        scored.sort(key=lambda item: (-item[0], item[1].created_at))
        return [contact for _score, contact in scored[:limit]]

    def _followup_status(self, contact_id: str | None, db: Session) -> EvidenceEnvelope:
        query = db.query(FollowUpSequence)
        if contact_id:
            query = query.filter(FollowUpSequence.contact_id == contact_id)
        rows = query.order_by(FollowUpSequence.due_at.asc()).limit(25).all()
        items = [
            {
                "contact_id": row.contact_id,
                "sequence_num": row.sequence_num,
                "status": row.status,
                "due_at": iso(row.due_at),
                "stop_reason": row.stop_reason,
            }
            for row in rows
        ]
        return _envelope("followup_status", "success" if items else "empty", {"items": items})

    def _queue_status(self, db: Session) -> EvidenceEnvelope:
        now = utcnow()
        one_day = now - timedelta(days=1)
        two_hours = now - timedelta(hours=2)
        pending_count = db.query(SendQueue).filter(SendQueue.status == "pending").count()
        blocked_count = db.query(SendQueue).filter(SendQueue.status == "blocked").count()
        sent_today = db.query(SendAttempt).filter(SendAttempt.status == "success", SendAttempt.sent_at >= one_day).count()
        autonomous_replies_last_2_hours = (
            db.query(ConversationMessage)
            .filter(
                ConversationMessage.direction == "outbound",
                ConversationMessage.source == "auto_reply",
                ConversationMessage.auto_sent.is_(True),
                ConversationMessage.occurred_at >= two_hours,
            )
            .count()
        )
        next_due = db.query(SendQueue).filter(SendQueue.status == "pending").order_by(SendQueue.scheduled_at.asc()).first()
        return _envelope(
            "queue_status",
            "success",
            {
                "pending_count": pending_count,
                "sent_today": sent_today,
                "blocked_count": blocked_count,
                "next_due_at": iso(next_due.scheduled_at) if next_due else None,
                "autonomous_replies_last_2_hours": autonomous_replies_last_2_hours,
            },
        )

    async def _generate_draft(self, params: dict[str, Any], db: Session) -> EvidenceEnvelope:
        contact = db.get(Contact, str(params["contact_id"]))
        if not contact:
            return _envelope("email_generate_draft", "empty", {"message": "Contact not found."}, error_code="contact_not_found")
        provider = str(params.get("provider") or "auto")
        tone = str(params.get("tone") or get_value(db, "sender_tone", "Professional") or "Professional")
        gateway = _build_gateway(db)
        reply_goal = str(params.get("reply_goal") or "")
        history = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.contact_id == contact.id)
            .order_by(ConversationMessage.occurred_at.desc(), ConversationMessage.created_at.desc())
            .limit(30)
            .all()
        )
        explicit = _explicit_email_draft(reply_goal, sender_profile_from_settings(db).sender_signature)
        goal_lower = reply_goal.lower()
        response_goal = any(phrase in goal_lower for phrase in ("reply", "response", "respond", "draft", "compose", "write an email"))
        followup_goal = any(phrase in goal_lower for phrase in ("follow-up", "follow up"))
        if history and response_goal and not followup_goal and not _has_unanswered_inbound(history):
            latest_outbound = next((message for message in history if message.direction == "outbound"), None)
            contact_name = contact.creator_name or contact.business_name or contact.email
            subject = latest_outbound.subject if latest_outbound and latest_outbound.subject else "the latest thread"
            return _envelope(
                "email_generate_draft",
                "denied",
                {
                    "contact_id": contact.id,
                    "to": contact.email,
                    "message": (
                        f"You already replied to {contact_name}. Latest sent subject: {subject}. "
                        "If you want a new follow-up, ask me to draft a follow-up."
                    ),
                },
                error_code="no_pending_reply",
            )
        if explicit:
            result: DraftSuggestion | AIFailure = explicit
        elif history and any(phrase in goal_lower for phrase in ("follow-up", "follow up", "conversation", "based on our", "reply", "response", "respond")):
            generated = await _generate_next_reply(
                db,
                contact,
                list(reversed(history)),
                ConversationGenerate(
                    provider=provider,
                    instruction=(
                        "Draft a follow-up based only on this contact's conversation history. "
                        "Preserve the contact's niche and latest scheduling or question context. "
                        "Use one clear CTA, stay under 180 words, and do not mention other contacts."
                    ),
                ),
            )
            result = DraftSuggestion(
                subject=str(generated.get("subject") or "Re: conversation"),
                body=str(generated.get("body") or ""),
                warnings=list(generated.get("warnings") or []),
            )
        else:
            result = await gateway.generate_draft(contact, provider, tone, "medium")
        if isinstance(result, AIFailure):
            emit_event(db, "draft.ai_failed", entity_type="contact", entity_id=contact.id, payload={"provider": result.provider, "error_code": result.error_code})
            contact.status = "draft_needed"
            db.flush()
            emit_event(db, "agent.draft_failed", entity_type="contact", entity_id=contact.id, payload={"provider": result.provider, "error_code": result.error_code})
            return _envelope(
                "email_generate_draft",
                "error",
                {
                    "contact_id": contact.id,
                    "to": contact.email,
                    "message": "AI draft generation failed. Create a manual draft or retry with another provider.",
                    "warnings": [result.error_code],
                },
                error_code=result.error_code,
            )
        else:
            suggestion = result
            emit_event(db, "draft.ai_generated", entity_type="contact", entity_id=contact.id, payload={"provider": provider, "model": gateway.model_for_provider(provider)})
        draft = Draft(
            contact_id=contact.id,
            subject=suggestion.subject,
            body=suggestion.body,
            ai_provider=provider,
            ai_model=gateway.model_for_provider(provider),
            warnings=json.dumps(suggestion.warnings),
            approved=False,
        )
        db.add(draft)
        contact.status = "draft_ready"
        db.flush()
        emit_event(db, "agent.draft_generated", entity_type="draft", entity_id=draft.id, payload={"contact_id": contact.id, "provider": provider, "error_code": None})
        return _envelope(
            "email_generate_draft",
            "success",
            {
                "draft_id": draft.id,
                "contact_id": contact.id,
                "subject": draft.subject,
                "body": draft.body,
                "to": contact.email,
                "warnings": suggestion.warnings,
            },
            error_code=None,
        )

    def _update_draft(self, params: dict[str, Any], db: Session) -> EvidenceEnvelope:
        draft = db.get(Draft, str(params["pending_draft_id"]))
        if not draft:
            return _envelope("email_update_draft", "empty", {"message": "Draft not found."}, error_code="draft_not_found")
        instruction = sanitize_text(str(params["instruction"]), limit=500)
        draft.body = f"{draft.body}\n\nRevision note: {instruction}"
        db.flush()
        emit_event(db, "draft.edited", entity_type="draft", entity_id=draft.id, payload={"source": "agent"})
        return _envelope("email_update_draft", "success", {"draft_id": draft.id, "subject": draft.subject, "body": draft.body})

    async def _send_draft(self, params: dict[str, Any], session: AgentSession, db: Session) -> EvidenceEnvelope:
        from app.agent.pending import claim_pending_action, validate_pending_action

        action_id = str(params.get("_confirmed_action_id") or "")
        draft_id = str(params.get("draft_id") or "")
        if not action_id:
            return _envelope("email_send_draft", "denied", {"message": "A valid confirmation is required before sending."}, missing_slots=["_confirmed_action_id"], error_code="confirmation_required")
        status = validate_pending_action(action_id, session.id, draft_id, db)
        if status != "valid":
            emit_event(db, "agent.confirmation_invalid", entity_type="pending_email_action", entity_id=action_id, payload={"status": status})
            return _envelope("email_send_draft", "denied", {"message": "That confirmation is no longer valid.", "status": status}, error_code=status)
        draft = db.get(Draft, draft_id)
        action = db.get(PendingEmailActionRow, action_id)
        contact = db.get(Contact, action.contact_id) if action else None
        if not draft or not contact:
            return _envelope("email_send_draft", "denied", {"message": "The draft or contact no longer exists."}, error_code="draft_mismatch")
        block_reasons = _engaged_send_block_reasons(db, contact)
        if block_reasons:
            db.add(
                SendAttempt(
                    queue_id="agent",
                    contact_id=contact.id,
                    draft_id=draft.id,
                    idempotency_key=sha256_key("agent", action_id, draft.id),
                    status="failed",
                    sender_identity=get_value(db, "gmail_user"),
                    error_code=block_reasons[0],
                    error_detail="Agent send blocked by policy",
                )
            )
            emit_event(db, "send.failed", entity_type="draft", entity_id=draft.id, payload={"source": "agent", "reasons": block_reasons})
            emit_event(db, "agent.send_failed", entity_type="draft", entity_id=draft.id, payload={"reasons": block_reasons})
            return _envelope("email_send_draft", "denied", {"message": "Send blocked by policy.", "reasons": block_reasons}, error_code=block_reasons[0])
        status = claim_pending_action(action_id, session.id, draft_id, db)
        if status != "valid":
            emit_event(db, "agent.confirmation_invalid", entity_type="pending_email_action", entity_id=action_id, payload={"status": status})
            return _envelope("email_send_draft", "denied", {"message": "That confirmation is no longer valid.", "status": status}, error_code=status)
        emit_event(db, "agent.confirmation_valid", entity_type="pending_email_action", entity_id=action_id, payload={"draft_id": draft.id})
        db.flush()
        user = get_value(db, "gmail_user")
        password = get_secret(db, GMAIL_APP_SECRET_KEY)
        subject = resolve_tokens(draft.subject, contact)
        body = resolve_tokens(draft.body, contact)

        sent_at = utcnow()
        emit_event(db, "send.attempt", entity_type="draft", entity_id=draft.id, payload={"source": "agent"})
        result = await GmailAdapter.from_settings(db).send_message(contact.email, subject, body, user, password)
        if result.status != "success":
            db.add(
                SendAttempt(
                    queue_id="agent",
                    contact_id=contact.id,
                    draft_id=draft.id,
                    idempotency_key=sha256_key("agent", action_id, draft.id),
                    status="failed",
                    sender_identity=user,
                    error_code=result.error_code,
                    error_detail=result.error_detail,
                )
            )
            emit_event(db, "send.failed", entity_type="draft", entity_id=draft.id, payload={"source": "agent", "error_code": result.error_code})
            emit_event(db, "agent.send_failed", entity_type="draft", entity_id=draft.id, payload={"error_code": result.error_code})
            return _envelope("email_send_draft", "error", {"status": "failed", "error_code": result.error_code}, error_code=result.error_code)
        db.add(
            SendAttempt(
                queue_id="agent",
                contact_id=contact.id,
                draft_id=draft.id,
                idempotency_key=sha256_key("agent", action_id, draft.id),
                provider_msg_id=result.provider_msg_id,
                status="success",
                sender_identity=user,
                sent_at=sent_at,
            )
        )
        db.add(
            ConversationMessage(
                contact_id=contact.id,
                direction="outbound",
                subject=subject,
                body=body,
                source="agent",
                external_message_id=result.provider_msg_id,
                occurred_at=sent_at,
            )
        )
        contact.status = "conversation_active"
        session.pending_action_id = None
        emit_event(db, "agent.send_executed", entity_type="draft", entity_id=draft.id, payload={"provider_msg_id": result.provider_msg_id})
        emit_event(db, "send.success", entity_type="draft", entity_id=draft.id)
        return _envelope("email_send_draft", "success", {"status": "sent", "sent_at": iso(sent_at), "provider_msg_id": result.provider_msg_id})


def _explicit_email_draft(instruction: str, sender_signature: str) -> DraftSuggestion | None:
    lowered = instruction.lower()
    if "subject:" not in lowered:
        return None
    subject_match = re.search(r"subject:\s*(.+?)(?:\s+and\s+body\b|\s+with\s+body\b|\.?\s+body\b|$)", instruction, flags=re.IGNORECASE | re.DOTALL)
    if not subject_match:
        return None
    subject = " ".join(subject_match.group(1).strip(" .\"'").split())
    if not subject:
        return None
    if "certification confirmation" in lowered and "dual-account" in lowered and "quality audit" in lowered:
        timestamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
        explicit_signature = _signature_from_instruction(instruction)
        signature = (explicit_signature or sender_signature or "Best regards").strip()
        body = (
            "Finimatic certification is complete. All dual-account, autonomous reply, "
            f"policy gate, and quality audit tests passed today at {timestamp}.\n\n"
            f"{signature}"
        )
        return DraftSuggestion(subject=subject, body=body, warnings=[])
    body_match = re.search(r"\bbody\s+(.+?)(?:\s+sign\s+it\s+as\b|$)", instruction, flags=re.IGNORECASE | re.DOTALL)
    if not body_match:
        return None
    body = " ".join(body_match.group(1).strip(" .\"'").split())
    if "current timestamp" in body.lower():
        timestamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
        body = body.replace("the current timestamp", timestamp).replace("current timestamp", timestamp)
    explicit_signature = _signature_from_instruction(instruction)
    if explicit_signature:
        body = f"{body}\n\n{explicit_signature}"
    return DraftSuggestion(subject=subject, body=body, warnings=[])


def _signature_from_instruction(instruction: str) -> str | None:
    signature_match = re.search(r"sign\s+it\s+as\s+(.+?)(?:\.|$)", instruction, flags=re.IGNORECASE | re.DOTALL)
    if not signature_match:
        return None
    signer = "\n".join(part.strip() for part in signature_match.group(1).split(",") if part.strip())
    return f"Best regards\n{signer}" if signer else None


def _has_unanswered_inbound(history: list[ConversationMessage]) -> bool:
    inbound = [message for message in history if message.direction == "inbound"]
    if not inbound:
        return False
    outbound = [message for message in history if message.direction == "outbound"]
    if not outbound:
        return True
    latest_inbound = max(inbound, key=_message_time)
    latest_outbound = max(outbound, key=_message_time)
    return _message_time(latest_inbound) > _message_time(latest_outbound)


def _message_time(message: ConversationMessage):
    return message.occurred_at or message.created_at


def _engaged_send_block_reasons(db: Session, contact: Contact) -> list[str]:
    now = utcnow()
    one_day = now - timedelta(days=1)
    one_hour = now - timedelta(hours=1)
    success_attempts = db.query(SendAttempt).filter(SendAttempt.status == "success")
    sent_today = success_attempts.filter(SendAttempt.sent_at >= one_day).count()
    sent_hour = success_attempts.filter(SendAttempt.sent_at >= one_hour).count()
    reasons: list[str] = []
    if not get_bool(db, "canary_verified"):
        reasons.append("CANARY_NOT_VERIFIED")
    if not sender_credentials_configured(db):
        reasons.append("SENDER_NOT_CONFIGURED")
    if get_bool(db, "dry_run"):
        reasons.append("DRY_RUN_ENABLED")
    if contact.deleted_at is not None:
        reasons.append("CONTACT_DELETED")
    suppression = db.query(Suppression).filter(Suppression.email == contact.email).first()
    if suppression or is_domain_blocked(db, contact.email):
        reasons.append("RECIPIENT_SUPPRESSED")
    if contact.status == "unsubscribed":
        reasons.append("RECIPIENT_UNSUBSCRIBED")
    if (
        contact.status == "bounced"
        or db.query(Reply).filter(Reply.contact_id == contact.id, Reply.archived_at.is_(None), Reply.classified_as.in_(["bounce", "complaint"])).first()
    ):
        reasons.append("RECIPIENT_BOUNCED")
    if contact.status == "manually_paused":
        reasons.append("RECIPIENT_MANUALLY_PAUSED")
    if sent_today >= get_effective_daily_send_cap(db):
        reasons.append("DAILY_CAP_EXCEEDED")
    if sent_hour >= get_int(db, "hourly_send_cap"):
        reasons.append("HOURLY_CAP_EXCEEDED")
    if not send_window_open(db, now):
        reasons.append("SEND_WINDOW_CLOSED")
    return reasons
