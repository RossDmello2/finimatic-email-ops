# Finimatic Implementation Report

Date: 2026-05-23
Scope: documentation-only source audit of the current workspace. No core project file was edited for this report.

This file documents what exists in the current implementation, why each source file exists, how routing works, how data moves, and which extra features were implemented beyond the original cold-email MVP documents.

## Current Truth

- Backend: FastAPI app under `backend/app`, mounted from `backend/app/main.py`.
- Frontend: React 18 + TypeScript + Vite single-page dashboard under `frontend/src`.
- Database: SQLAlchemy models create tables at runtime with `Base.metadata.create_all`; Alembic exists but `0001_initial.py` is a placeholder.
- Primary active DB observed: `backend/finimatic.db`.
- Secondary DB observed: `backend/finimatic_browser.db`; it is empty/stale compared with current source because it lacks newer tables such as `conversation_messages` and `templates`.
- Git: this workspace is not a Git repository.
- Secrets: `KEYS.md` and `backend/.env` exist locally and are ignored by `.gitignore`. This report does not include credential values.

## Root Files

| File | Why it exists | Role |
|---|---|---|
| `AGENTS.md` | Defines build order, security rules, and required docs to read before edits. | Agent operating contract. |
| `GOAL_PROMPT.md` | Defines the full P0-P9 build objective and final report shape. | Top-level implementation goal. |
| `PRD.md` | Defines product workflow, recipient lifecycle, policy gates, dashboard surfaces, and V1/V2 scope. | Product requirements. |
| `SCHEMA.md` | Defines expected tables and constraints. | Database source requirements. |
| `STACK.md` | Defines backend/frontend stack, env rules, Gmail adapter contract, and policy dataclasses. | Technical stack contract. |
| `AI_INTEGRATION.md` | Defines Groq/Gemini storage, provider fallback, output schema, and AI limits. | AI behavior contract. |
| `DATA_FLOW.md` | Defines architecture, flows, and intended file structure. | Architecture map. |
| `TESTING.md` | Defines test suites A-J and browser proof requirements. | Verification contract. |
| `KEYS.md` | Holds local test credentials. | Test setup only; never hardcoded or documented with values. |
| `.gitignore` | Excludes local secrets, DBs, caches, `node_modules`, and build output. | Secret/build hygiene. |
| `PROJECT_IMPLEMENTATION_REPORT.md` | Created by this documentation pass. | Single-file implementation inventory. |

## Backend Routing Map

`backend/app/main.py` creates the FastAPI app, configures CORS, initializes the DB, seeds settings, starts background workers unless disabled, and mounts all routers.

| Route Prefix | File | Endpoints | Purpose |
|---|---|---|---|
| `/api/health` | `backend/app/main.py` | `GET /api/health` | Health check. |
| `/api/settings` | `backend/app/settings/router.py` | `GET`, `POST`, `POST /verify-smtp` | Settings read/write and SMTP verification. |
| `/api/canary` | `backend/app/send/canary_router.py` | `POST /send` | One-time sender canary send with duplicate block. |
| `/api/import` | `backend/app/imports/router.py` | `POST /preview`, `POST /commit` | Lead preview/commit import. |
| `/api/contacts` | `backend/app/contacts/router.py` | `GET`, `POST`, `PATCH /{id}` | Contact list/create/status edits. |
| `/api/conversations` | `backend/app/conversations/router.py` | `GET`, `GET /{contact_id}`, `POST /{contact_id}/generate-reply`, `POST /{contact_id}/send` | Saved conversation history, AI reply generation, engaged reply sending. |
| `/api/drafts` | `backend/app/drafts/router.py` | `GET`, `POST`, `PATCH /{id}`, `POST /generate`, `POST /generate-bulk`, `GET /bulk-status/{job_id}`, `POST /approve-bulk`, `POST /{id}/subject-variants`, `POST /{id}/approve` | Draft creation, AI generation, bulk generation, approval, queue creation. |
| `/api/templates` | `backend/app/templates/router.py` | `GET`, `POST` | Reusable approved email templates. |
| `/api/queue` | `backend/app/send/router.py` | `GET`, `POST`, `GET /{id}`, `POST /process` | Send queue list/create/process. |
| `/api/followups` | `backend/app/followups/router.py` | `GET`, `GET /{id}`, `PATCH /{id}`, `POST /process` | Follow-up sequence list/edit/process. |
| `/api/suppressions` | `backend/app/suppressions/router.py` | `GET`, `POST` | Suppression list and manual suppression add. |
| `/api/replies` | `backend/app/replies/router.py` | `GET`, `POST`, `POST /fetch`, `POST /{id}/archive`, `POST /{id}/restore`, `DELETE /{id}` | Manual/IMAP reply records and lifecycle cleanup. |
| `/api/audit` | `backend/app/audit/router.py` | `GET` | Audit event stream. |

