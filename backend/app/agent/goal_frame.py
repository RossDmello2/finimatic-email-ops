from __future__ import annotations

from app.agent.catalog import get_capability
from app.agent.schemas import GoalFrame


class GoalFrameAgent:
    def propose(self, message: str, session_summary: str | None = None) -> GoalFrame:
        text = message.strip()
        lowered = text.lower()
        capability = _capability_from_text(lowered)
        if capability == "unsupported":
            return GoalFrame(
                user_goal=text,
                action_class="unsupported",
                proposed_capability="",
                confidence=0.4,
                reason="No approved email assistant capability matched.",
            )
        spec = get_capability(capability) or {}
        return GoalFrame(
            user_goal=text,
            action_class=str(spec.get("class") or "private_read"),  # type: ignore[arg-type]
            proposed_capability=capability,
            required_slots=list(spec.get("required_slots", [])),
            confidence=0.8,
            reason="Matched approved email assistant capability.",
        )


def _capability_from_text(lowered: str) -> str:
    if any(
        phrase in lowered
        for phrase in (
            "delete",
            "forward",
            "change password",
            "api key",
            "settings key",
            "run any tool",
            "write anything",
            "change settings",
            "send any email",
        )
    ):
        return "unsupported"
    if "workflow" in lowered and any(phrase in lowered for phrase in ("retry", "failed")):
        return "workflow_retry_failed"
    if "workflow" in lowered and any(phrase in lowered for phrase in ("start", "run", "execute")):
        return "workflow_run_start"
    if any(word in lowered for word in ("crm", "sheets", "salesforce", "hubspot", "integration")) and any(phrase in lowered for phrase in ("preview", "sync", "diff")):
        return "crm_preview_sync"
    if "deliverability" in lowered and any(phrase in lowered for phrase in ("summary", "health", "status", "read")):
        return "deliverability_read_summary"
    if "deliverability" in lowered and any(phrase in lowered for phrase in ("queue block", "blocked queue", "why blocked")):
        return "deliverability_explain_queue_block"
    if "compose" in lowered and "email" in lowered:
        return "email_generate_draft"
    if "draft" in lowered and "follow" in lowered:
        return "email_generate_draft"
    if _is_followup_draft_request(lowered):
        return "email_generate_draft"
    if any(phrase in lowered for phrase in ("current status", "currently suppressed", "is suppressed", "suppression")):
        return "contact_resolve"
    if "autonomous" in lowered and "repl" in lowered:
        return "queue_status"
    if any(phrase in lowered for phrase in ("most recent message", "latest message", "what did", "read reply from", "reply from")) and any(
        word in lowered for word in ("say", "said", "message", "reply")
    ):
        return "email_read_thread"
    if any(phrase in lowered for phrase in ("who replied", "who has replied", "has replied recently", "replies today", "replied today", "mail came", "mails came", "inbox today")):
        return "email_read_inbox"
    if "queue" in lowered:
        return "queue_status"
    if "follow" in lowered:
        return "followup_status"
    if lowered.startswith(("find ", "resolve ", "search contact")) or "contact" in lowered and "thread" not in lowered:
        return "contact_resolve"
    if any(phrase in lowered for phrase in ("generate", "draft", "dradt", "write a reply", "reply for", "reply them back", "reply him back", "reply her back")):
        return "email_generate_draft"
    if any(phrase in lowered for phrase in ("send it", "send draft", "send email", "confirm send")):
        return "email_send_draft"
    if "thread" in lowered or "conversation" in lowered or lowered.startswith("show "):
        return "email_read_thread"
    if "search" in lowered:
        return "email_search_thread"
    return "unsupported"


def _is_followup_draft_request(lowered: str) -> bool:
    if "follow" not in lowered:
        return False
    question_starts = ("who ", "whom ", "which ", "what ", "how many ", "show ", "list ")
    if lowered.startswith(question_starts) and "asking" not in lowered and "ask " not in lowered:
        return False
    return any(
        phrase in lowered
        for phrase in (
            "send another",
            "send a follow",
            "send follow",
            "another follow",
            "write a follow",
            "compose a follow",
            "draft a follow",
            "asking",
            "ask ",
            "meet",
            "discuss",
        )
    )
