# Email Agentic Assistant Handoff

Last grounded against this repo: 2026-05-23.

This file is a handoff for adding a governed agentic chatbot to an existing
email delivery project. It translates the current `agentic_chatbot` control
plane into an email assistant that can inspect mailbox state, understand the
user's request, generate a draft, ask for confirmation, and send only after the
same confirmed pending action is validated by backend code.

Use this as an implementation contract for another coding agent. The target
email project is not known, so the snippets use Python/FastAPI-style backend
code and vanilla browser JavaScript as a reference implementation. If the target
project uses a different stack, preserve the contracts and safety gates even if
the syntax changes.

## Purpose And Non-Negotiables

The assistant is not a simple autocomplete box. It is a governed agentic control
plane around email actions.

Required behavior:

- A small chatbot launcher appears at the right bottom corner of the email app.
- The user can type or speak requests such as "show replies from Rahul",
  "which mails came today", "generate a response to his reply", or "send it".
- The assistant can read approved mailbox/thread data through backend tools.
- The assistant can draft subject/body text using Groq, Gemini, or another
  configured model provider.
- The assistant must display the draft and ask for confirmation before sending.
- The assistant must send only the exact confirmed draft to the exact confirmed
  recipient list.
- The assistant must reject stale, duplicate, wrong-user, wrong-session, or
  mismatched confirmation attempts.

Hard rules:

- The browser must never see email passwords, API keys, app passwords, OAuth
  refresh tokens, Groq keys, Gemini keys, SMTP secrets, or raw provider tokens.
- The model must never receive raw passwords, auth tokens, mailbox credentials,
  or unrelated full mailbox history.
- The model may propose intent, slots, plans, draft text, and response text.
- Backend code owns validation, credential use, mailbox API calls, send calls,
  state commit, rollback, source labels, audit logs, and confirmation.
- Sending email is a side effect. It must use a pending-confirmation harness.
- A low-parameter model must not be trusted with hidden authority. Every model
  output must validate against strict schemas and positive capability rules.

In the current HPCL chatbot, the same safety shape exists around flight booking:
agents propose, Python validates, pending action is stored, confirmation is
required, and execution is bound to the same employee/session/action id. For
email, treat `email_send_draft` exactly like flight booking: a governed side
effect, never a direct model command.

## Architecture Map

Current repo source-backed roles:

| Current component | Email assistant equivalent | Responsibility |
|---|---|---|
| `GoalFrameAgent` | Goal frame classifier | Understand the real user goal before intent routing: read mail, search thread, draft reply, update draft, send, cancel, or unsupported. |
| `IntentAgent` | Email intent router | Select one catalog capability and dialogue act. It does not execute email actions. |
| `SlotAgent` | Email slot extractor | Extract recipient, thread id, message id, subject, tone, draft id, date range, group key, and correction events. |
| `OrchestratorAgent` | Email task planner | Break complex requests into bounded subtasks such as search thread, read last reply, draft response, verify draft, ask confirmation. |
| `ReasoningAgent` | Evidence reasoner | Reason only over supplied email/tool evidence, not hidden mailbox state. |
| `VerifierAgent` | Sufficiency checker | Decide whether evidence satisfies the original request and whether a draft/send is safe to present. |
| `ResponseAgent` | Response planner | Compose user-facing assistant text while preserving backend-owned source labels. |
| `RepairRouter` | Recovery policy | Retry, replan, clarify, refuse, or fail closed after schema/tool/verifier errors. |
| `MemoryLayer` | Session memory wrapper | Store current goal, slots, pending draft/send action, safe summaries, and cleanup state. |
| `CapabilityCatalog` | Positive capability catalog | Deny by default unless action/object matches an approved capability. |
| `AgenticToolExecutor` | Email tool execution boundary | Validate capability, session, privacy, and confirmation before calling mailbox/email tools. |

Layer model:

```text
Browser widget
  -> /email-agent/chat
  -> auth/session guard
  -> GoalFrameAgent
  -> Python capability validator
  -> IntentAgent
  -> SlotAgent
  -> OrchestratorAgent when a plan is needed
  -> AgenticToolExecutor
  -> Email tools: inbox, thread, draft, send, contact/group lookup
  -> ReasoningAgent over redacted tool evidence
  -> VerifierAgent over actual tool result envelope
  -> ResponseAgent or backend response renderer
  -> pending confirmation for side effects
  -> send only after backend validates confirmation
```

The important design rule is simple:

```text
Model output is a proposal.
Backend validation decides whether the proposal is allowed.
Backend tools perform every real read or write.
```

## End-To-End Flow

### Flow 1: User asks what mail came in

```text
User: "which mails came today?"
GoalFrame: informational / mailbox read / private
Intent: email_read_inbox
Slots: date_range=today, folder=inbox
ToolPlan: email_read_inbox, side_effect=false
Tool: reads approved mailbox metadata/snippets
Verifier: evidence sufficient if messages list is present
Response: summarize sender, subject, date, and short snippet
No confirmation required because no send/write occurred
```

### Flow 2: User asks to generate a reply

```text
User: "someone replied, generate a response for him"
GoalFrame: draft generation / continuation or new goal
Intent: email_generate_draft
Slots: thread_id or selected_message_id is required
Tool 1: email_read_thread
Tool 2: template_generate or model draft generation
Verifier: draft is based on selected thread, has recipient, subject, body
Response: show draft and ask whether to send
State: store draft as pending_action or draft_state, not sent
```

### Flow 3: User confirms sending

```text
User: "send it" or "confirm"
Backend: checks pending action exists
Backend: checks same user, same session, same draft hash, not expired, not consumed
Backend: records confirmation audit
AgenticToolExecutor: executes email_send_draft only with _confirmed_action_id
Email provider: sends exact confirmed draft
Backend: records executed/failed audit
Backend: clears pending action after completion
```

### Flow 4: User changes the draft before sending

```text
User: "make it more polite and mention tomorrow"
GoalFrame: side_effect_update or draft_update, continuation
Intent: email_update_draft
SlotAgent: emits update events for tone/body_instruction
Backend: updates pending draft, recomputes params_hash, asks confirmation again
Old confirmation id must not be reusable
```

### Flow 5: User cancels

```text
User: "cancel" or "leave it"
Backend: clears current goal, pending draft, pending send action, slot state
No email is sent
Response: "Cancelled. I did not send anything."
```

## Capability Catalog

Use a positive catalog. The assistant can only do actions listed here. Everything
else is unsupported or requires clarification.