## Database Tables

| Table | Implemented in | Purpose |
|---|---|---|
| `settings` | `backend/app/db/models.py` | Stores config and encrypted secrets. |
| `contacts` | `backend/app/db/models.py` | Stores leads/prospects and lifecycle state. |
| `import_batches` | `backend/app/db/models.py` | Stores committed import batch metadata. |
| `import_rows` | `backend/app/db/models.py` | Stores row-level import outcome. |
| `drafts` | `backend/app/db/models.py` | Stores manual/AI email drafts and approval state. |
| `templates` | `backend/app/db/models.py` | Stores reusable subject/body templates. |
| `send_queue` | `backend/app/db/models.py` | Stores pending/sent/blocked/skipped send jobs. |
| `send_attempts` | `backend/app/db/models.py` | Stores canary, queue, and conversation send attempts. |
| `follow_up_sequences` | `backend/app/db/models.py` | Stores due/stopped/dispatched follow-up records. |
| `suppressions` | `backend/app/db/models.py` | Stores do-not-contact emails. |
| `replies` | `backend/app/db/models.py` | Stores reply classifications and archived state. |
| `conversation_messages` | `backend/app/db/models.py` | Stores inbound/outbound conversation timeline per contact. |
| `audit_events` | `backend/app/db/models.py` | Stores redacted event history. |
| `provider_health` | `backend/app/db/models.py` | Stores provider status records. |

Observed `backend/finimatic.db` counts at audit time: 2 contacts, 36 conversation messages, 18 replies, 19 send attempts, 453 audit events, 1 draft, 1 queue entry, 1 follow-up, 1 template.

## Backend File Roles

