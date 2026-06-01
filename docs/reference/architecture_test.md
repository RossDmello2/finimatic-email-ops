# Finimatic Architecture Test Pack

Generated: 2026-05-26 15:39:52 +05:30

Purpose: give a local LLM a source-grounded architecture map and a set of architecture-level test prompts for finding hidden flaws, feature conflicts, and unsafe interactions in this Finimatic web application.

This document treats live source as the highest authority. Older reports are included only as evidence when they conflict with current source.

## Evidence Boundary

Workspace: `C:\Users\rossd\OneDrive\Documents\notes\email`

Mandatory docs read before writing:

- `PROJECT_IMPLEMENTATION_REPORT.md`
- `SCHEMA.md`
- `STACK.md`
- `DATA_FLOW.md`
- `AI_INTEGRATION.md`
- `AGENT_SCHEMA_EXTENSION.md`
- `EMAIL_AGENTIC_ASSISTANT_HANDOFF.md`
- `VERBA_ASSISTANT_REPLICATION_GUIDE.md`
- `AGENTS.md`

Skills used for method and review:

- `skill-orchestrator`
- `karpathy-coding-discipline`
- `fact-check-skill`
- `ultrathink`
- `llm-council`
- supporting: `doc-coauthoring`, `review`, `plan`

Verification snapshot:

- Backend test suite: `python -m pytest` from `backend` collected 182 items and passed 182, with 108 deprecation warnings.
- Frontend build: `npm run build` from `frontend` passed.
- Backend health: `GET http://127.0.0.1:8000/api/health` returned 200 and `{"status":"ok"}`.
- Frontend dev server: `GET http://localhost:5173/` returned 200.
- OpenAPI: `GET http://127.0.0.1:8000/openapi.json` returned 200 and includes `/api/agent/chat`, `/api/agent/confirm`, `/api/agent/cancel`.
- Browser automation: Playwright opened `http://localhost:5173/`, clicked the floating assistant launcher, verified `.va-shell` pointer-events `none`, `.va-panel` pointer-events `auto`, and model options `auto,groq,gemini`. Screenshot: `browser-evidence/architecture-doc-assistant-open.png`.

## Current System Shape

Finimatic is a local cold-email operations app with a FastAPI backend, SQLite database, React/Vite frontend, Gmail send/fetch integrations, Groq/Gemini AI generation, queue/follow-up automation, conversation reply handling, auto-reply handling, campaign planning, and a governed floating assistant.

Current running commands observed:

- Backend from `backend`: `python -m uvicorn app.main:app --host localhost --port 8000`
- Frontend from `frontend`: `npm run dev -- --host localhost --port 5173`

Current runtime mode from safe `GET /api/settings`:

- `mode=LIVE`
- `sender_readiness=canary_verified`
- `dry_run=false`
- Gmail configured
- Groq key count 3
- Gemini key count 6

This matters because any send-capable route or confirmation path should be treated as high-risk. This document does not execute live sends.

## Backend Architecture

Entrypoint:

- `backend/app/main.py`
  - Creates the FastAPI app.
  - Runs startup lifespan setup.
  - Calls `configure_database()`, `init_db()`, `seed_settings()`.
  - Starts periodic queue/follow-up worker loops.
  - Starts APScheduler IMAP fetch job unless `FINIMATIC_DISABLE_SCHEDULER=1`.
  - Mounts routers at lines 103-118; `agent_router` is mounted last with prefix `/api/agent`.

Important lifecycle side effect:

- `backend/app/db/session.py` uses `Base.metadata.create_all()` plus `_apply_lightweight_migrations()` at startup.
- Alembic migrations are not the only schema truth.
- This is a production portability risk because `0001_initial.py` is a no-op and later lightweight columns are not fully represented in Alembic.

Main backend modules:

