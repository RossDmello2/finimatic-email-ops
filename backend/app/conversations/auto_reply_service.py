from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.ai.schema import DraftSuggestion
from app.audit.service import emit_event
from app.contacts.utils import send_window_open
from app.conversations.router import (
    ConversationGenerate,
    _conversation_offer_reference,
    _backfill_contact_conversation,
    _conversation_signature,
    _engaged_send_block_reasons,
    _generate_next_reply,
)
from app.core.idempotency import sha256_key
from app.core.time import iso, utcnow
from app.db.models import Contact, ConversationMessage, Draft, Reply, SendAttempt, Suppression
from app.send.smtp_adapter import GmailAdapter
from app.settings.service import get_bool, get_int, get_secret, get_value, sender_credentials_configured


SAFE_DEFAULT_INTENTS = {"positive_interest", "objection", "question"}
BANNED_OPENERS = ("i hope", "thank you for your", "great to hear", "i appreciate")
BANNED_PHRASES = (
    "leverage",
    "synergy",
    "cutting-edge",
    "circle back",
    "touch base",
    "innovative solution",
    "reach out",
    "paradigm",
    "value-add",
    "i wanted to follow up",
    "just checking in",
)
CTA_PHRASES = (
    "would you be open",
    "can you share",
    "please share",
    "send me",
    "reply with",
    "let me know",
    "does that work",
    "are you free",
    "could we",
    "can we",
)


@dataclass
class QualityResult:
    passed: bool
    failures: list[str]


@dataclass
class AutoReplyResult:
    action: str
    mode: str = ""
    draft_id: str | None = None
    message_id: str | None = None
    reason: str | None = None
    subject: str | None = None


