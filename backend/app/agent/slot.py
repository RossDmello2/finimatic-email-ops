from __future__ import annotations

import re

from app.agent.catalog import required_slots_for_capability
from app.agent.schemas import IntentDecision, SlotAgentOutput

EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
HEX_ID_RE = re.compile(r"\b[a-f0-9]{32}\b", re.IGNORECASE)


class SlotAgent:
    def extract(self, message: str, session_summary: str | None, intent: IntentDecision) -> SlotAgentOutput:
        text = message.strip()
        lowered = text.lower()
        slots: dict[str, str] = {}
        contact_id = _extract_contact_id(text)
        if contact_id:
            slots["contact_id"] = contact_id
        name_or_email = _extract_name_or_email(text)
        if name_or_email:
            slots["name_or_email"] = name_or_email
        if intent.capability == "email_generate_draft":
            slots["reply_goal"] = text
            tone = _extract_tone(lowered)
            if tone:
                slots["tone"] = tone
        if intent.capability == "email_read_inbox" and "today" in lowered:
            slots["date_range"] = "today"
        if intent.capability == "email_search_thread" and text:
            slots["query"] = name_or_email or text
        if intent.capability == "contact_resolve" and not slots.get("name_or_email"):
            slots["name_or_email"] = text

        missing: list[str] = []
        for slot in required_slots_for_capability(intent.capability):
            if slot == "contact_id" and slots.get("name_or_email"):
                continue
            if slot not in slots:
                missing.append(slot)
        if intent.capability == "email_send_draft":
            missing = ["_confirmed_action_id"]
        return SlotAgentOutput(
            slots_filled=slots,
            slots_missing=missing,
            ready_to_execute=not missing,
            clarification_question=_clarification(intent.capability, missing),
            confidence=0.75 if not missing else 0.45,
        )


def _extract_contact_id(text: str) -> str | None:
    match = HEX_ID_RE.search(text)
    return match.group(0) if match else None


def _extract_name_or_email(text: str) -> str | None:
    lowered = text.lower()
    email = EMAIL_RE.search(text)
    if email:
        return email.group(0)
    aliases = {
        "data science educator": "Data Science Educator",
        "python educator": "Data Science Educator",
        "udemy educator": "Data Science Educator",
        "career coaching contact": "Career Coach Creator",
        "career coach contact": "Career Coach Creator",
        "career coach creator": "Career Coach Creator",
        "coaching contact": "Career Coach Creator",
    }
    for phrase, value in aliases.items():
        if phrase in lowered:
            return value
    patterns = [
        r"\bfor\s+(.+?)(?:\s+(?:with|in|using|today|now|based)\b|$)",
        r"\bfrom\s+(.+?)(?:\s+(?:with|in|today)\b|$)",
        r"\bstatus\s+of\s+(?:the\s+)?(.+?)(?:\s+(?:contact|lead|person)\b|$)",
        r"\bsuppressed\??\s*$",
        r"\bshow\s+(.+?)(?:'s)?\s+(?:thread|conversation)\b",
        r"\bread\s+(.+?)(?:'s)?\s+(?:thread|conversation)\b",
        r"\bread\s+(?:the\s+)?reply\s+from\s+(.+?)(?:\s+(?:with|in|today|now)\b|$)",
        r"\bwhat\s+did\s+(.+?)\s+(?:reply|replied|replyed|say|said)\b",
        r"\bwhat\s+has\s+(.+?)\s+(?:replied|said)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            if not match.groups():
                continue
            value = match.group(1).strip(" .,'\"")
            value = re.sub(r"^the\s+", "", value, flags=re.IGNORECASE)
            if value and value.lower() not in {"it", "the", "a", "reply", "draft", "he", "she", "him", "her", "they", "them", "that", "this", "same"}:
                return value
    return None


def _extract_tone(lowered: str) -> str | None:
    for tone in ("professional", "friendly", "casual", "direct", "polite"):
        if tone in lowered:
            return tone
    return None


def _clarification(capability: str, missing: list[str]) -> str | None:
    if not missing:
        return None
    if capability in {"email_read_thread", "email_generate_draft"} and "contact_id" in missing:
        return "Which contact should I use?"
    if capability == "contact_resolve":
        return "Which contact name or email should I look up?"
    if capability == "email_send_draft":
        return "Sending requires the Confirm button for a pending draft. I did not send anything."
    return f"Please provide: {', '.join(missing)}"
