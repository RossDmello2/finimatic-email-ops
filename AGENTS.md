# AGENTS.md — Finimatic Agent Addition Session

## Step 0 — Read These Files In Order Before Writing One Line Of Code

1. `docs/reference/PROJECT_IMPLEMENTATION_REPORT.md` — historical implementation baseline
2. `docs/reference/SCHEMA.md`                        — historical DB schema notes
3. `docs/reference/STACK.md`                         — historical tech stack contract
4. `docs/reference/DATA_FLOW.md`                     — historical architecture notes
5. `docs/reference/AI_INTEGRATION.md`                — Groq/Gemini pool contracts
6. `docs/reference/AGENT_SCHEMA_EXTENSION.md`        — two new DB tables + agent file structure
7. `docs/reference/EMAIL_AGENTIC_ASSISTANT_HANDOFF.md` — governed pipeline spec + pydantic schemas
8. `docs/reference/VERBA_ASSISTANT_REPLICATION_GUIDE.md` — widget implementation spec

Do not touch any existing file until all eight are read.

---

## What Already Exists — DO NOT REWRITE OR RESTRUCTURE

Finimatic is a **complete, working** cold-email ops system. Every surface below
is live, tested, and in production use. This session adds one isolated module.

```
backend/app/
  main.py                     ← FastAPI app root. DO NOT modify routes or lifespan.
  db/models.py                ← All existing SQLAlchemy tables. DO NOT alter any.
  db/session.py               ← get_db dependency. Reuse. Do not copy.
  core/crypto.py              ← Fernet encrypt/decrypt. Reuse for session token hash.
  core/idempotency.py         ← sha256 key generation. Reuse for params_hash.
  ai/groq_pool.py             ← GroqKeyPool. REUSE. Do not create a new Groq client.
  ai/groq_scheduler.py        ← GroqAdmissionGovernor. REUSE.
  ai/gemini_pool.py           ← GeminiKeyPool. REUSE.
  ai/gemini_scheduler.py      ← GeminiAdmissionGovernor. REUSE.
  ai/gateway.py               ← AIGateway. REUSE for draft generation calls.
  ai/prompts.py               ← Prompt builders. REUSE or extend.
  send/smtp_adapter.py        ← GmailAdapter. REUSE for conversation sends.
  send/policy.py              ← PolicyDecision. REUSE engaged-send gates.
  replies/imap_fetcher.py     ← IMAP. REUSE. Always run_in_executor.
  audit/service.py            ← emit_event(). REUSE for all agent audit events.
  conversations/router.py     ← existing conversation engine. READ. Do not break.

frontend/src/
  App.tsx                     ← Mount <AssistantWidget /> as last child only.
  api/client.ts               ← Reuse fetch patterns. Do not add Groq/Gemini keys here.
```

---

## What To Build — One Isolated Module

### Backend: `backend/app/agent/`
See `docs/reference/AGENT_SCHEMA_EXTENSION.md` for exact file list and responsibility of each file.

### Frontend: `frontend/src/features/floating-assistant/`
See `docs/reference/AGENT_SCHEMA_EXTENSION.md` for exact file list.
Follow `docs/reference/VERBA_ASSISTANT_REPLICATION_GUIDE.md` sections 4-24 for implementation.

---

## Alembic Migration — Do This First

Add exactly two new tables to the migration chain.
Do NOT alter any existing table. Do NOT drop anything.
See `docs/reference/AGENT_SCHEMA_EXTENSION.md` for exact SQL.

Tables to add:
- `agent_sessions`
- `pending_email_actions`

---

## Implementation Order — Strict, Do Not Reorder

```
Step 1   Alembic migration: agent_sessions + pending_email_actions
Step 2   backend/app/agent/schemas.py — GoalFrame, IntentDecision, SlotAgentOutput,
           ToolPlan, EvidenceEnvelope, PendingEmailAction, AgentChatRequest,
           AgentChatResponse. All models use extra="forbid".
Step 3   backend/app/agent/catalog.py — CAPABILITY_CATALOG dict, deny-by-default
           validate_capability(). See exact capability list below.
Step 4   backend/app/agent/tools.py — READ-ONLY DB tools first.
           contacts, replies, conversations, drafts, queue, follow-ups.
           No send capability yet. Every tool returns bounded, redacted data.
Step 5   backend/app/agent/memory.py — load/save/expire agent_sessions rows
Step 6   backend/app/agent/pending.py — create/validate/consume pending_email_actions
Step 7   backend/app/agent/goal_frame.py through response.py — agent pipeline
           (one file per agent: goal_frame, intent, slot, orchestrator,
           tools_executor, reasoning, verifier, response, repair)
Step 8   backend/app/agent/service.py — full pipeline orchestrator per turn
Step 9   backend/app/agent/router.py — three endpoints:
           POST /api/agent/chat
           POST /api/agent/confirm
           DELETE /api/agent/cancel
           Mount router in main.py under prefix /api/agent
Step 10  Tests (see required test list below) — ALL must pass before frontend
Step 11  frontend/src/features/floating-assistant/ — full widget per Verba guide
Step 12  Mount <AssistantWidget /> in App.tsx as last child before closing tag
Step 13  Browser verification (see acceptance checklist at bottom)
```

