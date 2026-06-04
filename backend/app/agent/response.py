from __future__ import annotations

from app.agent.schemas import EvidenceEnvelope, IntentDecision
from app.agent.tools import sanitize_text
from app.agent.verifier import VerificationDecision


RESPONSE_QUALITY_ADDENDUM = """
QUALITY RULES — enforce on every response:
- Never output a 32-character hex ID.
- Never output an ISO timestamp — say "2 hours ago", "yesterday".
- Never output a technical status code — translate to plain English.
  suppressed → opted out
  blocked_by_policy → blocked by a sending rule
  follow_up_stopped → no longer receiving follow-ups
  conversation_active → replied and needs your response
- Never output field names: classified_as, contact_id, params_hash,
  draft_id, idempotency_key, sequence_num, action_class, privacy_class.
- For lists: maximum 5 named items, then "...and N more".
- If the answer is a number, state it first.
- Never say "I cannot perform that" for a read or information question.
- Start with the actual answer, not a preamble.
"""


def _compact_snippet(value: object, limit: int = 140) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


class ResponseAgent:
    def compose(self, message: str, intent: IntentDecision, verification: VerificationDecision, evidence: list[EvidenceEnvelope]) -> str:
        lowered_message = message.lower()
        if not evidence:
            return "I could not find enough approved evidence to answer that."
        latest = evidence[-1]
        if latest.status == "denied":
            return sanitize_text(str(latest.data.get("message") or "That action is not allowed."))
        if intent.capability == "email_read_inbox":
            items = latest.data.get("items") or []
            if not items:
                return "No replies were found in the last 24 hours."
            if "how many" in lowered_message or "count" in lowered_message:
                contact_count = int(latest.data.get("distinct_contact_count") or 0)
                reply_count = int(latest.data.get("reply_count") or 0)
                window = "today" if latest.data.get("window") == "today" else "in the last 24 hours"
                contact_label = "contact" if contact_count == 1 else "contacts"
                reply_label = "reply" if reply_count == 1 else "replies"
                return sanitize_text(f"{contact_count} {contact_label} replied {window}; {reply_count} {reply_label} matched the same DB window.")
            lines = ["Replies in the last 24 hours:"]
            shown_items = items[:5]
            for item in shown_items:
                name = item.get("contact_name") or item.get("contact_email")
                classification = item.get("reply_classified_as") or "reply"
                if classification == "unknown":
                    classification = "reply"
                lines.append(f"- {name} ({item.get('contact_email')}): {classification} - {_compact_snippet(item.get('raw_summary'))}")
            remaining = int(latest.data.get("reply_count") or len(items)) - len(shown_items)
            if remaining > 0:
                lines.append(f"...and {remaining} more replies in this window.")
            return sanitize_text("\n".join(lines))
        if intent.capability == "email_read_thread":
            contact_label = latest.data.get("contact_name") or latest.data.get("contact_email")
            if any(phrase in lowered_message for phrase in ("most recent message", "latest message", "what did")):
                inbound = [item for item in latest.data.get("messages", []) if item.get("direction") == "inbound"]
                if inbound:
                    item = inbound[-1]
                    return sanitize_text(
                        f"Latest reply from {contact_label} ({latest.data.get('contact_email')}): {item.get('body')}"
                    )
                latest_reply = latest.data.get("latest_reply") or {}
                if latest_reply.get("raw_summary"):
                    return sanitize_text(
                        f"Latest reply from {contact_label} ({latest.data.get('contact_email')}): {latest_reply.get('raw_summary')}"
                    )
            if not latest.data.get("messages"):
                latest_reply = latest.data.get("latest_reply") or {}
                if latest_reply.get("raw_summary"):
                    return sanitize_text(
                        f"Latest reply from {contact_label} ({latest.data.get('contact_email')}): {latest_reply.get('raw_summary')}"
                    )
                return "I found the contact, but there are no conversation messages yet."
            lines = [f"Thread for {contact_label} ({latest.data.get('contact_email')}):"]
            for item in latest.data.get("messages", [])[-5:]:
                speaker = "You wrote" if item.get("direction") == "outbound" else "They wrote"
                lines.append(f"- {speaker}: {item.get('body')}")
            return sanitize_text("\n".join(lines))
        if intent.capability == "contact_resolve":
            items = latest.data.get("items") or []
            if not items:
                return "I could not find a matching contact."
            if "suppress" in lowered_message:
                item = items[0]
                answer = "YES" if item.get("suppressed") else "NO"
                reason = item.get("suppression_reason") or "no suppression record"
                return sanitize_text(f"{answer}. {item.get('email')} suppression status: {reason}.")
            if "status" in lowered_message:
                item = items[0]
                return sanitize_text(f"{item.get('email')} current status is {item.get('status')}.")
            return sanitize_text("Matches:\n" + "\n".join(f"- {item.get('email')} ({item.get('creator_name') or item.get('business_name') or item.get('status')})" for item in items))
        if intent.capability == "queue_status":
            if "autonomous" in lowered_message and "repl" in lowered_message:
                return sanitize_text(
                    f"{latest.data.get('autonomous_replies_last_2_hours', 0)} autonomous replies were sent in the last 2 hours."
                )
            return sanitize_text(
                "Queue status: "
                f"{latest.data.get('pending_count', 0)} pending, "
                f"{latest.data.get('blocked_count', 0)} blocked, "
                f"{latest.data.get('sent_today', 0)} sent in the last 24 hours."
            )
        if intent.capability == "followup_status":
            items = latest.data.get("items") or []
            if not items:
                return "No follow-up rows matched."
            lines = ["Follow-up status:"]
            for item in items[:10]:
                name = item.get("contact_name") or item.get("contact_email") or "contact"
                email = f" ({item.get('contact_email')})" if item.get("contact_email") else ""
                sequence = item.get("sequence_num")
                status = item.get("status") or "pending"
                lines.append(f"- {name}{email}: follow-up #{sequence} {status}")
            return sanitize_text("\n".join(lines))
        if intent.capability == "email_generate_draft":
            if latest.status == "error":
                return "I could not generate a draft with the configured model provider. Retry with another provider or create a manual draft."
            return "I've drafted a reply. Review it below and use Confirm only if you want it sent."
        if intent.capability == "email_send_draft":
            if latest.status == "success":
                return f"Sent. Provider message id: {latest.data.get('provider_msg_id')}"
            return sanitize_text(str(latest.data.get("message") or "I did not send the email."))
        return sanitize_text(str(latest.data.get("message") or "Done."))