| Capability | Class | Side effect | Confirmation | Required slots | Source label | Failure behavior |
|---|---|---:|---:|---|---|---|
| `email_read_inbox` | private_read | no | no | `folder`, optional `date_range`, optional `limit` | Mailbox | Return empty list or tool error; never fabricate messages. |
| `email_search_thread` | private_read | no | no | `query` or `sender` or `recipient` or `subject` | Mailbox | Ask clarification if search target is missing. |
| `email_read_thread` | private_read | no | no | `thread_id` or `message_id` | Mailbox | Ask user to select a thread if multiple matches. |
| `email_generate_draft` | draft_write_local | local draft only | ask before send | `thread_id` or `message_id`, `reply_goal` | Draft Generator | Show draft only; do not send. |
| `email_update_draft` | draft_write_local | local draft only | ask before send | `draft_id` or pending draft, update instruction | Draft Generator | Update pending draft and invalidate old confirmation. |
| `email_send_draft` | transactional | yes | yes | `draft_id`, `to`, `subject`, `body`, `_confirmed_action_id` | Email Provider | Deny unless pending confirmation validates. |
| `contact_resolve` | private_read | no | no | `name_or_email` | Contacts | Ask clarification for multiple matches. |
| `group_resolve` | private_read | no | no | `group_key` | Contacts | Expand only server-side; do not expose hidden group secrets. |
| `template_generate` | model_generation | no | no | `goal`, optional `tone`, optional `context` | Model | Must cite selected mailbox evidence or state missing context. |

Capability policy:

```json
{
  "capabilities": {
    "email_send_draft": {
      "allowed_action_classes": ["side_effect_create"],
      "required_slots": ["draft_id", "to", "subject", "body", "_confirmed_action_id"],
      "side_effect": true,
      "confirmation_required": true,
      "source_label": "Email Provider"
    },
    "email_read_thread": {
      "allowed_action_classes": ["private_mail_read"],
      "required_slots": ["thread_id"],
      "side_effect": false,
      "confirmation_required": false,
      "source_label": "Mailbox"
    }
  }
}
```

Do not implement unsupported capabilities by "just letting the model try".
Unsupported examples include deleting mail, forwarding confidential mail, reading
all historical mailbox content without a bounded query, changing credentials,
changing SMTP settings, or sending to an unresolved group without preview.

## State And Payload Contracts

### Browser to backend request

```json
{
  "session_id": "browser-session-id",
  "message": "generate a reply to Rahul's latest email",
  "selected_message_id": "optional-current-ui-message",
  "selected_thread_id": "optional-current-ui-thread",
  "voice": false
}
```

### Backend to browser response

```json
{
  "response": "I drafted a reply based on Rahul's latest message. Review it below.",
  "source": "Mailbox + Draft Generator",
  "intent": "email_generate_draft",
  "is_clarification": false,
  "draft": {
    "draft_id": "draft_abc123",
    "to": ["rahul@example.com"],
    "cc": [],
    "bcc": [],
    "subject": "Re: Project update",
    "body": "Hi Rahul, ...",
    "based_on_message_ids": ["msg_123"]
  },
  "pending_action": {
    "action_id": "act_123",
    "capability": "email_send_draft",
    "expires_at": "2026-05-23T13:20:00Z",
    "field_names": ["to", "subject", "body"],
    "confirmation_prompt": "Send this draft to rahul@example.com?"
  },
  "debug": {
    "agentic_trace": []
  }
}
```

### Session state

Store this on the backend, not in localStorage.

```python
EmailAgentSession = {
    "session_id": "...",
    "user_id": "...",
    "authenticated": True,
    "mailbox_account_id": "...",
    "current_goal": "",
    "current_intent": "",
    "selected_thread_id": None,
    "selected_message_id": None,
    "slots_filled": {},
    "slots_missing": [],
    "pending_draft": None,
    "pending_action": None,
    "agentic_trace": [],
    "safe_history_summary": "",
}
```

### Strict model and tool schemas

These Pydantic schemas are intentionally close to the current repo's
`app/agentic/schemas.py`, but renamed for email.

```python
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DialogueAct(str, Enum):
    NEW_INTENT = "new_intent"
    CONTINUATION = "continuation"
    CORRECTION = "correction"
    CANCEL = "cancel"


class ActionClass(str, Enum):
    INFORMATIONAL = "informational"
    PRIVATE_MAIL_READ = "private_mail_read"
    DRAFT_CREATE = "draft_create"
    DRAFT_UPDATE = "draft_update"
    SIDE_EFFECT_SEND = "side_effect_send"
    CLARIFICATION = "clarification"
    UNSUPPORTED = "unsupported"


class PrivacyClass(str, Enum):
    PUBLIC = "public"
    PRIVATE_MAIL = "private_mail"
    SIDE_EFFECT = "side_effect"
    UNSUPPORTED = "unsupported"


class CapabilityClass(str, Enum):
    MAILBOX = "mailbox"
    CONTACTS = "contacts"
    DRAFT = "draft"
    MODEL = "model"
    TRANSACTIONAL = "transactional"
    STATIC = "static"
    UNSUPPORTED = "unsupported"


ALLOWED_CAPABILITIES = {
    "email_read_inbox",
    "email_search_thread",
    "email_read_thread",
    "email_generate_draft",
    "email_update_draft",
    "email_send_draft",
    "contact_resolve",
    "group_resolve",
    "template_generate",
    "static_help",
    "unsupported",
}


def validate_capability(value: str) -> str:
    if value not in ALLOWED_CAPABILITIES:
        raise ValueError(f"capability is not allowed: {value}")
    return value


class GoalFrameDecision(StrictModel):
    user_goal: str = Field(min_length=1)
    action_class: ActionClass
    raw_action_text: str = ""
    object_phrase: str = ""
    context_relation: str = Field(pattern="^(new_goal|continuation|correction|cancellation|domain_switch|unrelated)$")
    privacy_class: PrivacyClass
    proposed_capability: str = ""
    required_slots: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""

    @field_validator("proposed_capability")
    @classmethod
    def capability_known_or_empty(cls, value: str) -> str:
        return validate_capability(value) if value else value


class IntentDecision(StrictModel):
    intent: str = Field(min_length=1)
    dialogue_act: DialogueAct
    capability_class: CapabilityClass
    capability: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    requires_confirmation: bool = False
    rationale: str | None = None

    @field_validator("capability")
    @classmethod
    def capability_allowed(cls, value: str) -> str:
        return validate_capability(value)


class SlotOperation(str, Enum):
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    NO_CHANGE = "NO_CHANGE"


class SlotEvent(StrictModel):
    op: SlotOperation
    field: str = Field(min_length=1)
    old_value: Any = None
    new_value: Any = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class SlotAgentOutput(StrictModel):
    slots_filled: dict[str, Any] = Field(default_factory=dict)
    slot_events: list[SlotEvent] = Field(default_factory=list)
    slots_missing: list[str] = Field(default_factory=list)
    ready_to_execute: bool = False
    clarification_question: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ToolPlan(StrictModel):
    capability: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    side_effect: bool
    source_label: str = Field(min_length=1)
    reason: str | None = None

    @field_validator("capability")
    @classmethod
    def capability_allowed(cls, value: str) -> str:
        return validate_capability(value)


class ToolEnvelopeStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    EMPTY = "empty"
    PENDING_CONFIRMATION = "pending_confirmation"
    DENIED = "denied"


class ToolResultEnvelope(StrictModel):
    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    intent_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    tool_class: str = Field(min_length=1)
    source_label: str = Field(min_length=1)
    status: ToolEnvelopeStatus
    data: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    latency_ms: int = Field(default=0, ge=0)
    retryable: bool = False
    error_code: str | None = None
    error_message: str | None = None
    params_redacted: dict[str, Any] = Field(default_factory=dict)


class PendingEmailAction(StrictModel):
    action_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    session_id_hash: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    draft_id: str = Field(min_length=1)
    params_hash: str = Field(min_length=1)
    redacted_diff: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    source_label: str = "Email Provider"
    consumed: bool = False
    confirmation_prompt: str = Field(min_length=1)

    @field_validator("capability")
    @classmethod
    def capability_allowed(cls, value: str) -> str:
        return validate_capability(value)

    @model_validator(mode="after")
    def expiry_after_created(self) -> "PendingEmailAction":
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        return self


class VerificationDecision(StrictModel):
    sufficient: bool
    confidence_score: float = Field(ge=0.0, le=1.0)
    what_is_missing: list[str] = Field(default_factory=list)
    retryable: bool = False
    retry_action: dict[str, Any] | None = None
    reflection_note: str = ""


class ResponsePlan(StrictModel):
    response_text: str
    source_label: str
    feedback_enabled: bool = True
    citations: list[dict[str, Any]] = Field(default_factory=list)
    language: str = "en"
```

