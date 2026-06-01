# Finimatic — Agent Schema Extension

This file defines the two new DB tables and the complete file structure
for the governed floating assistant module.
It extends SCHEMA.md without altering any existing table.

---

## New Table 1: agent_sessions

One row per browser tab session. Expires after 30 minutes of inactivity.
Session token is stored as sha256 hash — never raw.

```sql
CREATE TABLE agent_sessions (
    id                  TEXT PRIMARY KEY,
    session_token_hash  TEXT NOT NULL UNIQUE,   -- sha256(browser_generated_uuid)
    current_goal        TEXT,                   -- last understood user goal, plain text
    slots               TEXT,                   -- JSON: active slot values
                                                -- { contact_id, draft_id, tone,
                                                --   date_range, reply_goal }
    active_contact_id   TEXT REFERENCES contacts(id),
    pending_action_id   TEXT,                   -- FK to pending_email_actions.id
    context_summary     TEXT,                   -- safe redacted summary for model context
                                                -- NEVER contains passwords/keys/raw email
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at          DATETIME NOT NULL        -- updated_at + 30 minutes
);
```

---

## New Table 2: pending_email_actions

Confirmation harness for every send side effect.
A send executes ONLY if a valid, unconsumed record exists with matching
session_id + draft_id + params_hash + not expired + not consumed.

```sql
CREATE TABLE pending_email_actions (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES agent_sessions(id),
    action_type         TEXT NOT NULL DEFAULT 'email_send_draft',
    capability          TEXT NOT NULL DEFAULT 'email_send_draft',
    draft_id            TEXT NOT NULL REFERENCES drafts(id),
    contact_id          TEXT NOT NULL REFERENCES contacts(id),
    params_hash         TEXT NOT NULL,  -- sha256(draft_id + contact_id + subject + body)
    source_label        TEXT NOT NULL DEFAULT 'Email Provider',
    confirmation_prompt TEXT NOT NULL,  -- shown to user before send
    expires_at          DATETIME NOT NULL,  -- created_at + 180 seconds
    consumed            BOOLEAN NOT NULL DEFAULT FALSE,
    consumed_at         DATETIME,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### Validation States (enforced in application code, never by model)

```
pending_action_status(action_id, session_id, draft_id, params_hash) →
  'valid'            all five checks pass
  'not_found'        no record for this action_id
  'expired'          expires_at < now()
  'consumed'         consumed = TRUE
  'session_mismatch' action.session_id != provided session_id
  'draft_mismatch'   action.draft_id != provided draft_id
  'hash_mismatch'    action.params_hash != sha256(current draft content)