| File | Why it was created | Feature role |
|---|---|---|
| `backend/requirements.txt` | Pins backend dependencies. | FastAPI, SQLAlchemy, cryptography, Groq, Gemini, APScheduler, pytest. |
| `backend/sample.env.example` | Documents allowed backend env values. | Shows only `FERNET_KEY`, server, DB, and CORS env usage. |
| `backend/pytest.ini` | Configures backend tests. | Uses `tests`, async mode, and temp pytest base. |
| `backend/alembic.ini` | Alembic config entrypoint. | Present for migrations. |
| `backend/app/main.py` | FastAPI composition root. | CORS, DB init, settings seed, routers, queue/follow-up loops, IMAP scheduler. |
| `backend/app/core/crypto.py` | Secret encryption and fingerprint helper. | Fernet bootstrap, encrypt/decrypt, sha256[:12] fingerprints, redacted error text. |
| `backend/app/core/idempotency.py` | Deterministic key helper. | SHA-256 idempotency keys for canary/queue/conversation sends. |
| `backend/app/core/time.py` | Central time helpers. | UTC timestamps, parse/format helpers. |
| `backend/app/db/models.py` | SQLAlchemy model definitions. | Implements every current table including extra `templates` and `conversation_messages`. |
| `backend/app/db/session.py` | DB engine/session setup. | Normalizes SQLite URL, creates tables, applies lightweight reply-column migrations. |
| `backend/app/db/migrations/env.py` | Alembic environment. | Migration runner setup. |
| `backend/app/db/migrations/script.py.mako` | Alembic template. | Default revision template. |
| `backend/app/db/migrations/versions/0001_initial.py` | Initial migration placeholder. | Does not create schema; runtime `create_all` is the actual schema path. |
| `backend/app/settings/service.py` | Settings business logic. | Defaults, secret encryption, key parsing, fingerprints, mode label, warm-up caps. |
| `backend/app/settings/router.py` | Settings API. | Read/update settings and verify SMTP. |
| `backend/app/ai/schema.py` | AI response models. | `DraftSuggestion` and `AIFailure`. |
| `backend/app/ai/key_utils.py` | Key parsing/fingerprints. | Splits comma/semicolon/whitespace keys, dedupes, fingerprints. |
| `backend/app/ai/prompts.py` | Cold outreach prompt builders. | Sender profile, campaign context, draft user prompt. |
| `backend/app/ai/gateway.py` | AI draft gateway. | Manual, Groq, Gemini, auto fallback, fake AI mode, malformed fallback, subject variants, contact enrichment. |
| `backend/app/ai/groq_pool.py` | Groq key-pool utility. | LRU acquire, cooldown, quarantine, exhausted code. Not wired into main draft gateway. |
| `backend/app/ai/groq_scheduler.py` | Groq concurrency helper. | Async semaphore wrapper. |
| `backend/app/ai/gemini_pool.py` | Gemini pool type alias. | Reuses Groq pool behavior for Gemini keys. |
| `backend/app/ai/gemini_scheduler.py` | Gemini scheduler type alias. | Reuses Groq scheduler behavior. |
| `backend/app/contacts/utils.py` | Contact utility rules. | Tags, blocked domains, token replacement, send-window checks. |
| `backend/app/contacts/router.py` | Contact API. | List/create/patch contacts, store tags/custom fields. |
| `backend/app/imports/service.py` | Import engine. | CSV/TXT/paste/manual parsing, row validation, suppression/duplicate checks, replay-safe commit, Groq enrichment. |
| `backend/app/imports/router.py` | Import API. | Preview and commit endpoints. |
| `backend/app/drafts/router.py` | Draft API. | Manual draft, AI draft, bulk jobs, approval, queue creation, subject variants, templates from approved drafts. |
| `backend/app/templates/router.py` | Template API. | List/create reusable templates, optionally from approved draft. |
| `backend/app/send/smtp_adapter.py` | Gmail SMTP adapter. | Verify, send message, canary send, real SMTP or fake transport. |
| `backend/app/send/fake_transport.py` | Test transport. | Records sends in memory and avoids real Gmail in tests. |
| `backend/app/send/canary_router.py` | Canary API. | Sends nonce email, records attempt, sets `canary_verified`, blocks duplicates. |
| `backend/app/send/policy.py` | Queue policy evaluator. | Sender, canary, draft approval, suppression, bounce, reply, pause, caps, delay, idempotency gates. |
| `backend/app/send/queue_worker.py` | Queue worker. | Processes due queue, dry-run skip, SMTP send, send attempt, conversation message, follow-up schedule. |
| `backend/app/send/router.py` | Queue API. | Queue list/create/get/process endpoints. |
| `backend/app/followups/service.py` | Follow-up engine. | Stop-condition checks, Groq/manual follow-up draft generation, dispatch queueing, next follow-up scheduling. |
| `backend/app/followups/router.py` | Follow-up API. | List/get/patch/process follow-ups. |
| `backend/app/replies/service.py` | Reply lifecycle logic. | Create reply, stop follow-ups, add inbound conversation message, refresh contact status. |
| `backend/app/replies/imap_fetcher.py` | Gmail IMAP fetcher. | Reads recent inbox, matches contact replies after latest send, classifies with Groq, stores reply. |
| `backend/app/replies/router.py` | Replies API. | List/create/fetch/archive/restore/delete replies. |
| `backend/app/conversations/router.py` | Active conversation engine. | Saves thread history, backfills from sends/replies, generates context-aware replies, sends engaged replies, applies extra gates. |
| `backend/app/suppressions/router.py` | Suppression API. | List and add suppressed emails. |
| `backend/app/audit/service.py` | Audit writer. | Redacts payload keys/secret prefixes and writes events. |
| `backend/app/audit/router.py` | Audit API. | Returns audit event list. |

## Frontend File Roles

| File | Why it was created | Feature role |
|---|---|---|
| `frontend/package.json` | Defines frontend package and scripts. | `npm run dev`, `npm run build`, React/TanStack/lucide/sonner dependencies. |
| `frontend/package-lock.json` | Locks npm dependency versions. | Reproducible frontend install. |
| `frontend/index.html` | Vite HTML entrypoint. | Mounts React app. |
| `frontend/vite.config.ts` | Vite config. | Build/dev tooling. |
| `frontend/tsconfig.json` | TypeScript config. | TS compilation. |
| `frontend/tailwind.config.js` | Tailwind config. | CSS utility setup. |
| `frontend/postcss.config.js` | PostCSS config. | Tailwind/autoprefixer pipeline. |
| `frontend/src/main.tsx` | React entrypoint. | Mounts `App`, query client, toasts. |
| `frontend/src/App.tsx` | Main dashboard UI. | All surfaces, forms, modals, tables, conversation UI, mutations, query invalidation. |
| `frontend/src/api/client.ts` | Typed API wrapper. | Centralizes all backend endpoint calls and response types. |
| `frontend/src/styles.css` | App styling. | Layout, panels, tables, conversation thread, responsive behavior. |
| `frontend/src/vite-env.d.ts` | Vite type declaration. | TypeScript env support. |