## Implementation Snippets

### 1. Floating chatbot widget

Add this to the email app page after the main mail UI. It is intentionally
simple. Replace `/email-agent/chat` with the real backend route.

```html
<button id="email-agent-launcher" aria-label="Open email assistant">Chat</button>

<section id="email-agent-panel" aria-label="Email assistant" hidden>
  <header>
    <strong>Email Assistant</strong>
    <button id="email-agent-close" aria-label="Close assistant">x</button>
  </header>
  <div id="email-agent-messages" role="log" aria-live="polite"></div>
  <form id="email-agent-form">
    <button id="email-agent-mic" type="button" aria-label="Voice input">Mic</button>
    <input id="email-agent-input" autocomplete="off" placeholder="Ask about mail or draft a reply" />
    <button type="submit">Send</button>
  </form>
</section>

<style>
  #email-agent-launcher {
    position: fixed;
    right: 20px;
    bottom: 20px;
    z-index: 1000;
    height: 48px;
    min-width: 72px;
  }

  #email-agent-panel {
    position: fixed;
    right: 20px;
    bottom: 84px;
    z-index: 1000;
    width: min(420px, calc(100vw - 32px));
    height: min(640px, calc(100vh - 120px));
    background: #fff;
    border: 1px solid #d0d7de;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.18);
    display: flex;
    flex-direction: column;
  }

  #email-agent-panel[hidden] {
    display: none;
  }

  #email-agent-panel header,
  #email-agent-form {
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 10px;
    border-bottom: 1px solid #d0d7de;
  }

  #email-agent-form {
    border-top: 1px solid #d0d7de;
    border-bottom: 0;
  }

  #email-agent-messages {
    flex: 1;
    overflow: auto;
    padding: 10px;
  }

  #email-agent-input {
    flex: 1;
    min-width: 0;
  }

  .email-agent-message {
    margin: 0 0 10px;
    white-space: pre-wrap;
  }

  .email-agent-draft {
    border: 1px solid #d0d7de;
    padding: 8px;
    margin: 8px 0;
  }
</style>

<script>
(() => {
  const launcher = document.querySelector("#email-agent-launcher");
  const panel = document.querySelector("#email-agent-panel");
  const close = document.querySelector("#email-agent-close");
  const form = document.querySelector("#email-agent-form");
  const input = document.querySelector("#email-agent-input");
  const messages = document.querySelector("#email-agent-messages");

  function currentMailSelection() {
    return {
      selected_message_id: window.currentMessageId || null,
      selected_thread_id: window.currentThreadId || null
    };
  }

  function appendMessage(role, text, draft) {
    const item = document.createElement("div");
    item.className = "email-agent-message";
    item.textContent = `${role}: ${text}`;
    messages.appendChild(item);

    if (draft) {
      const box = document.createElement("div");
      box.className = "email-agent-draft";
      box.textContent = [
        `To: ${draft.to.join(", ")}`,
        draft.cc && draft.cc.length ? `Cc: ${draft.cc.join(", ")}` : "",
        `Subject: ${draft.subject}`,
        "",
        draft.body
      ].filter(Boolean).join("\n");
      messages.appendChild(box);
    }

    messages.scrollTop = messages.scrollHeight;
  }

  async function sendToAgent(message) {
    appendMessage("You", message);
    const response = await fetch("/email-agent/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        message,
        session_id: window.emailAgentSessionId || "default",
        ...currentMailSelection()
      })
    });
    const payload = await response.json();
    appendMessage("Assistant", payload.response || "No response.", payload.draft || null);
  }

  launcher.addEventListener("click", () => {
    panel.hidden = false;
    input.focus();
  });

  close.addEventListener("click", () => {
    panel.hidden = true;
  });

  form.addEventListener("submit", event => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    sendToAgent(text).catch(error => {
      appendMessage("Assistant", `The assistant failed: ${error.message}`);
    });
  });

  document.querySelector("#email-agent-mic").addEventListener("click", () => {
    if (!("webkitSpeechRecognition" in window)) {
      appendMessage("Assistant", "Voice input is not available in this browser.");
      return;
    }
    const recognition = new webkitSpeechRecognition();
    recognition.lang = "en-US";
    recognition.onresult = event => {
      input.value = event.results[0][0].transcript;
      input.focus();
    };
    recognition.start();
  });
})();
</script>
```

### 2. Backend chat route