```

Any status other than 'valid' → reject send + emit audit_event(send.confirmation_invalid).

---

## New audit_event Types (add to existing list in SCHEMA.md)

```
agent.goal_framed
agent.intent_resolved
agent.slots_filled
agent.clarification_asked
agent.capability_denied
agent.tool_executed
agent.draft_generated
agent.confirmation_created
agent.confirmation_valid
agent.confirmation_invalid
agent.confirmation_expired
agent.send_executed
agent.send_failed
agent.session_cancelled
agent.repair_triggered
```

---

## Backend File Structure (new files only)

```
backend/app/agent/
  __init__.py

  schemas.py
    # All pydantic models. All use ConfigDict(extra="forbid").
    # GoalFrame, IntentDecision, SlotAgentOutput, ToolPlan,
    # EvidenceEnvelope, PendingEmailAction,
    # AgentChatRequest, AgentChatResponse, AgentConfirmRequest, AgentCancelRequest
    # Source: adapt from EMAIL_AGENTIC_ASSISTANT_HANDOFF.md pydantic section

  catalog.py
    # CAPABILITY_CATALOG dict (see AGENTS.md for full spec)
    # validate_capability(value: str) -> str | raises ValueError
    # get_capability(name: str) -> dict | None
    # source_label_for_capability(name: str) -> str

  tools.py
    # AgenticToolExecutor class
    # _read_inbox(session, db) -> EvidenceEnvelope
    #   reads replies table, last 24h, max 25, returns bounded snippets
    # _read_thread(contact_id, session, db) -> EvidenceEnvelope
    #   reads conversation_messages, last 30, max 200 chars per body
    # _search_contacts(name_or_email, db) -> EvidenceEnvelope
    #   reads contacts table, max 5 matches
    # _followup_status(contact_id, db) -> EvidenceEnvelope
    #   reads follow_up_sequences for contact or all pending
    # _queue_status(db) -> EvidenceEnvelope
    #   reads send_queue pending/sent counts
    # _generate_draft(contact_id, reply_goal, tone, session, db) -> EvidenceEnvelope
    #   calls existing AIGateway.generate_draft()
    #   stores result in drafts table (approved=False, ai_provider)
    # _send_draft(draft_id, confirmed_action_id, session, db) -> EvidenceEnvelope
    #   validates pending_email_action first
    #   uses existing GmailAdapter.send_message() via run_in_executor
    #   records SendAttempt, ConversationMessage, audit_event(agent.send_executed)
    # execute(tool_plan: ToolPlan, session, db) -> EvidenceEnvelope
    #   dispatches to correct _method, enforces capability catalog
    #   for side_effect capabilities: validates pending_action_status first

  memory.py
    # load_session(session_token_hash, db) -> AgentSession | None
    # create_session(session_token_hash, db) -> AgentSession
    # update_session(session_id, updates, db) -> AgentSession
    # expire_session(session_id, db) -> None
    # cleanup_expired_sessions(db) -> int  (called lazily on each request)

  pending.py
    # create_pending_action(session_id, draft_id, contact_id, subject, body, db)
    #   -> PendingEmailAction
    # validate_pending_action(action_id, session_id, draft_id, params_hash, db)
    #   -> Literal['valid','not_found','expired','consumed',
    #               'session_mismatch','draft_mismatch','hash_mismatch']
    # consume_pending_action(action_id, db) -> None
    # cancel_pending_action(session_id, db) -> None
    # params_hash(draft_id, contact_id, subject, body) -> str
    #   sha256(f"{draft_id}|{contact_id}|{subject}|{body}")

  goal_frame.py
    # GoalFrameAgent class
    # propose(message, session_summary) -> GoalFrame
    # Single Groq call. Returns GoalFrame pydantic. Falls back to UNSUPPORTED on error.

  intent.py
    # IntentAgent class
    # decide(message, session_summary, goal_frame) -> IntentDecision
    # Single Groq call. Returns IntentDecision. Validates capability in catalog.

  slot.py
    # SlotAgent class
    # extract(message, session_summary, intent) -> SlotAgentOutput
    # Single Groq call. Returns SlotAgentOutput.
    # On missing slots: sets clarification_question, ready_to_execute=False.

  orchestrator.py
    # OrchestratorAgent class
    # Used only for multi-step plans (e.g. read thread THEN generate draft).
    # plan(intent, slots, session) -> list[ToolPlan]
    # Single Groq call. Returns ordered list of ToolPlan.

  reasoning.py
    # ReasoningAgent class
    # reason(message, intent, evidence_envelopes) -> ReasoningResult
    # Single Groq call. Evidence is redacted before passing.
    # NEVER passes raw passwords, keys, full email bodies > 200 chars.

  verifier.py
    # VerifierAgent class
    # verify(message, intent, reasoning_result) -> VerificationDecision
    # Single Groq call. Checks sufficiency and safety before response.

  response.py
    # ResponseAgent class
    # compose(message, intent, verification, evidence) -> str
    # Single Groq call. Produces final user-facing text.
    # Uses source_label from evidence, never model-invented labels.

  repair.py
    # RepairRouter class
    # handle(error_type, context) -> RepairAction
    # schema_error -> retry once with repair prompt
    # low_confidence -> ask clarification
    # tool_fail -> safe error message
    # all else -> fail closed with safe message

  service.py
    # AgentService class — orchestrates full pipeline per turn
    # chat(request: AgentChatRequest, db) -> AgentChatResponse
    #   Full pipeline: session → goal → catalog → intent → slots →
    #   [clarification if needed] → tools → reasoning → verifier →
    #   [pending action if side_effect] → response → save session → audit
    # confirm(request: AgentConfirmRequest, db) -> AgentChatResponse
    #   Validates pending_action_status → executes send → audit → clear
    # cancel(request: AgentCancelRequest, db) -> AgentChatResponse
    #   Clears pending draft and action → audit → return confirmation

  router.py
    # FastAPI router
    # POST /api/agent/chat    → AgentService.chat()
    # POST /api/agent/confirm → AgentService.confirm()
    # DELETE /api/agent/cancel → AgentService.cancel()
    # No credentials in request body. Session_token is a browser UUID.
    # Mount in main.py: app.include_router(agent_router, prefix="/api/agent")