---

## Capability Catalog — Implement Exactly This List

Deny everything not listed. Model cannot invent new capabilities.

```python
CAPABILITY_CATALOG = {
    "email_read_inbox": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": [],
        "optional_slots": ["date_range", "limit"],
        "source_label": "Mailbox",
        "max_results": 25,
    },
    "email_search_thread": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": [],
        "optional_slots": ["query", "sender", "recipient"],
        "source_label": "Mailbox",
        "max_results": 10,
    },
    "email_read_thread": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": ["contact_id"],
        "source_label": "Mailbox",
        "max_snippet_chars": 200,
        "max_snippets": 5,
    },
    "email_generate_draft": {
        "class": "draft_local",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": ["contact_id"],
        "optional_slots": ["reply_goal", "tone"],
        "source_label": "Draft Generator",
    },
    "email_update_draft": {
        "class": "draft_local",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": ["pending_draft_id", "instruction"],
        "source_label": "Draft Generator",
    },
    "email_send_draft": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": ["draft_id", "_confirmed_action_id"],
        "source_label": "Email Provider",
    },
    "contact_resolve": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": ["name_or_email"],
        "source_label": "Contacts",
        "max_results": 5,
    },
    "followup_status": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": [],
        "optional_slots": ["contact_id"],
        "source_label": "Follow-ups",
    },
    "queue_status": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": [],
        "source_label": "Queue",
    },
}
```

---

## Agent Pipeline Per Turn

```
POST /api/agent/chat { session_token, message, attachments? }
  → session guard: load or create agent_session (hash session_token with sha256)
  → GoalFrameAgent(Groq) → validate GoalFrame schema (extra=forbid)
  → capability catalog check → deny if not in catalog → return safe denial
  → IntentAgent(Groq) → validate IntentDecision schema
  → SlotAgent(Groq) → validate SlotAgentOutput schema
  → if slots_missing → return clarification_question, STOP. Do not proceed.
  → AgenticToolExecutor → fetch bounded, redacted DB evidence
       max 5 reply snippets per contact
       max 200 chars per snippet
       no raw email headers
       no passwords, keys, credentials
  → ReasoningAgent(Groq) → reasons over redacted evidence only
  → VerifierAgent(Groq) → checks evidence sufficiency
  → if side_effect capability:
       create pending_email_action row
       return confirmation_prompt to frontend
       STOP. Do not execute yet.
  → ResponseAgent(Groq) → final user-facing text
  → save/update agent_session context_summary
  → emit audit_event per docs/reference/AGENT_SCHEMA_EXTENSION.md types
  → return AgentChatResponse
```

---

## Confirmation Harness — The Most Important Part

`email_send_draft` executes ONLY when ALL of these pass:

```python
def validate_pending_action(action_id, session_token, draft_id, db) -> str:
    """
    Returns one of:
      'valid'            — proceed to send
      'not_found'        — no record for this action_id
      'expired'          — expires_at < now()
      'consumed'         — already used
      'session_mismatch' — action.session_id != current session
      'draft_mismatch'   — action.draft_id != provided draft_id
      'hash_mismatch'    — action.params_hash != sha256(current draft content)
    """
```

Any status other than `'valid'` → reject, emit `audit_event(send.confirmation_invalid)`.

A `consumed` action_id can never be reused. Not even once more.
Confirmation TTL = 180 seconds.

---

## Read-Only Tool Data Contracts

All DB reads return redacted, bounded evidence envelopes.
The model NEVER receives: passwords, API keys, SMTP creds, IMAP creds,
raw email headers, full conversation history (max 30 messages).

```python
# Tool: email_read_inbox
# Returns: list of {contact_email, contact_name, reply_classified_as,
#                   raw_summary[:200], received_at}
# Max results: 25

# Tool: email_read_thread  
# Returns: {contact_id, contact_email, messages: last 30 conversation_messages}
# Each message: {direction, body[:200], created_at}
# NEVER returns: SMTP password, Groq keys, Gemini keys, IMAP creds

# Tool: contact_resolve
# Returns: {id, email, creator_name, business_name, status, tags}
# Max results: 5 matches

# Tool: followup_status
# Returns: {contact_id, sequence_num, status, due_at, stop_reason}

# Tool: queue_status
# Returns: {pending_count, sent_today, blocked_count, next_due_at}
```

---

## DB Tool Write Contracts

```python
# Tool: email_generate_draft
# Uses: existing AIGateway.generate_draft() from backend/app/ai/gateway.py
# Stores draft in drafts table (approved=False)
# Returns: {draft_id, subject, body, to, warnings}

# Tool: email_send_draft
# Requires: _confirmed_action_id that passes validate_pending_action()
# Uses: existing GmailAdapter.send_message() via run_in_executor
# Uses: existing policy gates from send/policy.py (engaged-send subset)
# Records: SendAttempt, ConversationMessage, audit_event(agent.send_executed)
# Returns: {status, sent_at, provider_msg_id}
```

---