- `settings`: encrypted Gmail/Groq/Gemini settings and readiness.
- `imports`: CSV/text import preview and commit.
- `contacts`: contact lifecycle, soft delete, restore, tags.
- `drafts`: AI/manual draft creation, template save, bulk generation, approval.
- `templates`: reusable subject/body templates.
- `campaigns`: campaign plan creation, patching, activation, AI step suggestions.
- `send`: canary, policy gates, queue worker, Gmail SMTP adapter, fake transport.
- `followups`: due follow-up proposal and approval.
- `replies`: manual reply records, IMAP fetching, reply classification, suppression/stop logic.
- `conversations`: thread summaries, reply generation, engaged direct sends.
- `auto-reply`: pending auto-reply proposals, approval/reject log, autonomous mode.
- `provider_health`: provider health status.
- `audit`: redacted audit event storage.
- `agent`: floating assistant backend, channel routing, governed tool execution, pending-send confirmation harness.

## Frontend Architecture

Entrypoint:

- `frontend/src/main.tsx`
  - React root.
  - `QueryClientProvider`.
  - Renders `App`.
  - Renders `Toaster`.

Application shell:

- `frontend/src/App.tsx`
  - Single routed dashboard state called `surface`.
  - Surface registry includes 16 surfaces:
    - `setup`
    - `health`
    - `import`
    - `contacts`
    - `drafts`
    - `templates`
    - `campaigns`
    - `queue`
    - `followups`
    - `replies`
    - `conversations`
    - `autoReply`
    - `suppressions`
    - `audit`
    - `errors`
    - `settings`
  - Mounts `<AssistantWidget />` once as the final child.

API client:

- `frontend/src/api/client.ts`
  - Uses only `import.meta.env.VITE_API_URL ?? "http://localhost:8000"`.
  - Contains dashboard API functions.
  - Does not store provider keys in frontend env vars.

Floating assistant frontend:

- `frontend/src/features/floating-assistant/assistantApi.ts`
  - Uses only `VITE_API_URL`.
  - Stores tab session token in `sessionStorage` key `va_session_token`.
  - Calls `/api/agent/chat`, `/api/agent/confirm`, `/api/agent/cancel`.
- `frontend/src/features/floating-assistant/assistantStore.ts`
  - Uses `localStorage` for conversations, current id, UI, model.
  - Uses `sessionStorage` for current draft.
  - Strips file bytes before storing attachments.
  - Strips pending draft body before storing pending action metadata.
- `frontend/src/features/floating-assistant/AssistantWidget.tsx`
  - Launcher, panel, header controls, textarea, model select, attach, mic, send.
  - Uses browser SpeechRecognition only.
  - Does not auto-send voice input.
  - Sends attachment metadata only, not file bytes/text.
- `frontend/src/features/floating-assistant/AssistantWidget.css`
  - `va-` class prefix.
  - Shell has `pointer-events: none`.
  - Launcher/panel have `pointer-events: auto`.
  - Assistant z-index is 80.

## Database Architecture

Current active DB:

- `backend/finimatic.db`
- 17 tables:
  - `agent_sessions`
  - `audit_events`
  - `campaign_plans`
  - `contacts`
  - `conversation_messages`
  - `drafts`
  - `follow_up_sequences`
  - `import_batches`
  - `import_rows`
  - `pending_email_actions`
  - `provider_health`
  - `replies`
  - `send_attempts`
  - `send_queue`
  - `settings`
  - `suppressions`
  - `templates`

Stale DB:

- `backend/finimatic_browser.db`
- 12 empty/older tables.
- Missing `agent_sessions`, `pending_email_actions`, `campaign_plans`, `conversation_messages`, `templates`.
- Do not use this DB as current behavior evidence.

Core live model classes:

- `Setting`
- `Contact`
- `Draft`
- `Template`
- `SendQueue`
- `SendAttempt`
- `FollowUpSequence`
- `Suppression`
- `Reply`
- `ConversationMessage`
- `AuditEvent`
- `ProviderHealth`
- `CampaignPlan`
- `AgentSession`
- `PendingEmailActionRow`

Schema drift warning:

- `SCHEMA.md` is stale against current models.
- Live `contacts` include `deleted_at` and `auto_reply_override`.
- Live `drafts` include `notes`, `source`, `rejected`.
- Live `replies` include `intent`, `external_message_id`, `archived_at`.
- Live app has `conversation_messages`, `templates`, `campaign_plans`, `agent_sessions`, `pending_email_actions`.

Alembic drift warning:

