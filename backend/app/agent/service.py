from __future__ import annotations

import json
import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.agent.catalog import check_capability_tiered, source_label_for_capability
from app.agent.campaign_intelligence import answer_awareness_query
from app.agent.channel_router import ChannelDecision, classify_channel
from app.agent.context_loader import build_context_card, generate_proactive_opening, is_context_stale
from app.agent.goal_frame import GoalFrameAgent
from app.agent.intent import IntentAgent
from app.agent.layman_formatter import build_contact_name_map, format_for_layman
from app.agent.memory import get_or_create_session, update_session
from app.agent.orchestrator import OrchestratorAgent
from app.agent.pending import cancel_pending_action, create_pending_action, validate_pending_action
from app.agent.reasoning import ReasoningAgent
from app.agent.response import ResponseAgent
from app.agent.schemas import AgentCancelRequest, AgentChatRequest, AgentChatResponse, AgentConfirmRequest, AgentDraft, EvidenceEnvelope, IntentDecision, PendingEmailAction, ToolPlan
from app.agent.slot import SlotAgent
from app.agent.tools import AgenticToolExecutor, sanitize_text
from app.agent.verifier import VerifierAgent
from app.audit.service import emit_event
from app.db.models import Contact, ConversationMessage, Draft, PendingEmailActionRow, Reply

MAX_AGENT_MESSAGE_CHARS = 4000
TAIL_PRESERVE_CHARS = 1800
IDENTIFIER_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b[a-f0-9]{32}\b", re.IGNORECASE)