Note: `react-router-dom` is installed, but the current UI does not use URL routes. It uses local `surface` state inside `App.tsx` to switch dashboard sections.

## Generated And Runtime Files

| Path | Why it exists | Role |
|---|---|---|
| `backend/.env` | Generated/stored local Fernet key. | Secret; ignored; not documented with value. |
| `backend/finimatic.db` | Runtime SQLite DB. | Active observed app data. |
| `backend/finimatic_browser.db` | Older/browser SQLite DB. | Empty/stale DB snapshot. |
| `backend/uvicorn.log`, `.backend-uvicorn*.log`, `.frontend-vite.log` | Dev server logs. | Runtime diagnostics. |
| `frontend/node_modules/` | Installed npm dependencies. | Generated dependency tree. |
| `frontend/dist/` | Vite production build output. | Generated static build. |
| `__pycache__/`, `.pytest_cache/`, `.pytest-tmp/` | Python/test caches. | Generated test/runtime cache. |

## Implemented Feature Flows

### Settings And Secrets

1. UI posts settings to `POST /api/settings`.
2. `settings.service.set_settings` encrypts `gmail_app_password`, `groq_keys`, and `gemini_keys`.
3. `GET /api/settings` returns key counts and sha256[:12] fingerprints only.
4. App password, Groq textarea, and Gemini textarea are cleared from React state after save/verify success.
5. Mode label is derived as `DRY-RUN`, `CANARY`, or `LIVE`.

Extra settings implemented beyond the base docs:

- `campaign_context`
- `sender_name`
- `sender_role`
- `sender_offer`
- `sender_tone`
- `sender_signature`
- `groq_model`
- fixed `gemini_model` as `gemini-2.5-flash`
- `follow_up_template_1`
- `follow_up_template_2`
- `blocked_domains`
- send window start/end/timezone
- `warm_up_mode` and warm-up cap ramp
- `imap_fetch_interval_minutes`

### Gmail Verification And Canary

1. `POST /api/settings/verify-smtp` loads sender and decrypted app password.
2. `GmailAdapter.verify` logs into Gmail SMTP unless fake transport is enabled.
3. `POST /api/canary/send` generates an idempotency key from sender and report recipient.
4. Existing successful canary attempt returns `duplicate_blocked`.
5. New successful canary records a `SendAttempt`, sets `canary_verified=true`, sets `sender_readiness=canary_verified`, and returns nonce/timestamp/message id.

### Import

1. UI supports manual fields, paste text, CSV, and TXT upload.
2. Frontend parses CSV/TXT uploads into rows before preview.
3. Backend preview validates email, required creator/business field, in-payload duplicate, existing duplicate, blocked domain, and suppression match.
4. Preview does not create contacts.
5. Commit re-runs validation, creates batch/row records, creates accepted contacts, emits audit events, and returns contact ids.
6. A background task can enrich imported contacts with Groq if a website exists and personalization is empty.

### Contacts And Tags

- Contacts store source, notes, personalization, lead category, and custom fields.
- Tags are parsed and stored under `custom_fields.tags`.
- Contacts panel can filter by tag.
- Contacts patch can update status/notes/personalization.

### Drafts, AI, Bulk Generation, And Templates

1. Manual drafts are stored with `ai_provider=manual`.
2. AI draft generation supports `manual`, `groq`, `gemini`, `auto`, and `malformed_test`.
3. `auto` tries Groq first and falls back to Gemini if Groq fails.
4. AI output is parsed as JSON and validated into `DraftSuggestion`.
5. Malformed AI output produces an empty unapproved draft, `error_code`, and `draft.ai_failed` audit event.
6. Draft approval sets `approved=true`, sets `approved_at`, updates contact to `approved`, creates a queue entry, and emits audit.
7. Bulk draft generation runs in a background thread and tracks in-memory job status.
8. Bulk approval approves selected drafts and queues them.
9. Subject variants use Groq and return three cleaned subject lines where possible.
10. Approved drafts can be saved as reusable templates.