- `backend/app/db/migrations/versions/0001_initial.py` is a no-op.
- `0002_agent_tables.py` creates `agent_sessions` and `pending_email_actions`, but not live `AgentSession` columns `context_loaded_at`, `contact_name_map`, `turn_history`, `current_channel`.
- `0003_reply_followup_campaigns.py` alters existing tables and creates `campaign_plans`.
- Runtime startup migrations in `session.py` add columns not represented in Alembic parity.

## Route Map

Source router mounts:

- `GET /api/health`
- `/api/settings`
  - `GET ""`
  - `POST ""`
  - `POST /verify-smtp`
- `/api/provider-health`
  - `GET ""`
- `/api/canary`
  - `POST /send`
- `/api/import`
  - `POST /preview`
  - `POST /commit`
- `/api/contacts`
  - `GET ""`
  - `GET /recently-deleted`
  - `POST ""`
  - `PATCH /{contact_id}`
  - `DELETE /{contact_id}`
  - `POST /{contact_id}/restore`
- `/api/conversations`
  - `GET ""`
  - `GET /{contact_id}`
  - `POST /{contact_id}/generate-reply`
  - `POST /{contact_id}/send`
- `/api/auto-reply`
  - `POST /approve/{draft_id}`
  - `POST /reject/{draft_id}`
  - `GET /pending`
  - `GET /log`
- `/api/drafts`
  - `GET ""`
  - `POST ""`
  - `PATCH /{draft_id}`
  - `POST /generate`
  - `POST /generate-bulk`
  - `GET /bulk-status/{job_id}`
  - `POST /approve-bulk`
  - `POST /{draft_id}/subject-variants`
  - `POST /{draft_id}/approve`
- `/api/templates`
  - `GET ""`
  - `POST ""`
- `/api/campaigns`
  - `GET ""`
  - `POST ""`
  - `GET /{campaign_id}`
  - `PATCH /{campaign_id}`
  - `POST /{campaign_id}/activate`
- `/api/queue`
  - `GET ""`
  - `POST ""`
  - `GET /{queue_id}`
  - `POST /process`
- `/api/followups`
  - `GET ""`
  - `GET /{sequence_id}`
  - `PATCH /{sequence_id}`
  - `POST /{sequence_id}/approve-draft`
  - `POST /process`
- `/api/replies`
  - `GET ""`
  - `POST ""`
  - `POST /fetch`
  - `POST /{reply_id}/archive`
  - `POST /{reply_id}/restore`
  - `DELETE /{reply_id}`
- `/api/suppressions`
  - `GET ""`
  - `POST ""`
  - `DELETE /{suppression_id}`
- `/api/audit`
  - `GET ""`
- `/api/agent`
  - `POST /chat`
  - `POST /confirm`
  - `DELETE /cancel`

Important REST caveat:

- `GET /api/conversations` and `GET /api/conversations/{contact_id}` call backfill code and commit changes. Browser inspection can therefore mutate conversation rows even through GET.

## Data Flow Tests For A Local LLM

Use these as architecture tests. The goal is to find contradictions and unsafe paths, not to confirm happy paths only.

### Settings And Secrets

Expected flow:

1. User enters Gmail user, app password, Groq keys, Gemini keys in frontend.
2. `frontend/src/App.tsx` sends settings through `frontend/src/api/client.ts`.
3. `backend/app/settings/service.py` encrypts `gmail_app_password`, `groq_keys`, `gemini_keys`.
4. `settings_read()` returns configured booleans, key counts, and fingerprints only.
5. AI and send modules read decrypted secrets only on backend.

Architecture tests:

- Can any frontend response, localStorage value, sessionStorage value, audit payload, or provider health payload reveal raw `gsk_`, `AIza`, Fernet token, app password, SMTP password, or IMAP credential?
- Does `Save & Verify SMTP` clear Groq/Gemini textarea state in every success path, not only password state?
- Does `core/crypto.py` writing `backend/.env` when `FERNET_KEY` is absent create local secret sprawl or restart drift?

### Import To Contact

Expected flow:

1. Frontend Import surface calls `/api/import/preview`.
2. `imports/service.py` normalizes rows, validates emails, checks duplicates and suppressions.
3. Preview state is stored in process-local `PREVIEWS`.
4. Commit writes `import_batches`, `import_rows`, `contacts`.
5. Optional enrichment updates contact fields.