This route shows the correct control shape. In a real project, replace the
in-memory `SESSION_STORE` with the app's session store.

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter()
SESSION_STORE: dict[str, dict] = {}


class EmailAgentChatRequest(BaseModel):
    session_id: str
    message: str
    selected_message_id: str | None = None
    selected_thread_id: str | None = None
    voice: bool = False


class EmailAgentChatResponse(BaseModel):
    response: str
    source: str | None = None
    intent: str | None = None
    is_clarification: bool = False
    draft: dict | None = None
    pending_action: dict | None = None
    debug: dict = {}


async def current_user():
    # Replace with real auth. Must return a stable user id and mailbox account id.
    return {"user_id": "user_123", "mailbox_account_id": "mailbox_123"}


@router.post("/email-agent/chat", response_model=EmailAgentChatResponse)
async def email_agent_chat(request: EmailAgentChatRequest, user=Depends(current_user)):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    state = SESSION_STORE.setdefault(request.session_id, {
        "session_id": request.session_id,
        "user_id": user["user_id"],
        "mailbox_account_id": user["mailbox_account_id"],
        "authenticated": True,
        "slots_filled": {},
        "slots_missing": [],
        "agentic_trace": [],
    })

    if state["user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="session user mismatch")

    state["selected_message_id"] = request.selected_message_id
    state["selected_thread_id"] = request.selected_thread_id

    result = await handle_email_agent_turn(state, request.message)
    return EmailAgentChatResponse(**result)
```

### 3. Capability registry and tool boundary

The registry is the allowlist. The model cannot invent tool names outside this
registry.

```python
from dataclasses import dataclass
from typing import Protocol, Any


@dataclass
class EmailToolResult:
    data: dict[str, Any]
    complete: bool
    missing_fields: list[str]
    confidence: float
    source_label: str


class EmailTool(Protocol):
    capability: str
    source_label: str

    async def execute(self, params: dict[str, Any], state: dict[str, Any]) -> EmailToolResult:
        ...


class EmailToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, EmailTool] = {}

    def register(self, tool: EmailTool) -> None:
        self._tools[tool.capability] = tool

    def get(self, capability: str) -> EmailTool | None:
        return self._tools.get(capability)


REGISTRY = EmailToolRegistry()


def register_email_tools(provider, contacts):
    REGISTRY.register(ReadInboxTool(provider))
    REGISTRY.register(SearchThreadTool(provider))
    REGISTRY.register(ReadThreadTool(provider))
    REGISTRY.register(GenerateDraftTool(provider, contacts))
    REGISTRY.register(UpdateDraftTool())
    REGISTRY.register(SendDraftTool(provider))
    REGISTRY.register(ContactResolveTool(contacts))
    REGISTRY.register(GroupResolveTool(contacts))
```

### 4. Model boundary for Groq and Gemini

Only the backend calls Groq or Gemini. The browser never receives these keys.

```python
import json
import os
from typing import Any

import httpx
from pydantic import BaseModel


class ModelBoundaryError(RuntimeError):
    pass


class EmailModelClient:
    def __init__(self) -> None:
        self.provider_order = os.getenv("EMAIL_AGENT_MODEL_PROVIDERS", "groq,gemini").split(",")
        self.groq_key = os.getenv("GROQ_API_KEY", "")
        self.gemini_key = os.getenv("GEMINI_API_KEY", "")
        self.groq_model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

    async def call_schema(
        self,
        *,
        role: str,
        messages: list[dict[str, str]],
        output_model: type[BaseModel],
        fallback_data: dict[str, Any],
    ) -> BaseModel:
        safe_messages = redact_model_messages(messages)
        schema = output_model.model_json_schema()
        last_error: Exception | None = None

        for provider in self.provider_order:
            provider = provider.strip().lower()
            try:
                if provider == "groq" and self.groq_key:
                    raw = await self._call_groq(role, safe_messages, schema)
                    return output_model.model_validate(raw)
                if provider == "gemini" and self.gemini_key:
                    raw = await self._call_gemini(role, safe_messages, schema)
                    return output_model.model_validate(raw)
            except Exception as exc:
                last_error = exc
                continue

        if fallback_data:
            return output_model.model_validate(fallback_data)
        raise ModelBoundaryError(f"all model providers failed: {last_error}")

    async def _call_groq(self, role: str, messages: list[dict[str, str]], schema: dict) -> dict:
        payload = {
            "model": self.groq_model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Return only valid JSON for {role}. "
                        f"The object must match this schema: {json.dumps(schema)}"
                    ),
                },
                *messages,
            ],
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.groq_key}"},
                json=payload,
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)

    async def _call_gemini(self, role: str, messages: list[dict[str, str]], schema: dict) -> dict:
        prompt = "\n".join(m["content"] for m in messages)
        payload = {
            "contents": [{
                "parts": [{
                    "text": (
                        f"Return only valid JSON for {role}. "
                        f"Schema: {json.dumps(schema)}\n\n{prompt}"
                    )
                }]
            }],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json"
            }
        }
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.gemini_model}:generateContent?key={self.gemini_key}"
        )
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)


SENSITIVE_WORDS = {
    "password", "smtp_password", "app_password", "oauth", "refresh_token",
    "access_token", "api_key", "groq", "gemini", "cookie", "authorization"
}


def redact_model_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    clean = []
    for message in messages:
        text = str(message.get("content") or "")
        lowered = text.lower()
        if any(word in lowered for word in SENSITIVE_WORDS):
            text = "[REDACTED_SENSITIVE_CONTEXT]"
        clean.append({"role": message.get("role", "user"), "content": text})
    return clean
```

### 5. Email provider adapter

Put credential handling behind a provider interface. The agent never sees SMTP,
IMAP, Gmail, or OAuth secrets.

```python
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any
import smtplib


@dataclass
class EmailDraft:
    draft_id: str
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body: str
    based_on_message_ids: list[str]


class EmailProvider:
    async def read_inbox(self, account_id: str, *, folder: str, date_range: str | None, limit: int) -> list[dict]:
        raise NotImplementedError

    async def search_thread(self, account_id: str, *, query: str, limit: int) -> list[dict]:
        raise NotImplementedError

    async def read_thread(self, account_id: str, *, thread_id: str | None, message_id: str | None) -> dict:
        raise NotImplementedError

    async def send_draft(self, account_id: str, draft: EmailDraft) -> dict:
        raise NotImplementedError