Precision note: `GroqKeyPool` exists and is tested as a utility, but normal draft generation in `AIGateway._call_groq` uses the first Groq key. Gemini draft generation loops through configured Gemini keys.

### Queue And Policy

1. Approval creates a `send_queue` row with idempotency key `sha256(contact_id, sequence_num, draft_id)`.
2. Worker scans `pending` and `skipped` entries due at or before now.
3. If global send window is closed, worker emits `SEND_WINDOW_CLOSED` audit and leaves the entry pending.
4. Policy gates check sender readiness, canary, draft approval, suppression/domain block, bounce, reply/unsubscribe, manual pause, daily cap, hourly cap, send delay, and successful idempotency duplicate.
5. Gate failure sets queue status `blocked`, updates contact to `blocked_by_policy`, stores reason codes, and emits audit.
6. Dry-run records a blocked dry-run send attempt and sets queue status `skipped`.
7. Live send resolves template tokens, sends via Gmail, records a successful send attempt, sets contact to `sent`, writes an outbound `conversation_messages` row, emits `send.success`, and schedules the first follow-up.

### Token Replacement

The backend and frontend both support these draft/template tokens:

- `{{first_name}}`
- `{{full_name}}`
- `{{website}}`
- `{{niche}}`

Backend replacement happens immediately before queue sends.

### Follow-Ups

1. First live queue send schedules sequence `2` after `followup_interval_days`.
2. Follow-up processor finds due rows.
3. It stops on replied, unsubscribed, suppressed, bounced, manually paused, suppression table match, active reply/unsubscribe/bounce/complaint, or max follow-up reached.
4. If not stopped, it creates an approved follow-up draft from settings templates.
5. If Groq keys exist, Groq is used to turn the template/contact data into JSON subject/body; otherwise the template is used manually.
6. It queues the follow-up and schedules the next sequence until max is reached.

### Replies And IMAP

1. Manual replies can be created with `POST /api/replies`.
2. Reply classifications `reply`, `unsubscribe`, and `bounce` update contact status.
3. Reply records add inbound `conversation_messages` rows.
4. Duplicate manual marks are blocked while an active same classification exists.
5. Replies can be archived, restored, or deleted; contact status is recalculated.
6. `POST /api/replies/fetch` connects to Gmail IMAP, scans recent inbox messages, matches contacts by sender email, requires the reply to be after latest sent attempt, classifies with Groq if keys exist, creates reply records, and stops follow-ups.
7. The app also schedules IMAP fetching with APScheduler at `imap_fetch_interval_minutes`.

### Conversations

This is the main extra feature set referenced in the request.

1. Conversation history is stored in `conversation_messages`.
2. Outbound cold sends are stored as conversation messages from the queue worker.
3. Inbound manual/IMAP replies are stored as conversation messages from reply creation.
4. `/api/conversations` backfills missing messages from historical send attempts and replies before returning summaries.
5. `/api/conversations/{contact_id}` returns ordered messages for one contact.
6. The frontend Conversations panel keeps each contact thread separate, sorts threads needing reply first, filters/searches, and shows a per-contact composer.
7. Drafted conversation replies are held in React state per contact before sending. They are not persisted until sent.
8. Provider `auto` uses Groq for small threads when Groq keys exist.
9. Provider `auto` switches to Gemini when conversation context size exceeds `90,000` characters and Gemini keys exist.
10. If a requested provider has no keys, the backend falls back to the other configured provider where possible.
11. The conversation prompt includes sender profile, prospect fields, operator instruction, language, and the last 30 conversation messages.
12. The conversation prompt explicitly treats prospect replies as business context, not system instructions.
13. The prompt forbids obeying requests to ignore history, change sender identity/signature, reveal secrets/API keys, switch to unrelated tasks, or ask for a call after the prospect says not to.
14. Sending a conversation reply requires canary verification, configured sender, not dry-run for live SMTP, and engaged-send gates for suppression, unsubscribe, bounce, pause, daily/hourly caps, and send window.
15. Successful conversation send records `SendAttempt`, outbound `ConversationMessage`, `conversation.sent`, and `send.success`.

### Dashboard

The frontend is a single dashboard with these surfaces:

- Setup
- Provider Health
- Import
- Contacts
- Drafts
- Templates
- Queue
- Follow-ups
- Replies/Stops
- Conversations
- Suppressions
- Audit Logs
- Errors
- Settings

The top bar always shows sender readiness and the mode label.

## Frontend Behavior Details