Architecture tests:

- What happens if backend restarts between preview and commit?
- Can a stale preview commit after suppressions changed?
- Are duplicate and suppression decisions repeated at commit time or only in preview?
- Does personalization imported for AI context later leak into agent evidence?

### Draft Generation And Approval

Expected flow:

1. Drafts surface calls `/api/drafts/generate`, `/api/drafts/generate-bulk`, or manual draft routes.
2. `drafts/router.py` builds an `AIGateway`.
3. Groq/Gemini keys are read from encrypted settings.
4. AI output is sanitized and stored as unapproved `drafts`.
5. Approval creates `send_queue` entries.

Architecture tests:

- Are subject variants and campaign enrichment still using first key directly while normal Groq draft generation rotates keys?
- Can bulk draft jobs be lost because job state is an in-memory global dict/thread?
- Can a rejected or deleted-contact draft still be approved by any path?

### Queue Send Path

Expected flow:

1. Approved draft creates `send_queue`.
2. Queue worker calls `send/policy.py:evaluate_policy`.
3. Gates check canary, sender config, draft approval, deleted contact, suppression/domain, bounce, reply/no-reply, manual pause, caps, window, idempotency.
4. Dry-run records blocked attempt; live mode sends Gmail through `GmailAdapter`.
5. Success writes `send_attempts`, outbound `conversation_messages`, follow-up schedule, audit.

Architecture tests:

- Is every direct-send path using the same gate set as queue sends?
- Are conversation sends and agent sends allowed to bypass `no_reply` intentionally because they are engaged replies?
- Are idempotency keys exposed in API responses useful for debugging but risky for replay analysis?
- Do failed sends record enough audit evidence without leaking SMTP details?

### Replies And IMAP

Expected flow:

1. Manual or IMAP reply creates a `Reply`.
2. Reply classification sets intent.
3. Follow-ups stop.
4. Suppressions may be created for unsubscribe/hostile negative replies.
5. Inbound `conversation_messages` are added.
6. Auto-reply may create a proposed draft or autonomous send.

Architecture tests:

- `AGENTS.md` says IMAP should always run in executor. Current scheduler/request path calls synchronous IMAP fetch code. Is this acceptable under FastAPI threading and scheduler behavior?
- Provider health currently reports IMAP failed with `TimeoutError`. What user-facing flows assume IMAP is healthy?
- Can duplicate external message ids map incorrectly across contacts?
- Does archived/deleted reply state consistently refresh contact status and follow-up stops?

### Conversations And Auto-Reply

Expected flow:

1. Conversation GET routes backfill messages from attempts/drafts/replies.
2. Conversation reply generation uses recent thread context.
3. Conversation direct send checks engaged-send gates and sends through Gmail.
4. Auto-reply service can propose drafts or send autonomously when enabled and safe.

Architecture tests:

- Does a GET route mutating backfill data violate expectations for caches, browser prefetch, or verification?
- Do direct send gates differ between conversations, auto-reply, and agent?
- Is autonomous auto-reply compatible with older docs saying AI never triggers SMTP?
- Are auto-reply daily caps, safety classification, and canary/readiness gates enforced before any autonomous send?

### Floating Assistant And Governed Agent

Current agent flow:

1. Frontend sends `session_token`, message, selected provider, and attachment metadata to `/api/agent/chat`.
2. `AgentService.chat()` creates/loads `agent_sessions` using a hash of the session token.
3. The service builds a context card and may generate proactive/contextual responses.
4. Channel router classifies message as awareness/task/action.
5. Awareness queries can bypass the strict capability catalog and use campaign intelligence.
6. Task/action path uses:
   - `GoalFrameAgent`
   - `IntentAgent`
   - `check_capability_tiered`
   - `SlotAgent`
   - `OrchestratorAgent`
   - `AgenticToolExecutor`
   - `ReasoningAgent`
   - `VerifierAgent`
   - `ResponseAgent`
7. `email_generate_draft` stores an unapproved draft and creates a pending confirmation action.
8. Text like `send it` inside chat is rejected and tells the user to click Confirm.
9. `/api/agent/confirm` validates the pending action and sends only on valid confirmation.
10. `/api/agent/cancel` consumes pending actions and clears state.