class SMTPEmailProvider(EmailProvider):
    def __init__(self, credential_store) -> None:
        self.credential_store = credential_store

    async def send_draft(self, account_id: str, draft: EmailDraft) -> dict:
        creds = self.credential_store.get_smtp_credentials(account_id)
        message = EmailMessage()
        message["From"] = creds.sender
        message["To"] = ", ".join(draft.to)
        if draft.cc:
            message["Cc"] = ", ".join(draft.cc)
        message["Subject"] = draft.subject
        message.set_content(draft.body)

        with smtplib.SMTP_SSL(creds.host, creds.port) as smtp:
            smtp.login(creds.username, creds.password)
            smtp.send_message(message)

        return {
            "provider_message_id": "smtp_sent",
            "to": draft.to,
            "subject": draft.subject,
            "status": "sent"
        }


class GmailApiProvider(EmailProvider):
    def __init__(self, gmail_client_factory) -> None:
        self.gmail_client_factory = gmail_client_factory

    async def send_draft(self, account_id: str, draft: EmailDraft) -> dict:
        client = self.gmail_client_factory.for_account(account_id)
        # Convert draft to raw RFC 2822 and call users.messages.send.
        # Keep OAuth refresh/access tokens inside gmail_client_factory.
        return await client.send_message(
            to=draft.to,
            cc=draft.cc,
            bcc=draft.bcc,
            subject=draft.subject,
            body=draft.body,
        )
```

### 6. Email tools

```python
import hashlib
from uuid import uuid4


class ReadInboxTool:
    capability = "email_read_inbox"
    source_label = "Mailbox"

    def __init__(self, provider: EmailProvider) -> None:
        self.provider = provider

    async def execute(self, params: dict, state: dict) -> EmailToolResult:
        messages = await self.provider.read_inbox(
            state["mailbox_account_id"],
            folder=params.get("folder", "inbox"),
            date_range=params.get("date_range"),
            limit=min(int(params.get("limit", 10)), 25),
        )
        return EmailToolResult(
            data={"messages": messages},
            complete=True,
            missing_fields=[],
            confidence=1.0,
            source_label=self.source_label,
        )


class ReadThreadTool:
    capability = "email_read_thread"
    source_label = "Mailbox"

    def __init__(self, provider: EmailProvider) -> None:
        self.provider = provider

    async def execute(self, params: dict, state: dict) -> EmailToolResult:
        thread_id = params.get("thread_id") or state.get("selected_thread_id")
        message_id = params.get("message_id") or state.get("selected_message_id")
        if not thread_id and not message_id:
            return EmailToolResult({}, False, ["thread_id"], 0.0, self.source_label)
        thread = await self.provider.read_thread(
            state["mailbox_account_id"],
            thread_id=thread_id,
            message_id=message_id,
        )
        return EmailToolResult(
            data={"thread": thread},
            complete=True,
            missing_fields=[],
            confidence=1.0,
            source_label=self.source_label,
        )


class GenerateDraftTool:
    capability = "email_generate_draft"
    source_label = "Draft Generator"

    def __init__(self, model_client: EmailModelClient) -> None:
        self.model_client = model_client

    async def execute(self, params: dict, state: dict) -> EmailToolResult:
        thread = params.get("thread")
        if not thread:
            return EmailToolResult({}, False, ["thread"], 0.0, self.source_label)

        draft = await generate_draft_from_thread(
            model_client=self.model_client,
            thread=thread,
            reply_goal=params.get("reply_goal", "write a helpful reply"),
            tone=params.get("tone", "professional"),
        )
        draft["draft_id"] = f"draft_{uuid4().hex[:12]}"
        state["pending_draft"] = draft
        return EmailToolResult(
            data={"draft": draft},
            complete=True,
            missing_fields=[],
            confidence=0.9,
            source_label=self.source_label,
        )


class SendDraftTool:
    capability = "email_send_draft"
    source_label = "Email Provider"

    def __init__(self, provider: EmailProvider) -> None:
        self.provider = provider

    async def execute(self, params: dict, state: dict) -> EmailToolResult:
        draft = state.get("pending_draft")
        if not draft:
            return EmailToolResult({}, False, ["draft"], 0.0, self.source_label)

        confirmed_id = params.get("_confirmed_action_id")
        pending = state.get("pending_action") or {}
        if not confirmed_id or confirmed_id != pending.get("action_id"):
            return EmailToolResult(
                {"error": "send requires a valid confirmed pending action"},
                False,
                ["_confirmed_action_id"],
                0.0,
                "System",
            )

        result = await self.provider.send_draft(
            state["mailbox_account_id"],
            EmailDraft(**draft),
        )
        return EmailToolResult(
            data={"send_result": result},
            complete=result.get("status") == "sent",
            missing_fields=[],
            confidence=1.0 if result.get("status") == "sent" else 0.0,
            source_label=self.source_label,
        )


def draft_hash(draft: dict) -> str:
    stable = {
        "to": draft.get("to", []),
        "cc": draft.get("cc", []),
        "bcc": draft.get("bcc", []),
        "subject": draft.get("subject", ""),
        "body": draft.get("body", ""),
    }
    return hashlib.sha256(repr(stable).encode("utf-8")).hexdigest()
```

### 7. Draft generation

The model gets only selected thread evidence. It must not receive full mailbox
history or credentials.

```python
from pydantic import BaseModel, Field


class DraftOutput(BaseModel):
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str
    body: str
    based_on_message_ids: list[str] = Field(default_factory=list)


def model_safe_thread(thread: dict) -> dict:
    messages = []
    for item in thread.get("messages", [])[-8:]:
        messages.append({
            "message_id": item.get("message_id"),
            "from": item.get("from"),
            "to": item.get("to"),
            "sent_at": item.get("sent_at"),
            "subject": item.get("subject"),
            "body_excerpt": str(item.get("body", ""))[:3000],
        })
    return {"thread_id": thread.get("thread_id"), "messages": messages}


async def generate_draft_from_thread(
    *,
    model_client: EmailModelClient,
    thread: dict,
    reply_goal: str,
    tone: str,
) -> dict:
    safe_thread = model_safe_thread(thread)
    output = await model_client.call_schema(
        role="email_draft",
        messages=[
            {
                "role": "system",
                "content": (
                    "Create a reply email draft from the supplied thread only. "
                    "Return JSON with to, cc, bcc, subject, body, and based_on_message_ids. "
                    "Do not claim attachments were reviewed unless present in the thread evidence."
                ),
            },
            {
                "role": "user",
                "content": repr({
                    "reply_goal": reply_goal,
                    "tone": tone,
                    "thread": safe_thread,
                }),
            },
        ],
        output_model=DraftOutput,
        fallback_data={
            "to": [],
            "cc": [],
            "bcc": [],
            "subject": "Re:",
            "body": "I need more context before drafting this reply.",
            "based_on_message_ids": [],
        },
    )
    return output.model_dump()