```

---

## Backend Test File

```
backend/tests/test_agent.py
  # All 14 required tests from AGENTS.md
  # Uses existing conftest.py test app fixture
  # Uses FakeTransport for send tests
  # All tests must pass before frontend work begins
```

---

## Frontend File Structure (new files only)

```
frontend/src/features/floating-assistant/
  AssistantWidget.tsx
    # Full widget per VERBA_ASSISTANT_REPLICATION_GUIDE.md
    # va-shell (pointer-events:none) → va-launcher + va-panel
    # Header: new-chat, history, copy-last, clear, minimize, maximize, close
    # Messages: user bubbles (blue) + assistant bubbles (gray)
    # Pending draft card: when agent returns pending_action, show:
    #   To: [email]  Subject: [subject]  Body: [first 300 chars...]
    #   [CONFIRM SEND] [CANCEL]
    # Footer: textarea + model-selector + attach + mic + send
    # Escape key closes panel
    # All CSS class names prefixed va-
    # Reuses lucide-react icons (already installed)
    # Reuses sonner toast (already installed)

  assistantStore.ts
    # React state: isOpen, messages, pendingDraft, confirmationState,
    #   currentConversationId, conversations, model, isSending, isListening
    # localStorage keys: va_conversations, va_current_id, va_ui, va_model
    # sessionStorage keys: va_draft
    # No raw file bytes or API keys stored anywhere

  assistantApi.ts
    # POST /api/agent/chat(message, session_token) -> AgentChatResponse
    # POST /api/agent/confirm(session_token, action_id) -> AgentChatResponse
    # DELETE /api/agent/cancel(session_token) -> AgentChatResponse
    # Uses VITE_API_URL (already defined in project)
    # session_token = uuid generated once per browser tab, stored in sessionStorage

  AssistantWidget.css
    # All styles prefixed va-
    # va-shell, va-panel, va-launcher, va-header, va-actions,
    # va-messages, va-bubble, va-footer, va-input-wrap, va-composer-row,
    # va-draft-card, va-draft-confirm-btn, va-draft-cancel-btn
    # Never overrides global button, input, body, or select styles
    # Mobile responsive per Verba guide section 8
```

### Mount Point in App.tsx

Add exactly this as the last child before the closing `</div>` of the App root:

```tsx
import { AssistantWidget } from './features/floating-assistant/AssistantWidget';

// At the bottom of the return statement, before </div>:
<AssistantWidget />
```

Do not change any other line in App.tsx.

---

## Model Selector in Widget

The widget model selector options must map to existing Finimatic providers:

```typescript
const AGENT_MODELS = [
  { id: "auto", label: "Auto (Groq → Gemini)", provider: "auto" },
  { id: "groq", label: "Groq", provider: "groq" },
  { id: "gemini", label: "Gemini", provider: "gemini" },
];
```

These values are sent to `/api/agent/chat` as `provider`. The backend agent
reads keys from the existing settings DB via the existing settings service.
No new API keys are added anywhere.

---

## Evidence Envelope Contract

Every tool returns an EvidenceEnvelope. The model only sees this envelope.

```python
class EvidenceEnvelope(StrictModel):
    capability: str
    source_label: str
    status: Literal["success", "empty", "error", "denied"]
    data: dict[str, Any]     # bounded, redacted — see per-tool max sizes above
    missing_slots: list[str]
    error_code: str | None = None
    latency_ms: int = 0