class AutoReplyService:
    async def should_auto_reply(self, contact: Contact, reply: Reply, db: Session) -> tuple[bool, str]:
        should_reply, mode, _reason = self._decision(contact, reply, db)
        return should_reply, mode

    async def process_reply(self, contact_id: str, reply_id: str, db: Session) -> AutoReplyResult:
        contact = db.get(Contact, contact_id)
        reply = db.get(Reply, reply_id)
        if not contact or not reply:
            return AutoReplyResult(action="skipped", reason="missing_contact_or_reply")
        should_reply, mode, reason = self._decision(contact, reply, db)
        if not should_reply:
            emit_event(
                db,
                "auto_reply.skipped",
                entity_type="reply",
                entity_id=reply.id,
                payload={"contact_id": contact.id, "reason": reason},
            )
            return AutoReplyResult(action="skipped", reason=reason)
        return await self.generate_and_maybe_send(contact.id, reply.id, mode, db)

    async def generate_and_maybe_send(self, contact_id: str, reply_id: str, mode: str, db: Session) -> AutoReplyResult:
        contact = db.get(Contact, contact_id)
        reply = db.get(Reply, reply_id)
        if not contact or not reply:
            return AutoReplyResult(action="skipped", reason="missing_contact_or_reply")

        existing = self._existing_pending_proposal(db, reply.id)
        if existing and mode == "propose":
            return AutoReplyResult(action="proposed", mode="propose", draft_id=existing.id, subject=existing.subject)

        _backfill_contact_conversation(db, contact)
        db.flush()
        messages = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.contact_id == contact.id)
            .order_by(ConversationMessage.occurred_at.desc(), ConversationMessage.created_at.desc())
            .limit(30)
            .all()
        )
        scoped_messages = self._scope_messages_after_latest_fresh_outbound(messages, reply)
        history = list(reversed(scoped_messages))
        if not history:
            history = [
                ConversationMessage(
                    contact_id=contact.id,
                    direction="inbound",
                    subject="Re: conversation",
                    body=reply.raw_summary or "Interested.",
                    source="auto_reply_context",
                    external_message_id=f"auto-reply-context:{reply.id}",
                    occurred_at=reply.received_at,
                )
            ]
        if self._is_single_character_question(reply.raw_summary):
            subject = self._reply_subject_from_history(history)
            suggestion = DraftSuggestion(
                subject=subject,
                body=(
                    f"{contact.creator_name or contact.business_name or 'there'}, I saw your question mark; "
                    "what would you like me to clarify about the assistant, scope, pricing, or next step today?\n\n"
                    f"{_conversation_signature(db)}"
                ),
                warnings=[],
            )
            draft = self._store_draft(db, contact, suggestion, "auto_reply_proposed", reply.id, approved=False, quality=QualityResult(True, []))
            self._store_pending_message(db, contact, draft, reply)
            emit_event(db, "auto_reply.proposed", entity_type="draft", entity_id=draft.id, payload={"contact_id": contact.id, "draft_id": draft.id, "reason": "minimal_question"})
            return AutoReplyResult(action="proposed", mode="propose", draft_id=draft.id, subject=draft.subject)
        instruction = (
            "Reply to the latest message. Move toward a call. Be specific to what they said. "
            "Quality gate enforced: no generic opener, one CTA, under 250 words, no invented pricing, statistics, or timelines. "
            "If the latest reply expresses readiness, scheduling, or a timeline, answer that latest point directly and do not re-argue older objections. "
            "Use fresh wording that does not repeat prior outbound sentences."
        )
        generated = await _generate_next_reply(
            db,
            contact,
            history,
            ConversationGenerate(provider="auto", instruction=f"{instruction}\nLatest reply snippet: {(reply.raw_summary or '')[:400]}"),
        )
        suggestion = DraftSuggestion(subject=generated["subject"], body=generated["body"], warnings=[])
        quality = self.quality_gate(suggestion, contact, reply, db=db)
        if not quality.passed:
            emit_event(
                db,
                "auto_reply.quality_failed",
                entity_type="reply",
                entity_id=reply.id,
                payload={"contact_id": contact.id, "failures": quality.failures},
            )
            retry = await _generate_next_reply(
                db,
                contact,
                history,
                ConversationGenerate(
                    provider="auto",
                    instruction=(
                        f"{instruction}\nFix these quality failures exactly: {', '.join(quality.failures)}. "
                        f"Latest reply snippet: {(reply.raw_summary or '')[:400]}\n"
                        "Return a concise professional reply that passes the gate. "
                        "Use exactly one final question or call-to-action, not two. "
                        "Do not repeat any 10-word phrase from the previous outbound email."
                    ),
                ),
            )
            suggestion = DraftSuggestion(subject=retry["subject"], body=retry["body"], warnings=quality.failures)
            quality = self.quality_gate(suggestion, contact, reply, db=db)
            if not quality.passed:
                fallback = self._deterministic_safe_reply(contact, reply, history, db)
                if fallback:
                    suggestion = fallback
                    quality = self.quality_gate(suggestion, contact, reply, db=db)
                if not quality.passed:
                    mode = "propose"

        if mode == "propose":
            draft = self._store_draft(db, contact, suggestion, "auto_reply_proposed", reply.id, approved=False, quality=quality)
            self._store_pending_message(db, contact, draft, reply)
            emit_event(db, "auto_reply.proposed", entity_type="draft", entity_id=draft.id, payload={"contact_id": contact.id, "draft_id": draft.id})
            return AutoReplyResult(action="proposed", mode="propose", draft_id=draft.id, subject=draft.subject)

        block_reasons = self._send_block_reasons(db, contact)
        if block_reasons:
            suggestion.warnings.extend(block_reasons)
            draft = self._store_draft(db, contact, suggestion, "auto_reply_proposed", reply.id, approved=False, quality=quality)
            self._store_pending_message(db, contact, draft, reply)
            emit_event(
                db,
                "auto_reply.proposed",
                entity_type="draft",
                entity_id=draft.id,
                payload={"contact_id": contact.id, "draft_id": draft.id, "fallback_reason": "send_gate_blocked", "block_reasons": block_reasons},
            )
            return AutoReplyResult(action="proposed", mode="propose", draft_id=draft.id, reason="send_gate_blocked", subject=draft.subject)

        draft = self._store_draft(db, contact, suggestion, "auto_reply", reply.id, approved=True, quality=quality)
        result = await self._send_draft(db, contact, draft, source="auto_reply", reply_id=reply.id)
        return result

    def quality_gate(self, draft: DraftSuggestion, contact: Contact, reply: Reply, db: Session | None = None) -> QualityResult:
        failures: list[str] = []
        body = draft.body or ""
        lowered = body.lower().strip()
        if lowered.startswith(BANNED_OPENERS):
            failures.append("banned_opener")
        contact_name = (contact.creator_name or contact.business_name or "").strip()
        if contact_name:
            name_ok = contact_name.split()[0].lower() in lowered or contact_name.lower() in lowered
        else:
            name_ok = False
        direct_words = [word for word in re.findall(r"[A-Za-z0-9]{4,}", reply.raw_summary or "") if word.lower() not in {"that", "this", "with", "from", "your", "have", "about"}]
        if not name_ok and not any(word.lower() in lowered for word in direct_words[:12]):
            failures.append("missing_recipient_or_detail")
        if len(re.findall(r"\b\w+\b", body)) >= 250:
            failures.append("too_long")
        if any(phrase in lowered for phrase in BANNED_PHRASES):
            failures.append("banned_phrase")
        if not (draft.subject or "").strip().lower().startswith("re:"):
            failures.append("subject_not_reply")
        signature = _conversation_signature(db).strip() if db is not None else "Best regards"
        if not body.strip().lower().endswith(signature.lower()):
            failures.append("missing_signature")
        if db is not None and self._mentions_other_contact_detail(db, contact, body):
            failures.append("cross_contact_detail")
        inbound_percentages = set(re.findall(r"\b\d+\s?%", reply.raw_summary or ""))
        for percentage in re.findall(r"\b\d+\s?%", body):
            if percentage not in inbound_percentages:
                failures.append("fabricated_stat")
                break
        question_count = body.count("?")
        cta_count = sum(len(re.findall(rf"\b{re.escape(phrase)}\b", lowered)) for phrase in CTA_PHRASES)
        if question_count == 1 and cta_count <= 1:
            pass
        elif question_count == 0 and cta_count == 1:
            pass
        else:
            failures.append("cta_count")
        if db is not None and self._repeats_previous_outbound(db, contact.id, body):
            failures.append("repeats_previous_outbound")
        if len(re.findall(r"\b\w+\b", body)) <= 30:
            failures.append("too_short")
        return QualityResult(passed=not failures, failures=failures)

    async def approve_pending_draft(self, draft_id: str, db: Session) -> AutoReplyResult:
        draft = db.get(Draft, draft_id)
        if not draft or draft.source != "auto_reply_proposed" or draft.approved or draft.rejected:
            raise ValueError("draft_not_pending")
        contact = db.get(Contact, draft.contact_id)
        if not contact:
            raise ValueError("contact_not_found")
        block_reasons = self._send_block_reasons(db, contact, allow_proposed=True)
        if block_reasons:
            raise ValueError(",".join(block_reasons))
        draft.approved = True
        draft.approved_at = utcnow()
        result = await self._send_draft(db, contact, draft, source="auto_reply_approved", reply_id=self._reply_id_from_notes(draft.notes))
        emit_event(db, "auto_reply.approved_and_sent", entity_type="draft", entity_id=draft.id, payload={"contact_id": contact.id, "message_id": result.message_id})
        return result

    def reject_pending_draft(self, draft_id: str, db: Session) -> Draft:
        draft = db.get(Draft, draft_id)
        if not draft or draft.source != "auto_reply_proposed" or draft.rejected:
            raise ValueError("draft_not_pending")
        draft.rejected = True
        message = db.query(ConversationMessage).filter(ConversationMessage.external_message_id == f"draft:{draft.id}").first()
        if message:
            message.source = "auto_reply_rejected"
        emit_event(db, "auto_reply.rejected", entity_type="draft", entity_id=draft.id, payload={"contact_id": draft.contact_id})
        return draft

    def pending_drafts(self, db: Session) -> list[dict]:
        drafts = (
            db.query(Draft)
            .filter(Draft.source == "auto_reply_proposed", Draft.approved.is_(False), Draft.rejected.is_(False))
            .order_by(Draft.created_at.desc())
            .all()
        )
        rows: list[dict] = []
        for draft in drafts:
            contact = db.get(Contact, draft.contact_id)
            reply = db.get(Reply, self._reply_id_from_notes(draft.notes) or "") if draft.notes else None
            if reply is None:
                reply = (
                    db.query(Reply)
                    .filter(Reply.contact_id == draft.contact_id)
                    .order_by(Reply.received_at.desc())
                    .first()
                )
            rows.append(
                {
                    "id": draft.id,
                    "contact_id": draft.contact_id,
                    "contact_name": (contact.creator_name or contact.business_name or "") if contact else "",
                    "contact_email": contact.email if contact else "",
                    "their_reply": (reply.raw_summary or "")[:240] if reply else "",
                    "subject": draft.subject,
                    "body": draft.body,
                    "generated_at": iso(draft.created_at),
                }
            )
        return rows

    def _decision(self, contact: Contact, reply: Reply, db: Session) -> tuple[bool, str, str]:
        if getattr(contact, "deleted_at", None) is not None:
            return False, "", "contact_deleted"
        if not get_bool(db, "auto_reply_enabled"):
            return False, "", "global_disabled"
        safe_intents = self._safe_intents(db)
        if (reply.intent or "unknown") not in safe_intents:
            return False, "", "unsafe_intent"
        if reply.classified_as in {"unsubscribe", "bounce", "complaint"}:
            return False, "", "unsafe_classification"
        if db.query(Suppression).filter(Suppression.email == contact.email.strip().lower()).first():
            return False, "", "contact_suppressed"
        if contact.status in {"unsubscribed", "suppressed", "bounced"}:
            return False, "", "unsafe_contact_status"
        min_gap = max(0, get_int(db, "auto_reply_min_gap_minutes"))
        latest_auto = (
            db.query(ConversationMessage)
            .filter(
                ConversationMessage.contact_id == contact.id,
                ConversationMessage.direction == "outbound",
                or_(ConversationMessage.auto_sent.is_(True), ConversationMessage.source == "auto_reply_proposed"),
            )
            .order_by(ConversationMessage.occurred_at.desc())
            .first()
        )
        if latest_auto and (utcnow() - latest_auto.occurred_at.replace(tzinfo=utcnow().tzinfo)).total_seconds() < min_gap * 60:
            return False, "", "min_gap_not_elapsed"
        cap = max(0, get_int(db, "auto_reply_daily_cap"))
        today = utcnow() - timedelta(days=1)
        sent_today = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.direction == "outbound", ConversationMessage.auto_sent.is_(True), ConversationMessage.occurred_at >= today)
            .count()
        )
        if sent_today >= cap:
            return False, "", "daily_cap_exceeded"
        if not get_bool(db, "canary_verified"):
            return False, "", "canary_not_verified"
        if not send_window_open(db, utcnow()):
            return False, "", "send_window_closed"

        override = contact.auto_reply_override
        if override == "disabled":
            return False, "", "contact_override_disabled"
        if override == "enabled":
            return True, self._global_mode(db), ""
        if override == "propose":
            return True, "propose", ""
        return True, self._global_mode(db), ""

    def _safe_intents(self, db: Session) -> set[str]:
        raw = get_value(db, "auto_reply_safe_intents", "")
        values = {item.strip() for item in raw.split(",") if item.strip()}
        return values or SAFE_DEFAULT_INTENTS

    def _global_mode(self, db: Session) -> str:
        return "autonomous" if get_value(db, "auto_reply_mode", "propose") == "autonomous" else "propose"

    def _scope_messages_after_latest_fresh_outbound(self, messages: list[ConversationMessage], reply: Reply) -> list[ConversationMessage]:
        reply_time = self._aware_time(reply.received_at)
        anchors = [
            message
            for message in messages
            if message.direction == "outbound"
            and not (message.subject or "").strip().lower().startswith("re:")
            and self._aware_time(message.occurred_at) is not None
            and (reply_time is None or self._aware_time(message.occurred_at) <= reply_time)
        ]
        if not anchors:
            return messages
        anchor = max(anchors, key=lambda message: self._aware_time(message.occurred_at) or utcnow())
        anchor_time = self._aware_time(anchor.occurred_at)
        if anchor_time is None:
            return messages
        return [
            message
            for message in messages
            if self._aware_time(message.occurred_at) is not None and self._aware_time(message.occurred_at) >= anchor_time
        ]

    def _aware_time(self, value):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=utcnow().tzinfo)
        return value

    def _is_single_character_question(self, raw_summary: str | None) -> bool:
        value = (raw_summary or "").strip()
        if not value:
            return False
        first_line = value.splitlines()[0].strip()
        first_line = re.split(r"\s+On\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),", first_line, maxsplit=1)[0].strip()
        return first_line == "?"

    def _reply_subject_from_history(self, history: list[ConversationMessage]) -> str:
        for message in reversed(history):
            subject = (message.subject or "").strip()
            if message.direction == "inbound" and subject:
                return subject if subject.lower().startswith("re:") else f"Re: {subject}"
        for message in reversed(history):
            subject = (message.subject or "").strip()
            if subject:
                return subject if subject.lower().startswith("re:") else f"Re: {subject}"
        return "Re: conversation"

    def _send_block_reasons(self, db: Session, contact: Contact, *, allow_proposed: bool = False) -> list[str]:
        reasons = _engaged_send_block_reasons(db, contact)
        if not get_bool(db, "canary_verified"):
            reasons.append("CANARY_NOT_VERIFIED")
        if get_bool(db, "dry_run"):
            reasons.append("DRY_RUN_ENABLED")
        if not sender_credentials_configured(db):
            reasons.append("SENDER_NOT_CONFIGURED")
        if allow_proposed and "RECIPIENT_REPLIED" in reasons:
            reasons.remove("RECIPIENT_REPLIED")
        return sorted(set(reasons))

    def _store_draft(self, db: Session, contact: Contact, suggestion: DraftSuggestion, source: str, reply_id: str, *, approved: bool, quality: QualityResult) -> Draft:
        draft = Draft(
            contact_id=contact.id,
            subject=suggestion.subject,
            body=suggestion.body,
            ai_provider="auto",
            ai_model=None,
            warnings=json.dumps(list(dict.fromkeys([*suggestion.warnings, *quality.failures]))),
            notes=f"auto_reply_reply_id:{reply_id}",
            source=source,
            rejected=False,
            approved=approved,
            approved_at=utcnow() if approved else None,
        )
        db.add(draft)
        db.flush()
        return draft

    def _store_pending_message(self, db: Session, contact: Contact, draft: Draft, reply: Reply) -> None:
        if db.query(ConversationMessage).filter(ConversationMessage.external_message_id == f"draft:{draft.id}").first():
            return
        db.add(
            ConversationMessage(
                contact_id=contact.id,
                direction="outbound",
                subject=draft.subject,
                body=draft.body,
                source="auto_reply_proposed",
                auto_sent=False,
                external_message_id=f"draft:{draft.id}",
                occurred_at=utcnow(),
            )
        )

    async def _send_draft(self, db: Session, contact: Contact, draft: Draft, *, source: str, reply_id: str | None) -> AutoReplyResult:
        user = get_value(db, "gmail_user")
        password = get_secret(db, "gmail_app_password")
        idempotency_key = sha256_key("auto_reply", contact.id, reply_id or draft.id)
        existing = db.query(SendAttempt).filter(SendAttempt.idempotency_key == idempotency_key, SendAttempt.status == "success").first()
        if existing:
            return AutoReplyResult(action="skipped", mode="autonomous", reason="duplicate", message_id=existing.provider_msg_id, subject=draft.subject)
        sent_at = utcnow()
        result = await GmailAdapter.from_settings(db).send_message(contact.email, draft.subject, draft.body, user, password)
        if result.status != "success":
            db.add(
                SendAttempt(
                    queue_id="auto_reply",
                    contact_id=contact.id,
                    draft_id=draft.id,
                    idempotency_key=idempotency_key,
                    status="failed",
                    sender_identity=user,
                    error_code=result.error_code,
                    error_detail=result.error_detail,
                )
            )
            emit_event(db, "auto_reply.failed", entity_type="contact", entity_id=contact.id, payload={"draft_id": draft.id, "error_code": result.error_code})
            return AutoReplyResult(action="failed", mode="autonomous", draft_id=draft.id, reason=result.error_code, subject=draft.subject)
        db.add(
            SendAttempt(
                queue_id="auto_reply",
                contact_id=contact.id,
                draft_id=draft.id,
                idempotency_key=idempotency_key,
                provider_msg_id=result.provider_msg_id,
                smtp_response=result.smtp_response,
                status="success",
                sender_identity=user,
                sent_at=sent_at,
            )
        )
        message = db.query(ConversationMessage).filter(ConversationMessage.external_message_id == f"draft:{draft.id}").first()
        if message:
            message.source = source
            message.auto_sent = True
            message.external_message_id = result.provider_msg_id
            message.occurred_at = sent_at
        else:
            db.add(
                ConversationMessage(
                    contact_id=contact.id,
                    direction="outbound",
                    subject=draft.subject,
                    body=draft.body,
                    source=source,
                    auto_sent=True,
                    external_message_id=result.provider_msg_id,
                    occurred_at=sent_at,
                )
            )
        contact.status = "conversation_active"
        emit_event(db, "auto_reply.sent", entity_type="contact", entity_id=contact.id, payload={"draft_id": draft.id, "subject": draft.subject, "message_id": result.provider_msg_id})
        return AutoReplyResult(action="sent", mode="autonomous", draft_id=draft.id, message_id=result.provider_msg_id, subject=draft.subject)

    def _existing_pending_proposal(self, db: Session, reply_id: str) -> Draft | None:
        return (
            db.query(Draft)
            .filter(
                Draft.source == "auto_reply_proposed",
                Draft.notes == f"auto_reply_reply_id:{reply_id}",
                Draft.approved.is_(False),
                Draft.rejected.is_(False),
            )
            .first()
        )

    def _reply_id_from_notes(self, notes: str | None) -> str | None:
        if not notes:
            return None
        prefix = "auto_reply_reply_id:"
        if notes.startswith(prefix):
            return notes[len(prefix) :]
        return None

    def _repeats_previous_outbound(self, db: Session, contact_id: str, body: str) -> bool:
        previous = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.contact_id == contact_id, ConversationMessage.direction == "outbound")
            .order_by(ConversationMessage.occurred_at.desc(), ConversationMessage.created_at.desc())
            .first()
        )
        if not previous:
            return False
        current = self._word_ngrams(body, 10)
        prior = self._word_ngrams(previous.body, 10)
        return bool(current & prior)

    def _mentions_other_contact_detail(self, db: Session, contact: Contact, body: str) -> bool:
        normalized_body = " ".join((body or "").lower().split())
        if not normalized_body:
            return False
        signature = " ".join(_conversation_signature(db).lower().split())
        if signature:
            normalized_body = normalized_body.replace(signature, "")
        sender_name = " ".join((get_value(db, "sender_name", "") or "").lower().split())
        sender_role = " ".join((get_value(db, "sender_role", "") or "").lower().split())
        current_identifiers = self._strong_contact_identifiers(contact)
        other_contacts = db.query(Contact).filter(Contact.id != contact.id).all()
        for other in other_contacts:
            for candidate in self._strong_contact_identifiers(other):
                if candidate in {sender_name, sender_role}:
                    continue
                if any(candidate == current or candidate in current for current in current_identifiers):
                    continue
                if re.search(rf"(?<!\w){re.escape(candidate)}(?!\w)", normalized_body):
                    return True
        return False

    def _latest_unquoted_reply_text(self, raw_summary: str | None) -> str:
        raw = (raw_summary or "").strip()
        if not raw:
            return ""
        first_line = raw.splitlines()[0].strip()
        latest = re.split(r"\s+On\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),", first_line, maxsplit=1)[0].strip()
        return latest or first_line

    def _deterministic_safe_reply(self, contact: Contact, reply: Reply, history: list[ConversationMessage], db: Session) -> DraftSuggestion | None:
        latest = self._latest_unquoted_reply_text(reply.raw_summary)
        lowered = latest.lower()
        ack_lowered = re.sub(r"[^a-z0-9 ]+", "", lowered).strip()
        name = contact.creator_name or contact.business_name or "there"
        subject = self._reply_subject_from_history(history)
        offer = _conversation_offer_reference(db)
        if "quiz" in lowered or "python" in lowered:
            body = (
                f"{name}, yes. I would keep the next step grounded in the material and constraints you have already shared, "
                f"and tie it back to this offer: {offer}.\n\n"
                "That keeps the first version controlled and avoids guessing beyond approved source material.\n\n"
                "Would you like me to outline a narrow first version?\n\n"
                f"{_conversation_signature(db)}"
            )
        elif "hindi" in lowered or "language" in lowered:
            body = (
                f"{name}, yes. If language support matters, I would scope it only around examples and source material you approve, "
                f"and keep it tied to this offer: {offer}.\n\n"
                "Anything outside the supplied material should be routed back to you.\n\n"
                "Would you like me to outline a narrow first version?\n\n"
                f"{_conversation_signature(db)}"
            )
        elif re.fullmatch(r"(yes|yes i would|yes i would like that|yes i would like to|ok|okay|got it|ok got it|understood|sounds good|that works|makes sense)", ack_lowered):
            body = (
                f"{name}, understood. The next clean step is to pick one narrow first version tied to this offer: {offer}.\n\n"
                "That keeps scope controlled before expanding beyond the material you already trust.\n\n"
                "Would you like me to outline that first version?\n\n"
                f"{_conversation_signature(db)}"
            )
        else:
            return None
        return DraftSuggestion(subject=subject, body=body, warnings=["deterministic_quality_fallback"])

    def _strong_contact_identifiers(self, contact: Contact) -> set[str]:
        candidates = {
            contact.email,
            contact.creator_name,
            contact.business_name,
            contact.website_url,
        }
        if contact.website_url:
            website = re.sub(r"^https?://", "", contact.website_url.strip(), flags=re.IGNORECASE)
            website = re.sub(r"^www\.", "", website, flags=re.IGNORECASE).split("/")[0]
            candidates.add(website)
        return {
            " ".join(str(value).lower().split()).strip(" /")
            for value in candidates
            if value and len(" ".join(str(value).split()).strip(" /")) >= 6
        }

    def _word_ngrams(self, text: str, size: int) -> set[tuple[str, ...]]:
        words = [word.lower() for word in re.findall(r"\b\w+\b", text or "")]
        return {tuple(words[index : index + size]) for index in range(0, max(0, len(words) - size + 1))}