```

### 8. Pending confirmation harness

This is the most important part. It is the email equivalent of the current
flight pending-action flow.

```python
from datetime import datetime, timedelta, timezone
import hashlib
from uuid import uuid4


CONFIRMATION_TTL_SECONDS = 180
CONFIRM_WORDS = {"yes", "y", "confirm", "ok", "okay", "proceed", "go ahead", "send it"}
CANCEL_WORDS = {"no", "n", "cancel", "stop", "leave it", "do not send", "dont send", "don't send"}


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def confirmation_decision(text: str) -> str | None:
    compact = " ".join(text.lower().strip().split())
    if compact in CONFIRM_WORDS:
        return "confirm"
    if compact in CANCEL_WORDS:
        return "cancel"
    return None


def store_pending_send_action(state: dict, draft: dict) -> dict:
    now = datetime.now(timezone.utc)
    action = PendingEmailAction(
        action_id=f"act_{uuid4().hex[:12]}",
        user_id=state["user_id"],
        session_id_hash=hash_text(state["session_id"]),
        capability="email_send_draft",
        draft_id=draft["draft_id"],
        params_hash=draft_hash(draft),
        redacted_diff={
            "to": draft.get("to", []),
            "cc": draft.get("cc", []),
            "subject": draft.get("subject", ""),
            "body_preview": str(draft.get("body", ""))[:240],
        },
        created_at=now,
        expires_at=now + timedelta(seconds=CONFIRMATION_TTL_SECONDS),
        confirmation_prompt=(
            f"Send this email to {', '.join(draft.get('to', []))} "
            f"with subject \"{draft.get('subject', '')}\"? Reply confirm to send or cancel to stop."
        ),
    )
    state["pending_action"] = action.model_dump(mode="json")
    record_email_audit(state, "pending", action.model_dump(mode="json"))
    return state["pending_action"]


def pending_action_status(state: dict) -> tuple[PendingEmailAction | None, str | None]:
    payload = state.get("pending_action")
    if not isinstance(payload, dict):
        return None, None
    try:
        pending = PendingEmailAction.model_validate(payload)
    except Exception:
        state["pending_action"] = None
        return None, "invalid"
    if pending.consumed:
        return pending, "consumed"
    if pending.user_id != state.get("user_id"):
        return pending, "user_mismatch"
    if pending.session_id_hash != hash_text(state.get("session_id", "")):
        return pending, "session_mismatch"
    if pending.expires_at <= datetime.now(timezone.utc):
        return pending, "expired"
    draft = state.get("pending_draft") or {}
    if pending.draft_id != draft.get("draft_id"):
        return pending, "draft_mismatch"
    if pending.params_hash != draft_hash(draft):
        return pending, "draft_changed"
    return pending, "valid"


async def handle_pending_action_reply(text: str, state: dict) -> dict | None:
    pending, status = pending_action_status(state)
    if pending is None:
        return None

    decision = confirmation_decision(text)
    if status != "valid":
        record_email_audit(state, "denied", {"status": status, "pending": pending.model_dump(mode="json")})
        state["pending_action"] = None
        if decision is None:
            return None
        return {
            "response": "That pending email action is no longer valid. Please generate or review the draft again.",
            "source": "System",
            "intent": "email_send_draft",
            "is_clarification": False,
            "draft": state.get("pending_draft"),
            "pending_action": None,
            "debug": {"pending_status": status},
        }

    if decision == "cancel":
        record_email_audit(state, "cancelled", pending.model_dump(mode="json"))
        state["pending_action"] = None
        state["pending_draft"] = None
        return {
            "response": "Cancelled. I did not send anything.",
            "source": "System",
            "intent": "email_send_draft",
            "is_clarification": False,
            "draft": None,
            "pending_action": None,
            "debug": {},
        }

    if decision != "confirm":
        return None

    record_email_audit(state, "confirmed", pending.model_dump(mode="json"))
    state["pending_action"] = pending.model_copy(update={"consumed": True}).model_dump(mode="json")
    result = await execute_tool_plan(
        ToolPlan(
            capability="email_send_draft",
            params={
                "draft_id": pending.draft_id,
                "_confirmed_action_id": pending.action_id,
            },
            side_effect=True,
            source_label="Email Provider",
            reason="user confirmed pending email send",
        ),
        state,
    )
    lifecycle = "executed" if result.status == ToolEnvelopeStatus.SUCCESS else "failed"
    record_email_audit(state, lifecycle, result.model_dump(mode="json"))
    state["pending_action"] = None
    if result.status == ToolEnvelopeStatus.SUCCESS:
        sent_subject = state.get("pending_draft", {}).get("subject", "")
        state["pending_draft"] = None
        return {
            "response": f"Sent. Subject: {sent_subject}",
            "source": "Email Provider",
            "intent": "email_send_draft",
            "is_clarification": False,
            "draft": None,
            "pending_action": None,
            "debug": {"tool_result": result.model_dump(mode="json")},
        }
    return {
        "response": "I could not send the email. The provider did not return confirmed send evidence.",
        "source": "System",
        "intent": "email_send_draft",
        "is_clarification": False,
        "draft": state.get("pending_draft"),
        "pending_action": None,
        "debug": {"tool_result": result.model_dump(mode="json")},
    }
```

### 9. Tool executor with safety gates

```python
PRIVATE_PARAM_KEYS = {
    "password", "smtp_password", "app_password", "access_token",
    "refresh_token", "oauth_token", "cookie", "authorization"
}


def redacted_params(params: dict) -> dict:
    result = {}
    for key, value in params.items():
        if key.lower() in PRIVATE_PARAM_KEYS:
            result[key] = "[REDACTED]"
        else:
            result[key] = value
    return result