Current agent capability catalog:

- `email_read_inbox`
- `email_search_thread`
- `email_read_thread`
- `email_generate_draft`
- `email_update_draft`
- `email_send_draft`
- `contact_resolve`
- `followup_status`
- `queue_status`

Agent confirmation harness:

- Pending action TTL is 180 seconds.
- Action binds session, draft id, contact id, subject, body through `params_hash`.
- Validation statuses:
  - `valid`
  - `not_found`
  - `expired`
  - `consumed`
  - `session_mismatch`
  - `draft_mismatch`
  - `hash_mismatch`

Architecture tests:

- Does awareness routing conflict with the stricter "deny everything not in catalog" requirement?
- Does `CAPABILITY_TIERS` create a second capability universe that should be documented as read-only awareness, not action capability?
- Does `contact_resolve` return unbounded `personalization`, and could that become a privacy issue if response/reasoning agents are later swapped back to LLM calls?
- Does consuming a pending action before SMTP send make transient provider failures hard to retry?
- Is the public confirm payload unable to exercise `draft_mismatch` directly because the client does not send `draft_id`?
- Does localStorage retaining pending action metadata conflict with the widget guide's "message text only" storage rule?
- Does accepting attachments while sending metadata only create misleading UX?

## Current Verification Records

Commands and results:

- `python -m pytest` in `backend`: 182 passed, 108 warnings, 126.17s.
- `npm run build` in `frontend`: `tsc && vite build` passed, Vite built `dist/index.html`, CSS, JS.
- SQLite read-only introspection:
  - `backend/finimatic.db`: 17 tables, including agent/campaign/conversation/template tables.
  - `backend/finimatic_browser.db`: 12 older tables, missing newer tables.
- Safe source secret scan:
  - `backend/app`, `frontend/src`, and backend tests contain no raw live key patterns.
  - Tests intentionally contain fake redaction strings shaped like Groq/Gemini/app-password secrets to verify redaction behavior.
- Runtime safe GET probes:
  - `/api/health`: 200.
  - `/openapi.json`: 200 and includes agent endpoints.
  - frontend `/`: 200.
- Browser screenshot:
  - `browser-evidence/architecture-doc-assistant-open.png`.

Warnings from verification:

- Tests pass but emit deprecation warnings for `datetime.utcnow()` in agent context/campaign/formatter code.
- Provider health reports IMAP failed with `TimeoutError`.
- The app is live-mode/canary-verified; live send probes were intentionally not run.

## Architecture-Level Conflict Signals

The detailed relation/conflict ledger is in `relation.md`. The highest priority source-grounded conflicts are:

1. Alembic does not reconstruct the live schema.
2. `SCHEMA.md`, `PROJECT_IMPLEMENTATION_REPORT.md`, and parts of `DATA_FLOW.md` are stale relative to current agent/campaign/auto-reply/conversation source.
3. The agent runtime is not the exact Groq-backed pipeline described in the implementation instructions; most pipeline stages are deterministic/rule-based.
4. Strict capability catalog coexists with broader tiered capability routing and awareness fallback.
5. Direct send policies are duplicated across queue, conversations, auto-reply, and agent.
6. Browser GET of conversations can write backfilled rows.
7. Floating assistant accepts attachments but sends only metadata.
8. LocalStorage stores pending action metadata, not only message text.
9. IMAP fetch code is synchronous in scheduler/request path despite a hard instruction that IMAP work should run in an executor.

## Recommended Local LLM Prompt

Paste this document together with `files34.md` and `relation.md` into the local LLM and ask:

```text
You are reviewing a local FastAPI/React cold-email operations system. Use the attached architecture_test.md, files34.md, and relation.md as source-grounded evidence. Do not assume older project docs are current unless relation.md says they match live source. Find flaws, hidden feature conflicts, unsafe state transitions, stale-doc implementation gaps, and policy divergences. Prioritize issues that can cause wrong sends, leaked secrets, broken migrations, stale UI behavior, lost work, or operational deadlocks. For each issue, provide: severity, exact source paths/functions, reproduction idea, expected behavior, observed behavior, and safest fix.
```