class AgentService:
    def __init__(self) -> None:
        self.goal_frame = GoalFrameAgent()
        self.intent = IntentAgent()
        self.slot = SlotAgent()
        self.orchestrator = OrchestratorAgent()
        self.tools = AgenticToolExecutor()
        self.reasoning = ReasoningAgent()
        self.verifier = VerifierAgent()
        self.response = ResponseAgent()

    async def chat(self, request: AgentChatRequest, db: Session) -> AgentChatResponse:
        session = get_or_create_session(request.session_token, db)
        safe_message = _prepare_agent_message(request.message)

        contact_map = self._contact_map(session, db)
        turn_history = self._turn_history(session)
        if is_context_stale(getattr(session, "context_loaded_at", None)):
            session.context_summary = build_context_card(db)
            session.context_loaded_at = datetime.utcnow().isoformat()

        pending_ack = self._pending_ack_response(safe_message, session, contact_map, turn_history, db)
        if pending_ack:
            db.commit()
            return pending_ack

        greetings = {"", "hi", "hello", "hey", "start", "open", "help"}
        if safe_message.strip().lower() in greetings and not turn_history:
            opening = generate_proactive_opening(session.context_summary or "", db)
            formatted = format_for_layman(opening, contact_map)
            self._save_turn_state(session, safe_message, formatted, "awareness", "Campaign Overview", turn_history, db)
            emit_event(db, "agent.awareness_answered", entity_type="agent_session", entity_id=session.id, payload={"source": "Campaign Overview"})
            db.commit()
            return AgentChatResponse(response=formatted, source="Campaign Overview", intent="campaign_intelligence", channel="awareness")

        if self._is_help_question(safe_message):
            raw_response = self._help_response()
            formatted = format_for_layman(raw_response, contact_map)
            self._save_turn_state(session, safe_message, formatted, "awareness", "Assistant Help", turn_history, db)
            emit_event(db, "agent.static_help", entity_type="agent_session", entity_id=session.id, payload={"message": safe_message[:120]})
            db.commit()
            return AgentChatResponse(response=formatted, source="Assistant Help", intent="static_help", channel="awareness")

        contextual_status = self._contextual_reply_status(safe_message, session, db)
        if contextual_status:
            formatted = format_for_layman(contextual_status, contact_map)
            self._save_turn_state(session, safe_message, formatted, "awareness", "Conversation History", turn_history, db)
            emit_event(db, "agent.contextual_status_answered", entity_type="agent_session", entity_id=session.id, payload={"contact_id": getattr(session, "active_contact_id", None)})
            db.commit()
            return AgentChatResponse(response=formatted, source="Conversation History", intent="email_read_thread", channel="awareness")

        awaiting_contact_selection = self._awaiting_contact_selection(safe_message, session, turn_history)
        continuation_message = await self._contact_continuation_message(safe_message, session, turn_history, db)
        forced_channel = "task" if continuation_message else None
        if continuation_message:
            safe_message = continuation_message
        elif awaiting_contact_selection:
            raw_response = self._repeat_contact_clarification(session, db)
            formatted = format_for_layman(raw_response, contact_map)
            self._save_turn_state(session, safe_message, formatted, "task", "Contacts", turn_history, db)
            emit_event(db, "agent.clarification_asked", entity_type="agent_session", entity_id=session.id, payload={"missing": ["contact_id"], "reason": "unresolved_contact_selection"})
            db.commit()
            return AgentChatResponse(
                response=formatted,
                source="Contacts",
                intent="contact_resolve",
                channel="task",
                is_clarification=True,
                error_code="missing_slots",
            )
        elif self._is_acknowledgement(safe_message):
            raw_response = "Got it. Ask me about a reply, thread, draft, queue, follow-up, or contact when you are ready."
            formatted = format_for_layman(raw_response, contact_map)
            self._save_turn_state(session, safe_message, formatted, "awareness", "Assistant Help", turn_history, db)
            emit_event(db, "agent.acknowledgement", entity_type="agent_session", entity_id=session.id, payload={"message": safe_message[:40]})
            db.commit()
            return AgentChatResponse(response=formatted, source="Assistant Help", intent="acknowledgement", channel="awareness")

        if forced_channel:
            channel_decision = ChannelDecision(channel=forced_channel, confidence=1.0, routing_reason="continued_pending_contact_task")
            current_channel = forced_channel
        else:
            channel_decision = await classify_channel(
                message=safe_message,
                context_hint=(session.context_summary or "")[:150],
            )
            current_channel = self._coerce_channel_for_existing_pipeline(safe_message, channel_decision.channel)
        session.current_channel = current_channel

        if current_channel == "awareness":
            raw_response = await answer_awareness_query(
                question=safe_message,
                db=db,
                session_context=session.context_summary or "",
                turn_history=turn_history,
            )
            formatted = format_for_layman(raw_response, contact_map)
            self._remember_single_contact_from_text(formatted, session, db)
            self._save_turn_state(session, safe_message, formatted, current_channel, "Campaign Data", turn_history, db)
            emit_event(
                db,
                "agent.awareness_answered",
                entity_type="agent_session",
                entity_id=session.id,
                payload={"routing_reason": channel_decision.routing_reason, "confidence": channel_decision.confidence},
            )
            db.commit()
            return AgentChatResponse(
                response=formatted,
                source="Campaign Data",
                intent=self._awareness_intent(safe_message),
                channel=current_channel,
            )

        goal = self.goal_frame.propose(safe_message, session.context_summary)
        if not goal.proposed_capability:
            raw_response = "I cannot perform that through the email assistant. I can read replies, search threads, draft replies, check queue/follow-ups, and send only after confirmation."
            formatted = format_for_layman(raw_response, contact_map)
            self._save_turn_state(session, safe_message, formatted, current_channel, "System", turn_history, db)
            emit_event(db, "agent.capability_denied", entity_type="agent_session", entity_id=session.id, payload={"message": safe_message[:120]})
            db.commit()
            return AgentChatResponse(
                response=formatted,
                source="System",
                intent="unsupported",
                channel=current_channel,
                error_code="capability_denied",
            )
        emit_event(db, "agent.goal_framed", entity_type="agent_session", entity_id=session.id, payload={"capability": goal.proposed_capability})
        intent = self.intent.decide(safe_message, session.context_summary, goal)
        emit_event(db, "agent.intent_resolved", entity_type="agent_session", entity_id=session.id, payload={"capability": intent.capability})
        capability_check = check_capability_tiered(intent.capability, current_channel)
        if capability_check.redirect_to == "campaign_intelligence":
            raw_response = await answer_awareness_query(
                question=safe_message,
                db=db,
                session_context=session.context_summary or "",
                turn_history=turn_history,
            )
            formatted = format_for_layman(raw_response, contact_map)
            self._remember_single_contact_from_text(formatted, session, db)
            self._save_turn_state(session, safe_message, formatted, current_channel, "Campaign Data", turn_history, db)
            emit_event(
                db,
                "agent.capability_redirected",
                entity_type="agent_session",
                entity_id=session.id,
                payload={"capability": intent.capability, "redirect_to": capability_check.redirect_to, "channel": current_channel},
            )
            db.commit()
            return AgentChatResponse(
                response=formatted,
                source="Campaign Data",
                intent=capability_check.redirect_to,
                channel=current_channel,
            )
        if not capability_check.allowed:
            raw_response = "That request requires a supported action path. I did not change anything."
            formatted = format_for_layman(raw_response, contact_map)
            self._save_turn_state(session, safe_message, formatted, current_channel, "System", turn_history, db)
            emit_event(
                db,
                "agent.capability_denied",
                entity_type="agent_session",
                entity_id=session.id,
                payload={"capability": intent.capability, "channel": current_channel, "reason": capability_check.denial_reason},
            )
            db.commit()
            return AgentChatResponse(
                response=formatted,
                source="System",
                intent=intent.intent,
                channel=current_channel,
                is_clarification=True,
                error_code="capability_denied",
            )
        slots = self.slot.extract(safe_message, session.context_summary, intent)
        self._apply_active_contact_if_continuation(safe_message, intent.capability, slots, session, db)
        emit_event(db, "agent.slots_filled", entity_type="agent_session", entity_id=session.id, payload={"slots": slots.slots_filled, "missing": slots.slots_missing})

        evidence: list[EvidenceEnvelope] = []
        if intent.capability == "email_send_draft":
            raw_response = "Sending requires the Confirm button for a pending draft. I did not send anything."
            formatted = format_for_layman(raw_response, contact_map)
            self._save_turn_state(session, safe_message, formatted, current_channel, "System", turn_history, db)
            emit_event(db, "agent.confirmation_invalid", entity_type="agent_session", entity_id=session.id, payload={"status": "confirmation_required"})
            db.commit()
            return AgentChatResponse(
                response=formatted,
                source="System",
                intent=intent.intent,
                channel=current_channel,
                is_clarification=True,
                error_code="confirmation_required",
            )

        if slots.slots_missing:
            next_slots = dict(slots.slots_filled)
            if intent.capability in {"email_read_thread", "email_generate_draft"} and "contact_id" in slots.slots_missing:
                raw_response, next_slots = self._contact_clarification_message(intent.capability, next_slots, db)
            else:
                raw_response = slots.clarification_question or "I need one more detail before I can continue."
            formatted = format_for_layman(raw_response, contact_map)
            self._save_turn_state(session, safe_message, formatted, current_channel, "System", turn_history, db)
            emit_event(db, "agent.clarification_asked", entity_type="agent_session", entity_id=session.id, payload={"missing": slots.slots_missing})
            update_session(session.id, {"current_goal": goal.user_goal, "slots": next_slots}, db)
            db.commit()
            return AgentChatResponse(
                response=formatted,
                source="System",
                intent=intent.intent,
                channel=current_channel,
                is_clarification=True,
                error_code="missing_slots",
            )

        if intent.capability in {"email_read_thread", "email_generate_draft"} and not slots.slots_filled.get("contact_id"):
            resolved = await self._resolve_contact(slots.slots_filled.get("name_or_email"), session, db)
            evidence.extend(resolved["evidence"])
            if not resolved.get("contact_id"):
                formatted = format_for_layman(resolved["message"], contact_map)
                self._save_turn_state(session, safe_message, formatted, current_channel, "Contacts", turn_history, db)
                emit_event(db, "agent.clarification_asked", entity_type="agent_session", entity_id=session.id, payload={"missing": ["contact_id"]})
                next_slots = dict(slots.slots_filled)
                candidate_ids = resolved.get("candidate_contact_ids") or []
                if candidate_ids:
                    next_slots["candidate_contact_ids"] = candidate_ids
                update_session(session.id, {"current_goal": goal.user_goal, "slots": next_slots}, db)
                db.commit()
                return AgentChatResponse(
                    response=formatted,
                    source="Contacts",
                    intent=intent.intent,
                    channel=current_channel,
                    is_clarification=True,
                    error_code="missing_slots",
                )
            slots.slots_filled["contact_id"] = resolved["contact_id"]

        plans = self.orchestrator.plan(intent, {**slots.slots_filled, "provider": request.provider}, session)
        for plan in plans:
            evidence.append(await self.tools.execute(plan, session, db))

        active_contact_id = self._active_contact_id_from_evidence(evidence, slots.slots_filled, session, db)
        reasoning = self.reasoning.reason(safe_message, intent, evidence)
        verification = self.verifier.verify(safe_message, intent, reasoning)
        response_text = self.response.compose(safe_message, intent, verification, evidence)
        response_text = format_for_layman(response_text, contact_map)
        draft = None
        pending = None
        if intent.capability == "email_generate_draft" and evidence and evidence[-1].status == "success":
            draft = self._draft_from_evidence(evidence[-1], db)
            if draft:
                action = create_pending_action(session.id, draft.draft_id, draft.contact_id, draft.subject, draft.body, db)
                emit_event(db, "agent.confirmation_created", entity_type="pending_email_action", entity_id=action.id, payload={"draft_id": draft.draft_id})
                pending = self._pending_to_schema(action, draft)
        update_session(
            session.id,
            {
                "current_goal": goal.user_goal,
                "active_contact_id": active_contact_id,
                "slots": slots.slots_filled,
                "context_summary": session.context_summary,
                "context_loaded_at": session.context_loaded_at,
                "contact_name_map": json.dumps(contact_map, sort_keys=True),
                "turn_history": json.dumps(self._updated_turn_history(turn_history, safe_message, response_text, current_channel, source_label_for_capability(intent.capability))),
                "current_channel": current_channel,
            },
            db,
        )
        db.commit()
        return AgentChatResponse(
            response=response_text,
            source=source_label_for_capability(intent.capability),
            intent=intent.intent,
            channel=current_channel,
            draft=draft,
            pending_action=pending,
            error_code=evidence[-1].error_code if evidence and evidence[-1].status in {"error", "denied"} else None,
        )

    async def confirm(self, request: AgentConfirmRequest, db: Session) -> AgentChatResponse:
        session = get_or_create_session(request.session_token, db)
        action = db.get(PendingEmailActionRow, request.action_id)
        if action is None:
            emit_event(db, "agent.confirmation_invalid", entity_type="pending_email_action", entity_id=request.action_id, payload={"status": "not_found"})
            db.commit()
            return self._invalid_confirmation("not_found")
        status = validate_pending_action(action.id, session.id, action.draft_id, db)
        if status != "valid":
            event_type = "agent.confirmation_expired" if status == "expired" else "agent.confirmation_invalid"
            emit_event(db, event_type, entity_type="pending_email_action", entity_id=action.id, payload={"status": status})
            db.commit()
            return self._invalid_confirmation(status)
        intent = IntentDecision(intent="email_send_draft", capability="email_send_draft", requires_confirmation=True, confidence=1.0)
        plan = ToolPlan(
            capability="email_send_draft",
            params={"draft_id": action.draft_id, "_confirmed_action_id": action.id},
            side_effect=True,
            source_label="Email Provider",
            reason="user clicked Confirm",
        )
        evidence = [await self.tools.execute(plan, session, db)]
        reasoning = self.reasoning.reason("send confirmed draft", intent, evidence)
        verification = self.verifier.verify("send confirmed draft", intent, reasoning)
        response_text = self.response.compose("send confirmed draft", intent, verification, evidence)
        db.commit()
        return AgentChatResponse(
            response=response_text,
            source="Email Provider" if evidence[-1].status == "success" else "System",
            intent="email_send_draft",
            channel="action",
            error_code=evidence[-1].error_code,
        )

    async def cancel(self, request: AgentCancelRequest, db: Session) -> AgentChatResponse:
        session = get_or_create_session(request.session_token, db)
        cancel_pending_action(session.id, db)
        update_session(session.id, {"current_goal": None, "slots": {}, "active_contact_id": None, "context_summary": "User cancelled the pending agent action."}, db)
        emit_event(db, "agent.session_cancelled", entity_type="agent_session", entity_id=session.id)
        db.commit()
        return AgentChatResponse(response="Cancelled. I did not send anything.", source="System", intent="cancel", channel="action")

    async def _resolve_contact(self, name_or_email: str | None, session, db: Session) -> dict:
        if not name_or_email:
            return {"contact_id": None, "message": "Which contact should I use?", "evidence": []}
        evidence = [
            await self.tools.execute(
                ToolPlan(capability="contact_resolve", params={"name_or_email": name_or_email}, side_effect=False, source_label="Contacts"),
                session,
                db,
            )
        ]
        data = evidence[-1].data
        if data.get("status") == "needs_clarification":
            candidate_ids = [str(item.get("id")) for item in data.get("items") or [] if item.get("id")]
            return {
                "contact_id": None,
                "message": data.get("clarification_question") or "Which contact should I use?",
                "evidence": evidence,
                "candidate_contact_ids": candidate_ids,
            }
        items = data.get("items") or []
        if len(items) == 1:
            return {"contact_id": items[0]["id"], "message": "", "evidence": evidence}
        if len(items) > 1:
            choices = [
                self._contact_choice_line(index, db.get(Contact, item["id"]) or item, db)
                for index, item in enumerate(items[:5], start=1)
            ]
            return {
                "contact_id": None,
                "message": "I found multiple contacts. Which one should I use?\n" + "\n".join(choices),
                "evidence": evidence,
                "candidate_contact_ids": [str(item.get("id")) for item in items[:5] if item.get("id")],
            }
        return {"contact_id": None, "message": "I could not find that contact. Which contact should I use?", "evidence": evidence}

    def _draft_from_evidence(self, evidence: EvidenceEnvelope, db: Session | None = None) -> AgentDraft | None:
        draft_id = evidence.data.get("draft_id")
        contact_id = evidence.data.get("contact_id")
        if not draft_id or not contact_id:
            return None
        stored = db.get(Draft, str(draft_id)) if db else None
        contact = db.get(Contact, str(contact_id)) if db else None
        return AgentDraft(
            draft_id=str(draft_id),
            contact_id=str(contact_id),
            to=(contact.email if contact else str(evidence.data.get("to") or "")),
            subject=(stored.subject if stored else str(evidence.data.get("subject") or "")),
            body=(stored.body if stored else str(evidence.data.get("body") or "")),
            warnings=(json.loads(stored.warnings or "[]") if stored and stored.warnings else list(evidence.data.get("warnings") or [])),
        )

    def _pending_to_schema(self, action: PendingEmailActionRow, draft: AgentDraft) -> PendingEmailAction:
        return PendingEmailAction(
            action_id=action.id,
            draft_id=action.draft_id,
            contact_id=action.contact_id,
            to=draft.to,
            subject=draft.subject,
            body=draft.body,
            confirmation_prompt=action.confirmation_prompt,
            expires_at=action.expires_at,
        )

    def _pending_ack_response(self, message: str, session, contact_map: dict[str, str], turn_history: list[dict], db: Session) -> AgentChatResponse | None:
        if message.strip().lower() not in {"ok", "okay", "yes", "yep", "sure", "go ahead", "looks good"}:
            return None
        action_id = getattr(session, "pending_action_id", None)
        if not action_id:
            return None
        action = db.get(PendingEmailActionRow, action_id)
        if not action or action.consumed:
            return None
        status = validate_pending_action(action.id, session.id, action.draft_id, db)
        if status != "valid":
            response = self._invalid_confirmation(status)
            self._save_turn_state(session, message, response.response, "action", "System", turn_history, db)
            return response
        draft = db.get(Draft, action.draft_id)
        if not draft:
            return None
        contact = db.get(Contact, draft.contact_id)
        agent_draft = AgentDraft(
            draft_id=draft.id,
            contact_id=draft.contact_id,
            to=contact.email if contact else "",
            subject=draft.subject,
            body=draft.body,
            warnings=json.loads(draft.warnings or "[]") if draft.warnings else [],
        )
        raw_response = "The draft is ready below. Please review it, then use Confirm to send or Cancel to stop. I will not send from an 'ok' chat message alone."
        formatted = format_for_layman(raw_response, contact_map)
        self._save_turn_state(session, message, formatted, "action", "System", turn_history, db)
        return AgentChatResponse(
            response=formatted,
            source="System",
            intent="email_send_draft",
            channel="action",
            is_clarification=True,
            draft=agent_draft,
            pending_action=self._pending_to_schema(action, agent_draft),
            error_code="confirmation_required",
        )

    def _summary(self, capability: str, evidence: list[EvidenceEnvelope]) -> str:
        payload = {"last_capability": capability, "statuses": [item.status for item in evidence][-3:]}
        return sanitize_text(json.dumps(payload, sort_keys=True), limit=500)

    def _awaiting_contact_selection(self, message: str, session, turn_history: list[dict]) -> bool:
        pending_goal = str(getattr(session, "current_goal", None) or "").strip()
        return bool(pending_goal and self._goal_waits_for_contact(pending_goal) and self._looks_like_contact_selection(message, turn_history))

    async def _contact_continuation_message(self, message: str, session, turn_history: list[dict], db: Session) -> str | None:
        pending_goal = str(getattr(session, "current_goal", None) or "").strip()
        if not pending_goal or not self._goal_waits_for_contact(pending_goal):
            return None
        if not self._looks_like_contact_selection(message, turn_history):
            return None
        contact = await self._contact_from_selection(message, session, db)
        if not contact:
            return None
        session.active_contact_id = contact.id
        return f"{pending_goal} for {contact.email}"

    def _goal_waits_for_contact(self, goal: str) -> bool:
        lowered = goal.lower()
        return any(phrase in lowered for phrase in ("generate", "draft", "response", "reply", "thread", "conversation", "show"))

    def _looks_like_contact_selection(self, message: str, turn_history: list[dict]) -> bool:
        lowered = message.lower().strip()
        if not lowered or self._is_help_question(message):
            return False
        if len(lowered) > 90:
            return False
        if any(word in lowered for word in ("queue", "follow", "send", "delete", "suppress", "approve", "campaign")):
            return False
        if any(phrase in lowered for phrase in ("main one", "the main", "that one", "this one", "first one", "top one", "same one", "ok", "okay", "yes")):
            return True
        if re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", message):
            return True
        last_assistant = next((turn for turn in reversed(turn_history) if turn.get("role") == "assistant"), {})
        return "which contact" in str(last_assistant.get("text", "")).lower()

    async def _contact_from_selection(self, message: str, session, db: Session) -> Contact | None:
        lowered = message.lower().strip()
        if any(phrase in lowered for phrase in ("main one", "the main", "that one", "this one", "first one", "top one", "same one", "ok", "okay", "yes")):
            candidates = self._stored_candidate_contacts(session, db)
            if candidates:
                return candidates[0]
            if any(phrase in lowered for phrase in ("same one", "that one", "this one")):
                active_contact_id = getattr(session, "active_contact_id", None)
                active = db.get(Contact, active_contact_id) if active_contact_id else None
                if active and not active.deleted_at:
                    return active
            return None
        resolved = await self._resolve_contact(message, session, db)
        contact_id = resolved.get("contact_id")
        return db.get(Contact, contact_id) if contact_id else None

    def _apply_active_contact_if_continuation(self, message: str, capability: str, slots, session, db: Session) -> None:
        if capability not in {"email_generate_draft", "email_read_thread"}:
            return
        if slots.slots_filled.get("contact_id") or slots.slots_filled.get("name_or_email"):
            return
        if not self._should_use_active_contact(message, capability):
            return
        active_contact_id = getattr(session, "active_contact_id", None)
        contact = db.get(Contact, active_contact_id) if active_contact_id else None
        if not contact or contact.deleted_at:
            return
        slots.slots_filled["contact_id"] = contact.id
        slots.slots_missing = [slot for slot in slots.slots_missing if slot != "contact_id"]
        slots.ready_to_execute = not slots.slots_missing

    def _should_use_active_contact(self, message: str, capability: str) -> bool:
        lowered = message.lower().strip()
        continuation_terms = (
            "another",
            "same",
            "that",
            "this",
            "he",
            "she",
            "him",
            "her",
            "them",
            "their",
            "follow",
            "reply",
            "response",
            "continue",
            "ask",
            "asking",
        )
        if any(term in lowered for term in continuation_terms):
            return True
        return capability == "email_generate_draft" and lowered in {"generate a response", "draft a response", "write a reply"}

    def _active_contact_id_from_evidence(self, evidence: list[EvidenceEnvelope], slots: dict, session, db: Session) -> str | None:
        explicit_contact_id = slots.get("contact_id")
        if explicit_contact_id:
            return str(explicit_contact_id)
        found: list[str] = []
        for item in evidence:
            for contact_id in self._contact_ids_from_data(item.data):
                if contact_id not in found:
                    found.append(contact_id)
        if len(found) == 1:
            contact = db.get(Contact, found[0])
            if contact and not contact.deleted_at:
                return contact.id
        current = getattr(session, "active_contact_id", None)
        if current and current in found:
            return current
        return current

    def _contact_ids_from_data(self, data: dict) -> list[str]:
        found: list[str] = []
        contact_id = data.get("contact_id")
        if contact_id:
            found.append(str(contact_id))
        contact = data.get("contact")
        if isinstance(contact, dict) and contact.get("id"):
            found.append(str(contact["id"]))
        for item in data.get("items") or []:
            if isinstance(item, dict) and item.get("contact_id"):
                found.append(str(item["contact_id"]))
            if isinstance(item, dict) and item.get("id") and data.get("status") in {"resolved", "needs_clarification"}:
                found.append(str(item["id"]))
        return found

    def _remember_single_contact_from_text(self, text: str, session, db: Session) -> None:
        emails: list[str] = []
        for email in re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text):
            normalized = email.strip().lower()
            if normalized not in emails:
                emails.append(normalized)
        if len(emails) != 1:
            contact = self._single_contact_named_in_text(text, db)
            if contact:
                session.active_contact_id = contact.id
            return
        contact = db.query(Contact).filter(Contact.email == emails[0], Contact.deleted_at.is_(None)).first()
        if contact:
            session.active_contact_id = contact.id

    def _single_contact_named_in_text(self, text: str, db: Session) -> Contact | None:
        lowered = text.lower()
        matches: dict[str, Contact] = {}
        contacts = db.query(Contact).filter(Contact.deleted_at.is_(None)).all()
        for contact in contacts:
            labels = [contact.creator_name, contact.business_name]
            for label in labels:
                label_text = " ".join(str(label or "").lower().split())
                if len(label_text) < 3:
                    continue
                if re.search(r"\b" + re.escape(label_text) + r"\b", lowered):
                    matches[contact.id] = contact
        if len(matches) == 1:
            return next(iter(matches.values()))
        return None

    def _contact_clarification_message(self, capability: str, slots: dict, db: Session) -> tuple[str, dict]:
        next_slots = dict(slots)
        candidates = self._contact_candidates_for_pending_task(capability, db)
        next_slots["candidate_contact_ids"] = [contact.id for contact in candidates]
        if candidates:
            return self._format_contact_choices(candidates, db), next_slots
        if capability == "email_generate_draft":
            return (
                "Which contact should I use? I do not see anyone currently waiting for a response, so type the exact name or email if you want a new draft.",
                next_slots,
            )
        return "Which contact should I use? Type their name or email.", next_slots

    def _repeat_contact_clarification(self, session, db: Session) -> str:
        candidates = self._stored_candidate_contacts(session, db)
        if candidates:
            return self._format_contact_choices(candidates, db)
        return "Which contact should I use? Type their name or email."

    def _stored_candidate_contacts(self, session, db: Session) -> list[Contact]:
        slots = self._json_field(getattr(session, "slots", None), {})
        candidate_ids = slots.get("candidate_contact_ids") if isinstance(slots, dict) else []
        if not isinstance(candidate_ids, list):
            return []
        contacts: list[Contact] = []
        for contact_id in candidate_ids[:5]:
            contact = db.get(Contact, str(contact_id))
            if contact and not contact.deleted_at:
                contacts.append(contact)
        return contacts

    def _contact_candidates_for_pending_task(self, capability: str, db: Session) -> list[Contact]:
        contacts = db.query(Contact).filter(Contact.deleted_at.is_(None)).all()
        if not contacts:
            return []
        scored: list[tuple[tuple[int, int, float], Contact]] = []
        for contact in contacts:
            unanswered = self._contact_has_unanswered_inbound(contact, db)
            if capability == "email_generate_draft" and not unanswered:
                continue
            status_score = 2 if contact.status in {"replied", "conversation_active"} else 1 if contact.status in {"draft_ready", "draft_needed"} else 0
            reply_count = db.query(Reply).filter(Reply.contact_id == contact.id, Reply.archived_at.is_(None)).count()
            latest = (
                db.query(ConversationMessage)
                .filter(ConversationMessage.contact_id == contact.id)
                .order_by(ConversationMessage.occurred_at.desc(), ConversationMessage.created_at.desc())
                .first()
            )
            latest_ts = latest.occurred_at or latest.created_at if latest else getattr(contact, "updated_at", None)
            timestamp = latest_ts.timestamp() if hasattr(latest_ts, "timestamp") else 0.0
            scored.append(((3 if unanswered else 0, status_score + reply_count, timestamp), contact))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [contact for _score, contact in scored[:5]]

    def _format_contact_choices(self, contacts: list[Contact], db: Session) -> str:
        lines = ["Which contact should I use?"]
        for index, contact in enumerate(contacts[:5], start=1):
            lines.append(self._contact_choice_line(index, contact, db))
        return "\n".join(lines)

    def _contact_choice_line(self, index: int, contact: Contact | dict, db: Session | None) -> str:
        if isinstance(contact, dict):
            name = contact.get("name") or contact.get("creator_name") or contact.get("business_name") or contact.get("email") or "Contact"
            email = contact.get("email") or ""
            status = contact.get("status") or ""
            return f"{index}. {name} ({email}){f' - {status}' if status else ''}"
        name = contact.creator_name or contact.business_name or contact.email
        label = self._contact_attention_label(contact, db) if db else "needs your response"
        return f"{index}. {name} ({contact.email}) - {label}"

    def _contact_attention_label(self, contact: Contact, db: Session | None) -> str:
        if db and self._contact_has_unanswered_inbound(contact, db):
            return "needs your response"
        if contact.status in {"replied", "conversation_active"}:
            return "has replied"
        if contact.status == "draft_ready":
            return "has a draft ready"
        return "available"

    def _contact_has_unanswered_inbound(self, contact: Contact, db: Session) -> bool:
        latest_inbound = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.contact_id == contact.id, ConversationMessage.direction == "inbound")
            .order_by(ConversationMessage.occurred_at.desc(), ConversationMessage.created_at.desc())
            .first()
        )
        if not latest_inbound:
            return False
        latest_outbound = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.contact_id == contact.id, ConversationMessage.direction == "outbound")
            .order_by(ConversationMessage.occurred_at.desc(), ConversationMessage.created_at.desc())
            .first()
        )
        if not latest_outbound:
            return True
        return (latest_inbound.occurred_at or latest_inbound.created_at) > (latest_outbound.occurred_at or latest_outbound.created_at)

    def _contextual_reply_status(self, message: str, session, db: Session) -> str | None:
        lowered = message.lower().strip()
        if _looks_like_reply_draft_request(lowered):
            return None
        if not (("reply" in lowered or "respond" in lowered) and any(word in lowered for word in ("pending", "back", "sent", "still", "did", "have"))):
            return None
        pending_notice = self._pending_action_status_notice(session, db)
        if pending_notice:
            return pending_notice
        if not getattr(session, "active_contact_id", None):
            return None
        contact = db.get(Contact, session.active_contact_id)
        if not contact:
            return None
        latest_inbound = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.contact_id == contact.id, ConversationMessage.direction == "inbound")
            .order_by(ConversationMessage.occurred_at.desc(), ConversationMessage.created_at.desc())
            .first()
        )
        latest_outbound = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.contact_id == contact.id, ConversationMessage.direction == "outbound")
            .order_by(ConversationMessage.occurred_at.desc(), ConversationMessage.created_at.desc())
            .first()
        )
        name = contact.creator_name or contact.business_name or contact.email
        if latest_outbound and (not latest_inbound or (latest_outbound.occurred_at or latest_outbound.created_at) >= (latest_inbound.occurred_at or latest_inbound.created_at)):
            return f"Yes. You already replied to {name}. Latest sent subject: {latest_outbound.subject or 'conversation reply'}."
        if latest_inbound:
            preview = sanitize_text(latest_inbound.body or latest_inbound.subject or "", limit=180)
            return f"No. {name} is still waiting for your response. Latest reply: {preview}"
        return f"I do not see an inbound reply from {name} yet, so there is no pending reply to answer."

    def _pending_action_status_notice(self, session, db: Session) -> str | None:
        action_id = getattr(session, "pending_action_id", None)
        if not action_id:
            return None
        action = db.get(PendingEmailActionRow, action_id)
        if not action or action.consumed:
            return None
        if validate_pending_action(action.id, session.id, action.draft_id, db) != "valid":
            return None
        active_contact_id = getattr(session, "active_contact_id", None)
        if active_contact_id and active_contact_id != action.contact_id:
            return None
        contact = db.get(Contact, action.contact_id)
        draft = db.get(Draft, action.draft_id)
        if not contact or not draft:
            return None
        name = contact.creator_name or contact.business_name or contact.email
        session.active_contact_id = contact.id
        return f"A draft for {name} is waiting for your confirmation. Use Confirm to send it, or Cancel to stop. Subject: {draft.subject}"

    def _is_help_question(self, message: str) -> bool:
        lowered = message.lower().strip()
        return lowered in {"what can u do", "what can you do", "how can i use this", "how do i use this", "help me use this"} or any(
            phrase in lowered for phrase in ("what can this assistant do", "what can the assistant do", "how can i use the assistant")
        )

    def _is_acknowledgement(self, message: str) -> bool:
        return message.lower().strip() in {"ok", "okay", "thanks", "thank you", "got it", "cool", "fine"}

    def _help_response(self) -> str:
        return (
            "I can help with Finimatic campaign work:\n"
            "1. Reply questions - ask who replied, what they said, or whether a contact still needs your response.\n"
            "2. Generate drafts - say \"generate a response for Sachi\" or pick a contact after I ask.\n"
            "3. Review threads - ask for a contact's latest message or full thread summary.\n"
            "4. Queue and follow-ups - ask what is pending, blocked, sent, or due.\n"
            "5. Safe sending - I can prepare a draft, but sending still requires the Confirm button."
        )

    def _invalid_confirmation(self, status: str) -> AgentChatResponse:
        messages = {
            "not_found": "confirmation_required: I could not find that pending send action. I did not send anything.",
            "expired": "That confirmation expired. Please generate the draft again.",
            "consumed": "That message was already sent or cancelled. I won't send it again.",
            "session_mismatch": "That confirmation belongs to a different session. I did not send anything.",
            "draft_mismatch": "That confirmation no longer matches the draft. I did not send anything.",
            "hash_mismatch": "The draft changed after confirmation was created. Please review it again before sending.",
        }
        return AgentChatResponse(response=messages.get(status, "That confirmation is not valid. I did not send anything."), source="System", intent="email_send_draft", channel="action", error_code=status)

    def _contact_map(self, session, db: Session) -> dict[str, str]:
        existing = self._json_field(getattr(session, "contact_name_map", None), {})
        if isinstance(existing, dict) and existing:
            return {str(key): str(value) for key, value in existing.items()}
        contacts = db.query(Contact).filter(Contact.deleted_at.is_(None)).all()
        contact_map = build_contact_name_map(contacts)
        session.contact_name_map = json.dumps(contact_map, sort_keys=True)
        return contact_map

    def _turn_history(self, session) -> list[dict]:
        value = self._json_field(getattr(session, "turn_history", None), [])
        return value if isinstance(value, list) else []

    def _save_turn_state(
        self,
        session,
        message: str,
        response: str,
        channel: str,
        source: str,
        turn_history: list[dict],
        db: Session,
    ) -> None:
        update_session(
            session.id,
            {
                "turn_history": json.dumps(self._updated_turn_history(turn_history, message, response, channel, source)),
                "current_channel": channel,
                "context_summary": getattr(session, "context_summary", None),
                "context_loaded_at": getattr(session, "context_loaded_at", None),
                "contact_name_map": getattr(session, "contact_name_map", None),
                "active_contact_id": getattr(session, "active_contact_id", None),
            },
            db,
        )

    def _updated_turn_history(self, turn_history: list[dict], message: str, response: str, channel: str, source: str) -> list[dict]:
        updated = list(turn_history)
        updated.append({"role": "user", "text": message[:200], "channel": channel})
        updated.append({"role": "assistant", "text": response[:300], "source": source})
        return updated[-20:]

    def _json_field(self, value, fallback):
        if value in (None, ""):
            return fallback
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except Exception:
            return fallback

    def _awareness_intent(self, message: str) -> str:
        lowered = message.lower()
        if "reply" in lowered or "repl" in lowered:
            return "email_read_inbox"
        if "queue" in lowered:
            return "queue_status"
        if "follow" in lowered:
            return "followup_status"
        return "campaign_intelligence"

    def _coerce_channel_for_existing_pipeline(self, message: str, channel: str) -> str:
        lowered = message.lower().strip()
        awareness_phrases = (
            "who all replied",
            "show me the replys",
            "show me the replies",
            "show replies",
            "show the replies",
            "received any reply",
            "received any replies",
            "have i received any reply",
            "have i received any replies",
            "reply recently",
            "replies recently",
            "who have i replied",
            "who did i reply",
            "whom all have i replied",
            "whom did i reply",
            "i have replied",
            "i replied",
            "who have i responded",
            "who did i respond",
            "i have responded",
            "who all did i send",
            "who did i send",
            "whom did i send",
            "which contacts did i send",
        )
        if any(phrase in lowered for phrase in awareness_phrases):
            return "awareness"
        if any(phrase in lowered for phrase in ("send it", "send draft", "send email", "confirm send", "approve", "suppress", "cancel", "activate", "delete")):
            return "action"
        if any(phrase in lowered for phrase in ("generate", "compose", "draft", "thread", "conversation", "most recent message", "latest message", "what did", "current status", "currently suppressed", "is suppressed", "queue", "follow", "autonomous", "reply them back", "reply him back", "reply her back", "reply back", "write a reply")):
            return "task"
        if "who replied today" in lowered or "replied today" in lowered or "how many contacts replied" in lowered:
            return "task"
        return channel