async def execute_tool_plan(plan: ToolPlan, state: dict) -> ToolResultEnvelope:
    plan = ToolPlan.model_validate(plan)
    if not state.get("authenticated"):
        raise PermissionError("authenticated session required")
    if plan.capability not in ALLOWED_CAPABILITIES:
        raise PermissionError(f"capability not allowed: {plan.capability}")

    if plan.side_effect:
        pending, status = pending_action_status(state)
        if status != "valid" and not (pending and pending.consumed):
            return ToolResultEnvelope(
                intent_id=plan.capability,
                capability=plan.capability,
                tool_class="PendingConfirmation",
                source_label="System",
                status=ToolEnvelopeStatus.PENDING_CONFIRMATION,
                data={},
                missing_fields=["confirmation"],
                params_redacted=redacted_params(plan.params),
            )
        if plan.params.get("_confirmed_action_id") != pending.action_id:
            return ToolResultEnvelope(
                intent_id=plan.capability,
                capability=plan.capability,
                tool_class="PendingConfirmation",
                source_label="System",
                status=ToolEnvelopeStatus.DENIED,
                data={"error": "confirmed action id mismatch"},
                params_redacted=redacted_params(plan.params),
            )

    tool = REGISTRY.get(plan.capability)
    if tool is None:
        return ToolResultEnvelope(
            intent_id=plan.capability,
            capability=plan.capability,
            tool_class="MissingTool",
            source_label="System",
            status=ToolEnvelopeStatus.DENIED,
            data={"error": "unknown capability"},
            params_redacted=redacted_params(plan.params),
        )

    result = await tool.execute(plan.params, state)
    return ToolResultEnvelope(
        intent_id=plan.capability,
        capability=plan.capability,
        tool_class=type(tool).__name__,
        source_label=result.source_label,
        status=ToolEnvelopeStatus.SUCCESS if result.complete else ToolEnvelopeStatus.EMPTY,
        data=result.data,
        missing_fields=result.missing_fields,
        params_redacted=redacted_params(plan.params),
    )
```

### 10. Agent orchestration skeleton

This is the minimal turn handler. A production implementation should split each
agent into its own file, but keep this order.

```python
async def handle_email_agent_turn(state: dict, user_message: str) -> dict:
    pending_response = await handle_pending_action_reply(user_message, state)
    if pending_response is not None:
        return pending_response

    goal_frame = await goal_frame_agent.propose(user_message, state)
    capability_check = validate_goal_against_email_catalog(goal_frame, state)
    if not capability_check["allowed"]:
        return {
            "response": capability_check["safe_response"],
            "source": "System",
            "intent": "unsupported",
            "is_clarification": False,
            "draft": None,
            "pending_action": None,
            "debug": {"goal_frame": goal_frame.model_dump(mode="json")},
        }

    intent = await intent_agent.decide(user_message, state, goal_frame)
    slots = await slot_agent.extract(user_message, state, intent=intent.intent)

    if slots.slots_missing:
        state["slots_missing"] = slots.slots_missing
        state["slots_filled"].update(slots.slots_filled)
        return {
            "response": slots.clarification_question or f"Please provide: {', '.join(slots.slots_missing)}",
            "source": "System",
            "intent": intent.intent,
            "is_clarification": True,
            "draft": state.get("pending_draft"),
            "pending_action": state.get("pending_action"),
            "debug": {"intent": intent.model_dump(mode="json"), "slots": slots.model_dump(mode="json")},
        }

    if intent.capability == "email_generate_draft":
        thread_result = await execute_tool_plan(
            ToolPlan(
                capability="email_read_thread",
                params={
                    "thread_id": slots.slots_filled.get("thread_id") or state.get("selected_thread_id"),
                    "message_id": slots.slots_filled.get("message_id") or state.get("selected_message_id"),
                },
                side_effect=False,
                source_label="Mailbox",
            ),
            state,
        )
        if thread_result.status != ToolEnvelopeStatus.SUCCESS:
            return {
                "response": "I need a specific email or thread before I can draft a reply.",
                "source": "System",
                "intent": intent.intent,
                "is_clarification": True,
                "draft": None,
                "pending_action": None,
                "debug": {"thread_result": thread_result.model_dump(mode="json")},
            }
        draft_result = await execute_tool_plan(
            ToolPlan(
                capability="email_generate_draft",
                params={
                    "thread": thread_result.data["thread"],
                    "reply_goal": slots.slots_filled.get("reply_goal", user_message),
                    "tone": slots.slots_filled.get("tone", "professional"),
                },
                side_effect=False,
                source_label="Draft Generator",
            ),
            state,
        )
        draft = draft_result.data.get("draft")
        pending = store_pending_send_action(state, draft) if draft else None
        return {
            "response": pending["confirmation_prompt"] if pending else "I could not create a draft.",
            "source": "Mailbox + Draft Generator",
            "intent": intent.intent,
            "is_clarification": bool(not draft),
            "draft": draft,
            "pending_action": pending,
            "debug": {
                "goal_frame": goal_frame.model_dump(mode="json"),
                "intent": intent.model_dump(mode="json"),
                "slots": slots.model_dump(mode="json"),
            },
        }

    plan = ToolPlan(
        capability=intent.capability,
        params=slots.slots_filled,
        side_effect=intent.capability == "email_send_draft",
        source_label=source_label_for_capability(intent.capability),
    )
    tool_result = await execute_tool_plan(plan, state)
    return render_email_agent_response(intent, tool_result, state)
```

### 11. Capability validation

```python
def validate_goal_against_email_catalog(goal: GoalFrameDecision, state: dict) -> dict:
    capability = goal.proposed_capability
    if not capability or capability == "unsupported":
        return {
            "allowed": False,
            "safe_response": (
                "I cannot perform that through the email assistant. "
                "I can read/search mailbox data, draft replies, update drafts, "
                "resolve contacts/groups, and send only after confirmation."
            ),
        }

    if capability not in ALLOWED_CAPABILITIES:
        return {"allowed": False, "safe_response": "That email capability is not approved."}

    if capability == "email_send_draft" and goal.action_class != ActionClass.SIDE_EFFECT_SEND:
        return {"allowed": False, "safe_response": "Sending requires an explicit send action and confirmation."}

    if capability.startswith("email_read") and goal.privacy_class != PrivacyClass.PRIVATE_MAIL:
        return {"allowed": False, "safe_response": "Mailbox reads must stay in the private mail boundary."}

    return {"allowed": True, "safe_response": None}


def source_label_for_capability(capability: str) -> str:
    return {
        "email_read_inbox": "Mailbox",
        "email_search_thread": "Mailbox",
        "email_read_thread": "Mailbox",
        "email_generate_draft": "Draft Generator",
        "email_update_draft": "Draft Generator",
        "email_send_draft": "Email Provider",
        "contact_resolve": "Contacts",
        "group_resolve": "Contacts",
        "template_generate": "Model",
    }.get(capability, "System")