```

Fields never allowed in `data`:
- Any value containing a Fernet key, Groq key (`gsk_`), Gemini key (`AIza`)
- Any value containing `app_password`, `smtp_password`, `imap_password`
- Any value longer than 200 characters if it came from a reply/email body
- Any SMTP response string
- Raw IMAP headers

---

## Confirmation Flow — Frontend to Backend

```
1. User types: "generate a reply for Sarah"
   → POST /api/agent/chat { session_token, message: "generate a reply for Sarah" }
   ← AgentChatResponse {
       response: "I've drafted a reply for Sarah. Review and confirm below.",
       draft: { draft_id, subject, body, to },
       pending_action: { action_id, confirmation_prompt, expires_at }
     }

2. Widget shows draft card. User reads it.

3a. User clicks CONFIRM:
    → POST /api/agent/confirm { session_token, action_id }
    ← AgentChatResponse { response: "Sent. Subject: Re: ...", pending_action: null }

3b. User clicks CANCEL:
    → DELETE /api/agent/cancel { session_token }
    ← AgentChatResponse { response: "Cancelled. I did not send anything.", pending_action: null }

3c. User clicks CONFIRM again (consumed):
    → POST /api/agent/confirm { session_token, action_id }
    ← AgentChatResponse { response: "That message was already sent. I won't send it again." }

3d. After 180s, user clicks CONFIRM (expired):
    → POST /api/agent/confirm { session_token, action_id }
    ← AgentChatResponse { response: "That confirmation expired. Please generate the draft again." }
```

---

## Alembic Migration File

Create as the next migration after the existing `0001_initial.py`.
Filename: `backend/app/db/migrations/versions/0002_agent_tables.py`

```python
"""Add agent_sessions and pending_email_actions tables

Revision ID: 0002_agent_tables
Revises: 0001_initial
Create Date: 2026-05-23
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_agent_tables"
down_revision = "0001_initial"


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("session_token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("current_goal", sa.Text),
        sa.Column("slots", sa.Text),
        sa.Column("active_contact_id", sa.Text, sa.ForeignKey("contacts.id")),
        sa.Column("pending_action_id", sa.Text),
        sa.Column("context_summary", sa.Text),
        sa.Column("created_at", sa.DateTime, nullable=False,
                  server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime, nullable=False,
                  server_default=sa.func.current_timestamp()),
        sa.Column("expires_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "pending_email_actions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("session_id", sa.Text, sa.ForeignKey("agent_sessions.id"), nullable=False),
        sa.Column("action_type", sa.Text, nullable=False, server_default="email_send_draft"),
        sa.Column("capability", sa.Text, nullable=False, server_default="email_send_draft"),
        sa.Column("draft_id", sa.Text, sa.ForeignKey("drafts.id"), nullable=False),
        sa.Column("contact_id", sa.Text, sa.ForeignKey("contacts.id"), nullable=False),
        sa.Column("params_hash", sa.Text, nullable=False),
        sa.Column("source_label", sa.Text, nullable=False, server_default="Email Provider"),
        sa.Column("confirmation_prompt", sa.Text, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("consumed", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("consumed_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime, nullable=False,
                  server_default=sa.func.current_timestamp()),
    )


def downgrade() -> None:
    op.drop_table("pending_email_actions")
    op.drop_table("agent_sessions")
```

---

## Note On Runtime Table Creation

The project uses `Base.metadata.create_all` at startup (not Alembic at runtime).
After creating the SQLAlchemy models for the two new tables in `db/models.py`,
the tables will be created automatically on next server start if they don't exist.
The Alembic file above provides the forward-migration path for production.
Both approaches must be consistent (same columns, same constraints).