def _prepare_agent_message(message: str) -> str:
    if len(message) <= MAX_AGENT_MESSAGE_CHARS:
        return sanitize_text(message, limit=MAX_AGENT_MESSAGE_CHARS)
    head = message[: MAX_AGENT_MESSAGE_CHARS - TAIL_PRESERVE_CHARS]
    tail = message[-TAIL_PRESERVE_CHARS:]
    identifiers = []
    for match in IDENTIFIER_RE.finditer(message):
        token = match.group(0)
        if token not in identifiers:
            identifiers.append(token)
    identifier_text = f"\nReferenced identifiers: {' '.join(identifiers[:10])}" if identifiers else ""
    bounded = f"{head}\n...[message truncated; tail preserved]...\n{tail}{identifier_text}"
    return sanitize_text(bounded, limit=MAX_AGENT_MESSAGE_CHARS + 500)


def _looks_like_reply_draft_request(lowered: str) -> bool:
    status_question = lowered.startswith(("did ", "have ", "has ", "is ", "are ")) or "pending" in lowered or "already" in lowered
    action_request = any(word in lowered for word in ("can you", "can u", "please", "write", "draft", "generate", "create", "prepare"))
    if status_question and not action_request:
        return False
    if action_request and any(word in lowered for word in ("reply", "response", "follow-up", "follow up")):
        return True
    return any(
        phrase in lowered
        for phrase in (
            "reply them back",
            "reply him back",
            "reply her back",
            "reply back",
            "write a reply",
            "draft a reply",
            "generate a reply",
            "generate a response",
            "draft a response",
        )
    )