```

### 12. Audit log

Use durable storage in production. At minimum, audit pending, confirmed,
executed, failed, cancelled, denied, and expired.

```python
def record_email_audit(state: dict, lifecycle_status: str, payload: dict) -> None:
    event = {
        "lifecycle_status": lifecycle_status,
        "user_id": state.get("user_id"),
        "session_id_hash": hash_text(state.get("session_id", "")),
        "capability": payload.get("capability"),
        "action_id": payload.get("action_id"),
        "draft_id": payload.get("draft_id"),
        "params_hash": payload.get("params_hash"),
        "source_label": payload.get("source_label", "System"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # Replace this with INSERT into audit table or structured application logger.
    state.setdefault("audit_events", []).append(event)
```

## Security And Privacy Rules

Credential rules:

- Store app passwords/OAuth refresh tokens server-side only.
- Encrypt credentials at rest where the target deployment supports it.
- Never return credentials to the browser.
- Never place credentials in model prompts, model traces, debug JSON, logs, or
  screenshots.
- Prefer OAuth with scoped Gmail/Microsoft permissions over raw password SMTP.
- If SMTP app password is used, store it as a secret and rotate it after testing.

Mailbox data rules:

- Send only the selected thread or a bounded search result to the model.
- Truncate long message bodies before model calls.
- Do not send full mailbox history to the model.
- Do not include unrelated personal emails in context.
- Strip tracking headers, raw MIME internals, and hidden provider metadata unless
  the user explicitly asks to inspect headers and the app supports it.

Send safety rules:

- `email_send_draft` is the only send capability.
- It is side-effecting and confirmation-required.
- Confirmation must bind `user_id`, `session_id_hash`, `draft_id`, `params_hash`,
  `action_id`, and expiry.
- A changed draft invalidates the old confirmation.
- A sent or consumed action id cannot be reused.
- If audit write fails before send, fail closed and do not send.
- If provider returns no send evidence, report failure and do not claim success.

Public model boundary rules:

- Groq/Gemini receives only sanitized user request, safe selected thread context,
  and schema instructions.
- Model output must be parsed as JSON and validated locally.
- Invalid JSON should trigger at most one repair retry, then fail closed or ask
  clarification.
- Do not rely on the model to decide whether a send is confirmed.

## Implementation Order For The Coding Agent

1. Add backend schemas and tests first.
2. Add a capability catalog and deny-by-default validation.
3. Add credential-safe email provider adapters.
4. Add read-only mailbox tools.
5. Add model boundary with Groq/Gemini provider order from environment.
6. Add draft generation from selected thread evidence.
7. Add pending confirmation harness.
8. Add send tool behind the confirmation harness.
9. Add the floating widget and connect it to the chat route.
10. Add browser and integration tests for read, draft, confirm, send, cancel,
    stale confirm, duplicate confirm, wrong-session confirm, and changed draft.

Do not implement send first. The send path is correct only after schemas,
catalog validation, redaction, audit, and pending-confirmation checks exist.

## Testing Checklist

### Unit tests

- `GoalFrameDecision` rejects unknown capability.
- `IntentDecision` rejects unsupported capability.
- `SlotAgentOutput` can represent insert/update/delete/no-change slot events.
- `ToolPlan` rejects invented tool names.
- `PendingEmailAction` rejects expiry before creation.
- `pending_action_status` returns `valid` only for same user/session/draft hash.
- `pending_action_status` returns `expired`, `consumed`, `user_mismatch`,
  `session_mismatch`, `draft_mismatch`, or `draft_changed` correctly.
- `execute_tool_plan` returns `PENDING_CONFIRMATION` for send without
  `_confirmed_action_id`.
- `execute_tool_plan` denies confirmed id mismatch.
- Redaction blocks SMTP password, app password, OAuth tokens, model keys, and
  cookies from model payloads.

### Integration tests

- User asks "which mails came today"; backend returns a bounded mailbox summary
  and no pending action.
- User asks "show replies from Rahul"; backend searches/reads matching thread
  without sending.
- User asks "generate a response to this reply"; backend reads the selected
  thread, creates a draft, stores pending action, and asks confirmation.
- User says "make it shorter"; backend updates the pending draft and creates a
  new confirmation hash.
- User says "confirm"; backend sends exactly the pending draft.
- User says "confirm" again; backend refuses duplicate confirmation.
- User says "confirm" after expiry; backend refuses stale confirmation.
- User switches browser session and confirms; backend refuses session mismatch.
- User changes recipient after confirmation prompt; old confirmation is invalid.
- Provider send failure returns failure and does not claim success.

### Browser tests

- Floating widget opens and closes without breaking the email page.
- User can type a request and see assistant response.
- Draft preview renders `to`, `subject`, and `body`.
- Confirmation prompt is visible before send.
- Cancel clears the pending draft from the widget.
- Voice input fills the text box when browser speech recognition is available.
- No key, token, password, or cookie appears in network response payloads.

### Acceptance checklist

- Read latest inbox/thread without sending.
- Generate reply draft from an actual selected email.
- Refuse to send without confirmation.
- Send only the confirmed draft to the confirmed recipient list.
- Reject stale, mismatched, duplicate, or wrong-session confirmations.
- Never expose email password/API keys/model keys to prompts, logs, or browser
  payloads.

## What The Receiving Coding Agent Must Preserve

If the implementation is changed to another stack, preserve these invariants:

- Capability catalog is positive and deny-by-default.
- Agent outputs are strict JSON schemas.
- Tool execution is backend-owned.
- Credentials stay behind backend provider adapters.
- Mailbox evidence is bounded and redacted before model calls.
- Draft generation is not sending.
- Sending requires a valid pending action and explicit confirmation.
- Audit is written before confirmed send execution.
- State cleanup clears pending drafts/actions on cancel, expiry, failure, and
  successful completion.
- Final user response uses backend/tool source labels, not model-invented
  labels.

## Minimal Environment Variables

```text
EMAIL_AGENT_MODEL_PROVIDERS=groq,gemini
GROQ_API_KEY=<server-side-secret>
GROQ_MODEL=llama-3.1-8b-instant
GEMINI_API_KEY=<server-side-secret>
GEMINI_MODEL=gemini-1.5-flash
EMAIL_AGENT_CONFIRMATION_TTL_SECONDS=180
EMAIL_AGENT_MAX_INBOX_RESULTS=25
SMTP_HOST=<server-side-secret-or-config>
SMTP_PORT=465
SMTP_USERNAME=<server-side-secret>
SMTP_PASSWORD=<server-side-secret>
```

Do not put these values in browser JavaScript. Load them only in backend
configuration.

## Final Mental Model

The assistant should feel autonomous to the user, but it must be governed inside
the code:

```text
Understand -> Plan -> Read evidence -> Draft -> Verify -> Ask confirmation
-> Validate same pending action -> Send -> Audit -> Clear state
```

The model can think and propose. The backend must decide, validate, execute, and
prove.