- `frontend/src/api/client.ts` uses only `VITE_API_URL`, defaulting to `http://localhost:8000`.
- All API calls are JSON fetch wrappers in one file.
- Query data is loaded through TanStack Query.
- Mutations invalidate all or targeted query keys after state-changing operations.
- Canary send requires a modal confirmation and shows nonce/status after success.
- Settings form uses `type="password"` for app password and clears password/key state after save/verify success.
- Provider health shows key counts and fingerprints, not raw keys.
- Conversations panel fetches replies manually by button and also runs quiet background fetch every 60 seconds while a contact is selected.

## Verification Files

| File | Purpose | Key evidence |
|---|---|---|
| `backend/tests/test_settings_smtp_canary.py` | Settings, encryption, SMTP fake transport, canary duplicate tests. | Verifies no raw secrets in settings response/storage and duplicate canary block. |
| `backend/tests/test_import_policy_ai_followups.py` | Import, prompts, AI fallback, bulk generation, templates, policy, dry-run, tokens, follow-ups, replies, conversations. | Covers implemented behavior including conversation provider routing and thread separation. |
| `backend/tests/conftest.py` | Test app fixture. | Uses temp SQLite DB, fake transport, fake scheduler disabled. |
| `verification_artifacts/crc_conversation_stress_verification.md` | Live CRC browser stress verification summary. | Documents 30-message target, prompt weaknesses found, fix applied, and regression result. |
| `verification_artifacts/crc_30_chat_live_transcript.md` | Live conversation transcript. | Shows multi-turn CRC conversation and final prompt-injection regression. |
| `verification_artifacts/parallel_conversation_script.md` | Stress-test script for two simultaneous conversations. | Defines pass criteria for thread separation and provider routing. |
| `verification_artifacts/conversation-app-proof.png` | Browser proof screenshot. | Visual conversation app evidence. |
| `verification_artifacts/gmail-recipient-proof.png` | Gmail proof screenshot. | Recipient inbox proof. |

Existing verification artifact claims:

- `cd backend && python -m pytest` -> 27 passed.
- `cd frontend && npm run build` -> passed clean.

This documentation-only pass did not rerun the full browser or test suite; it inspected source, tests, DB schema/counts, and existing verification artifacts.

## Security Controls Present

- `.gitignore` includes `KEYS.md`, `.env`, `backend/.env`, DB files, caches, `node_modules`, and `dist`.
- Fernet encryption is used for Gmail password and provider keys.
- Settings API returns configured booleans, counts, and fingerprints only.
- Audit payloads redact suspicious secret field names and known secret prefixes.
- UI clears credential fields after save/verify success.
- AI cannot approve drafts or directly trigger queued cold sends.
- Canary verification is required before live lead send and conversation send.
- Policy gates cannot be bypassed through the queue route.
- Conversation prompt includes explicit prompt-injection defenses around secrets, identity, history, unrelated tasks, and call requests.

## Important Limitations And Gaps

- Alembic migration `0001_initial.py` is a placeholder; schema creation currently depends on runtime `Base.metadata.create_all`.
- `backend/finimatic_browser.db` is stale/empty relative to current source.
- Groq draft generation uses the first Groq key in the main `AIGateway`, even though `GroqKeyPool` exists.
- Queue send-window closure leaves queue entries pending and emits audit rather than storing `SEND_WINDOW_CLOSED` in `policy_block_reasons`.
- Conversation reply sends are direct engaged sends, not normal queue entries.
- Frontend navigation is state-based, not URL-routed.
- Browser/Gmail proof was not rerun during this report creation.

## High-Level End-To-End Flow

1. Operator configures Settings.
2. Backend encrypts secrets and returns safe fingerprints.
3. Operator verifies SMTP.
4. Operator sends canary.
5. Canary success unlocks live sending.
6. Operator imports contacts.
7. Contacts can be enriched, tagged, filtered, drafted manually or by AI.
8. Drafts remain unapproved until operator approval.
9. Approval creates queue entries.
10. Queue worker evaluates policy gates.
11. Dry-run skips SMTP; live mode sends through Gmail.
12. Successful sends create send attempts, conversation messages, audit events, and follow-up sequences.
13. Replies can be entered manually or fetched through IMAP.
14. Replies update contact lifecycle, stop follow-ups, and append inbound conversation messages.
15. Conversations surface uses saved history and provider routing to generate context-aware replies.
16. Conversation replies can be sent directly after canary and engaged-send gates pass.