## Security Hard Rules — Enforce In Every File Written

1. `VITE_API_URL` is the only Vite env var. Never add Groq/Gemini keys to frontend.
2. Model receives only: sanitized user message + bounded redacted DB evidence.
3. Model output is validated against strict pydantic schemas before any action.
4. `email_send_draft` cannot execute without a valid, unconsumed pending_email_action.
5. Session token is stored as sha256 hash only — never raw in DB.
6. Every send attempt (success or fail) emits audit_event.
7. `imaplib` and `smtplib` always run in `asyncio.get_event_loop().run_in_executor`.
8. APScheduler is NOT used for agent — agent is request-scoped only.
9. All agent pydantic schemas use `model_config = ConfigDict(extra="forbid")`.
10. No raw secret (password, key, token) in any agent response, log, or audit payload.

---

## Required Tests — ALL Must Pass Before Frontend Work Begins

```
test_capability_deny           → request outside catalog → denied, safe message
test_slot_missing              → no contact_id → clarification_question returned
test_tool_read_inbox           → "who replied today" → DB data only, no hallucination
test_tool_read_thread          → bounded snippet, max 200 chars, no headers
test_confirmation_required     → email_send_draft without confirm → REJECTED
test_confirmation_valid        → correct action_id + session + hash → send executes
test_confirmation_consumed     → same action_id twice → REJECTED as consumed
test_confirmation_expired      → action_id after 180s → REJECTED as expired
test_confirmation_session_mismatch → different session → REJECTED
test_confirmation_draft_changed    → params_hash changed → REJECTED as hash_mismatch
test_cancel                    → pending draft cleared, no email sent, audit written
test_no_raw_key_in_response    → no gsk_ or AIza prefix in any agent response
test_generate_draft_not_send   → email_generate_draft does NOT trigger smtp
test_audit_written             → every turn emits at minimum one agent audit event
```

---

## Widget Behavior — Follow VERBA Guide Sections 4–24

- Fixed bottom-right launcher. Shell uses `pointer-events: none`. Launcher and panel use `pointer-events: auto`.
- Panel opens above launcher.
- Header controls: new chat, history, copy last answer, clear thread, minimize, maximize, close.
- Footer: textarea + model selector (groq/gemini/auto mirrors existing settings) + attach + mic + send.
- All CSS classes prefixed `va-`. Never overwrite global styles.
- Conversation history in `localStorage` (message text only, no file bytes).
- Draft text in `sessionStorage`.
- Voice: browser SpeechRecognition only — paste to input, never auto-send.
- When agent returns a pending draft: show a confirmation card with to/subject/body.
  - Confirm button → POST /api/agent/confirm { session_token, action_id }
  - Cancel button → DELETE /api/agent/cancel { session_token }
- Unread badge on launcher when panel is closed and assistant replied.
- Escape key closes panel.

---

## Acceptance Checklist — Verify All Before Marking Complete

```
□ cd backend && python -m pytest → ALL existing tests still pass
□ cd frontend && npm run build → clean, zero errors
□ Widget launcher visible bottom-right on all 12 existing dashboard surfaces
□ Existing dashboard navigation, forms, mutations → fully unbroken
□ "who replied today?" → returns names/emails from DB replies table (last 24h)
□ "show [contact]'s thread" → returns bounded snippet, no raw headers
□ "generate a reply for [contact]" → draft card appears with to/subject/body
□ "send it" WITHOUT clicking Confirm → REJECTED with reason message
□ Confirm button → email lands in recipient inbox → audit_event(agent.send_executed)
□ Confirm button again (same action_id) → REJECTED as consumed
□ Wait 3 min then Confirm → REJECTED as expired
□ "cancel" → clears draft card, no email sent, audit_event(agent.session_cancelled)
□ Switch dashboard page, reopen widget → history preserved in localStorage
□ No raw gsk_ or AIza key visible in any network response payload
□ No app password or SMTP cred in any agent response
□ pytest count must equal or exceed existing 27 passed (new tests added on top)
□ Model selector in widget uses existing Groq/Gemini keys from Settings (not new env vars)
```

---

## Credentials Rule

All Groq and Gemini keys are stored in the existing settings DB (Fernet-encrypted).
The agent module reads them via existing `settings/service.py` decrypt methods.
Do NOT add new env vars for AI keys.
Do NOT add GROQ_API_KEY or GEMINI_API_KEY to any .env or Vite config.

---

## mount point in main.py

After all existing router mounts, add exactly:

```python
from backend.app.agent.router import router as agent_router
app.include_router(agent_router, prefix="/api/agent")
```

Do not change any other line in main.py.

---

## Final Deliverable After All Steps Pass

Produce a summary covering:
- Files added (full path list)
- Files modified (main.py mount only + App.tsx widget mount only)
- Test count before vs after
- Pipeline trace for one full send (goal → intent → slots → tool → pending → confirm → send)
- Confirmation harness proof (consumed test + expired test output)
- Browser evidence (screenshot of widget open + draft card + sent email)
- Secret scan: no gsk_ / AIza / password in any response payload
- Mark NOT COMPLETE if any checklist item lacks evidence
