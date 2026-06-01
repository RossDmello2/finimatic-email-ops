# Finimatic Deployment Intelligence Report

Date: 2026-05-27
Workspace: `C:\Users\rossd\OneDrive\Documents\notes\email`
Scope: read-only source, schema, API, and deployment-hosting audit for the current web application.

Update for public repository readers: this report is a historical deployment audit. The PostgreSQL driver gap identified below has since been addressed in `backend/requirements.txt` with `psycopg[binary]`; the authentication/access-control warning remains the primary public deployment blocker.

## Verification Summary

This report is grounded in live source and runtime-adjacent checks, not only older docs.

Verified inputs:

- Required project docs read in order: `PROJECT_IMPLEMENTATION_REPORT.md`, `SCHEMA.md`, `STACK.md`, `DATA_FLOW.md`, `AI_INTEGRATION.md`, `AGENT_SCHEMA_EXTENSION.md`, `EMAIL_AGENTIC_ASSISTANT_HANDOFF.md`, `VERBA_ASSISTANT_REPLICATION_GUIDE.md`.
- Live backend source inspected: `backend/app/main.py`, `backend/app/db/models.py`, `backend/app/db/session.py`, routers under `backend/app/**/router.py`, `backend/app/agent/*`, `backend/app/ai/gateway.py`, `backend/app/send/*`, `backend/app/settings/service.py`.
- Live frontend source inspected: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/src/App.tsx`, `frontend/src/api/client.ts`, `frontend/src/features/floating-assistant/*`.
- FastAPI route inventory generated from `app.main:app`: 60 routes.
- SQLAlchemy model inventory generated from `Base.metadata`: 17 tables.
- Current active SQLite DB counts inspected without reading secret values.
- Backend tests: `cd backend && python -m pytest -q` -> `190 passed, 112 warnings in 150.17s`.
- Frontend build: `cd frontend && npm run build` -> clean Vite production build.
- Source secret-prefix scan: `rg -n "gsk_|AIza" backend/app frontend/src` -> no matches.

Official hosting references checked for current deployment assumptions:

- Vercel Vite framework docs: https://vercel.com/docs/frameworks/frontend/vite
- Vite environment variable docs: https://vite.dev/guide/env-and-mode/
- Render docs for Python/FastAPI style web services and managed PostgreSQL should be used during actual deployment setup: https://render.com/docs

## Current Deployment Verdict

Best fit for the current architecture:

1. Frontend: Vercel static site from `frontend/`.
2. Backend: Render web service from `backend/`.
3. Database: do not treat any local SQLite file as production storage. Use Render PostgreSQL with the included `psycopg[binary]` dependency, or use a Render persistent disk with SQLite only as a small single-instance stopgap.

Important blocker before public hosting:

The current backend has no application authentication layer. CORS only limits browser calls from other origins; it does not stop direct HTTP clients. Publicly exposing this backend with real Gmail credentials means anyone who can reach the API can attempt settings changes, imports, queue operations, agent chat, confirmation flows, and other state-changing operations. Do not deploy this publicly with real credentials until authentication or network-level access control is added.

PostgreSQL dependency note:

`STACK.md` describes PostgreSQL as a production option, and the SQLAlchemy code can accept `DATABASE_URL`. The public repository now includes `psycopg[binary]` in `backend/requirements.txt`, so the earlier missing-driver finding is superseded. Authentication or private access control is still required before public hosting with real credentials.

## What The Frontend Is

The frontend is a React 18 + TypeScript + Vite single-page operator dashboard for cold-email operations. It is not URL-routed. Navigation is held in React state inside `frontend/src/App.tsx`.

Verified stack:

- Framework: React 18.
- Build tool: Vite 5.
- Language: TypeScript.
- Data fetching/cache: TanStack Query.
- Icons: `lucide-react`.
- Toasts: `sonner`.
- Styling: `frontend/src/styles.css` plus Tailwind/PostCSS config.
- Build command: `npm run build`.
- Dev command: `npm run dev`.
- Frontend API base: `import.meta.env.VITE_API_URL ?? "http://localhost:8000"`.

Frontend surfaces in the current dashboard:

1. Setup
2. Provider Health
3. Import
4. Contacts
5. Drafts
6. Templates
7. Campaigns
8. Queue
9. Follow-ups
10. Replies/Stops
11. Conversations
12. Auto-Reply
13. Suppressions
14. Audit Logs
15. Errors
16. Settings

What the UI does:

- Stores and displays sender readiness and DRY-RUN/CANARY/LIVE mode.
- Lets the operator configure Gmail user/app password, Groq keys, Gemini keys, caps, send windows, sender profile, follow-up templates, and auto-reply settings.
- Previews and commits imports from manual input, paste, CSV, or TXT.
- Manages contacts, tags, soft deletes, restores, notes, personalization, and auto-reply override.
- Creates, edits, generates, approves, bulk-generates, and queues drafts.
- Manages reusable templates.
- Creates campaign plans and activates campaign drafts against matching contacts.
- Processes queue entries and follow-up drafts.
- Lists replies, fetches IMAP replies, archives/restores/deletes reply records.
- Shows conversation threads and generates/sends conversation replies.
- Shows auto-reply pending drafts and logs.
- Manages suppressions.
- Shows audit and error-oriented audit views.

Floating assistant frontend:

- Mounted at the bottom of `App.tsx` as `<AssistantWidget />`.
- Files: `frontend/src/features/floating-assistant/AssistantWidget.tsx`, `assistantApi.ts`, `assistantStore.ts`, `AssistantWidget.css`.
- Uses only `VITE_API_URL` for backend access.
- Stores chat text/history in localStorage keys `va_conversations`, `va_current_id`, `va_ui`, `va_model`.
- Stores current draft input in sessionStorage key `va_draft`.
- Stores session token in sessionStorage key `va_session_token`.
- Does not store file bytes in localStorage. It sends attachment metadata only.
- Model selector values: `auto`, `groq`, `gemini`.
- Confirms sends through `/api/agent/confirm`, cancels through `/api/agent/cancel`.

Frontend deploy settings:

- Vercel root directory: `frontend`.
- Build command: `npm run build`.
- Output directory: `dist`.
- Environment variable: `VITE_API_URL=https://<backend-host>`.
- Do not set Gmail, Groq, Gemini, or Fernet secrets in Vercel frontend variables.

## What The Backend Is

The backend is a FastAPI application under `backend/app`, mounted by `backend/app/main.py`.

Verified stack:

- Framework: FastAPI.
- Server: Uvicorn.
- ORM: SQLAlchemy 2.
- Migration tooling: Alembic.
- Default database: SQLite at `backend/finimatic.db` when run from `backend/`.
- Config source: environment variables plus encrypted settings rows.
- Secrets at rest: Fernet encryption via `backend/app/core/crypto.py`.
- Email send: Gmail SMTP over `smtplib.SMTP_SSL("smtp.gmail.com", 465)`.
- Reply fetch: Gmail IMAP via `imaplib`, with executor usage.
- Background work: in-process async loops plus APScheduler.
- AI providers: Groq SDK and Google GenAI SDK through backend settings keys.

Backend lifecycle:

On startup, `lifespan()` does the following:

1. Configures DB from `DATABASE_URL`, defaulting to `sqlite:///./finimatic.db`.
2. Runs `init_db()`, which calls `Base.metadata.create_all()` and lightweight column migrations.
3. Seeds default settings.
4. Sets `app.state.transport` to fake transport only when `FINIMATIC_TRANSPORT=fake`.
5. Unless `FINIMATIC_DISABLE_SCHEDULER=1`, starts:
   - queue worker every 30 seconds,
   - follow-up worker every 300 seconds,
   - APScheduler IMAP reply fetch at `imap_fetch_interval_minutes`.

Backend modules:

- `settings`: encrypted configuration, mode label, SMTP verification.
- `provider_health`: provider health rows.
- `send`: canary send, queue API, SMTP adapter, queue worker, send policy.
- `imports`: import preview and commit.
- `contacts`: contact list/create/update/delete/restore.
- `drafts`: manual/AI drafts, bulk draft jobs, approval, queue creation, subject variants.
- `templates`: reusable subject/body templates.
- `campaigns`: campaign plan CRUD and activation.
- `followups`: follow-up list/update/process/approve-draft.
- `replies`: manual reply records, IMAP fetch, archive/restore/delete.
- `conversations`: conversation timeline, reply generation, engaged direct send.
- `auto-reply`: pending auto-reply approval/rejection/log.
- `suppressions`: suppression list/create/delete.
- `audit`: audit log reads and redacted event writes.
- `agent`: governed assistant pipeline, bounded DB tools, draft generation, pending confirmation send harness.

Backend deploy settings:

Recommended Render settings from repo root:

- Root directory or working directory: `backend`.
- Build command: `pip install -r requirements.txt`.
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Required env:
  - `FERNET_KEY=<stable generated Fernet key>`
  - `DATABASE_URL=<database URL>`
  - `ALLOWED_ORIGINS=https://<vercel-frontend-domain>`
- Optional env:
  - `FINIMATIC_DISABLE_SCHEDULER=1` only for safe smoke tests where background sends/fetches must not run.
  - `FINIMATIC_TRANSPORT=fake` only for non-production tests.
  - `FINIMATIC_FAKE_AI=1` only for non-production tests.

Critical Fernet rule:

Set `FERNET_KEY` explicitly before production use. If the backend auto-generates `backend/.env` on one host and that file is later lost or replaced, existing encrypted Gmail/Groq/Gemini settings may become unreadable.

## Database Schema

Current SQLAlchemy model table count: 17.

Active local database observed:

- `backend/finimatic.db`: exists, size 2318336 bytes.
- `backend/finimatic_browser.db`: exists but stale/empty relative to the active DB.

Current `backend/finimatic.db` row counts:

| Table | Rows |
|---|---:|
| agent_sessions | 57 |
| audit_events | 5046 |
| campaign_plans | 5 |
| contacts | 86 |
| conversation_messages | 175 |
| drafts | 159 |
| follow_up_sequences | 38 |
| import_batches | 28 |
| import_rows | 95 |
| pending_email_actions | 8 |
| provider_health | 1 |
| replies | 70 |
| send_attempts | 100 |
| send_queue | 50 |
| settings | 35 |
| suppressions | 1 |
| templates | 3 |

Schema inventory from live SQLAlchemy models:

### settings

Stores app settings and encrypted secret values.

- `id` PK
- `key` unique indexed
- `value`
- `updated_at`

Secret settings: `gmail_app_password`, `groq_keys`, `gemini_keys`.

### contacts

Stores leads/prospects and lifecycle state.

- `id` PK
- `email` unique indexed
- `creator_name`
- `business_name`
- `website_url`
- `source`
- `provenance`
- `notes`
- `personalization`
- `lead_category`
- `custom_fields`
- `auto_reply_override`
- `status`
- `import_batch_id` FK to `import_batches.id`
- `deleted_at`
- `created_at`
- `updated_at`

### import_batches

Stores import batch totals.

- `id` PK
- `filename`
- `format`
- `total`
- `accepted`
- `rejected`
- `duplicate`
- `suppressed`
- `created_at`

### import_rows

Stores row-level import outcomes.

- `id` PK
- `batch_id` FK to `import_batches.id`
- `row_num`
- `raw_data`
- `email`
- `status`
- `reason`
- `contact_id` FK to `contacts.id`

### drafts

Stores manual, generated, follow-up, auto-reply, and agent drafts.

- `id` PK
- `contact_id` FK to `contacts.id`
- `subject`
- `body`
- `ai_provider`
- `ai_model`
- `warnings`
- `notes`
- `source`
- `rejected`
- `approved`
- `approved_at`
- `created_at`
- `updated_at`

### templates

Stores reusable templates.

- `id` PK
- `name` unique indexed
- `subject_template`
- `body_template`
- `created_at`

### send_queue

Stores queued cold-send and follow-up jobs.

- `id` PK
- `contact_id` FK to `contacts.id`
- `draft_id` FK to `drafts.id`
- `sequence_num`
- `scheduled_at`
- `status`
- `idempotency_key` unique indexed
- `policy_block_reasons`
- `created_at`
- Unique constraint: `(contact_id, sequence_num)`

### send_attempts

Stores send attempts and outcomes.

- `id` PK
- `queue_id`
- `contact_id`
- `draft_id`
- `idempotency_key` indexed
- `provider_msg_id`
- `smtp_response`
- `status`
- `sender_identity`
- `sent_at`
- `error_code`
- `error_detail`

### follow_up_sequences

Stores follow-up schedules and pending follow-up draft references.

- `id` PK
- `contact_id` FK to `contacts.id`
- `sequence_num`
- `due_at`
- `draft_id` FK to `drafts.id`
- `pending_draft_id` FK to `drafts.id`
- `status`
- `stop_reason`
- `created_at`
- Unique constraint: `(contact_id, sequence_num)`

### suppressions

Stores manual/imported unsubscribe/bounce/suppression blocks.

- `id` PK
- `email` unique indexed
- `reason`
- `source`
- `created_at`

### replies

Stores bounded reply records and classification metadata.

- `id` PK
- `contact_id` FK to `contacts.id`
- `received_at`
- `classified_as`
- `intent`
- `raw_summary`
- `external_message_id` indexed
- `archived_at`
- `created_at`

### conversation_messages

Stores per-contact inbound/outbound timeline.

- `id` PK
- `contact_id` FK to `contacts.id`, indexed
- `direction`
- `subject`
- `body`
- `source`
- `auto_sent`
- `external_message_id` indexed
- `occurred_at`
- `created_at`

### audit_events

Stores redacted event history.

- `id` PK
- `event_type` indexed
- `entity_type`
- `entity_id`
- `actor`
- `payload`
- `created_at`

### provider_health

Stores provider health state.

- `id` PK
- `provider` unique
- `status`
- `last_checked`
- `error_code`
- `details`

### campaign_plans

Stores multi-step campaign plans and activation counts.

- `id` PK
- `name`
- `goal`
- `target_tags`
- `step_1_draft`
- `step_2_draft`
- `step_3_draft`
- `status`
- `contacts_count`
- `sent_count`
- `stopped_count`
- `created_at`
- `updated_at`

### agent_sessions

Stores assistant session state. Raw session token is not stored.

- `id` PK
- `session_token_hash` unique indexed
- `current_goal`
- `slots`
- `active_contact_id` FK to `contacts.id`
- `pending_action_id`
- `context_summary`
- `context_loaded_at`
- `contact_name_map`
- `turn_history`
- `current_channel`
- `created_at`
- `updated_at`
- `expires_at`

### pending_email_actions

Stores send confirmations for agent-generated drafts.

- `id` PK
- `session_id` FK to `agent_sessions.id`, indexed
- `action_type`
- `capability`
- `draft_id` FK to `drafts.id`
- `contact_id` FK to `contacts.id`
- `params_hash`
- `source_label`
- `confirmation_prompt`
- `expires_at`
- `consumed`
- `consumed_at`
- `created_at`

## Migrations

Current migration files:

- `0001_initial.py`: creates all current SQLAlchemy metadata tables.
- `0002_agent_tables.py`: idempotently adds `agent_sessions` and `pending_email_actions` if missing.
- `0003_reply_followup_campaigns.py`: adds reply intent, draft notes, follow-up pending draft id, and campaign plans.

Important production note:

The runtime startup path still calls `Base.metadata.create_all()` plus lightweight column migrations. Alembic exists and should be used for production migrations, but the app is also self-creating tables at runtime. This is convenient for local/small deploys and less ideal for controlled production schema management.

## API Inventory

FastAPI route count from live app: 60.

### Health

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/api/health` | none | Returns `{"status":"ok"}`. |

### Settings And Provider Health

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/api/settings` | none | Return safe settings view, counts/fingerprints for keys, no raw secrets. |
| POST | `/api/settings` | `SettingsWrite` dynamic dict | Save settings, encrypt Gmail/Groq/Gemini secrets. |
| POST | `/api/settings/verify-smtp` | none | Verify Gmail SMTP credentials. |
| GET | `/api/provider-health` | none | List provider health rows. |

### Canary

| Method | Path | Body | Purpose |
|---|---|---|---|
| POST | `/api/canary/send` | none | Send one canary/test email and set canary readiness on success. |

### Import

| Method | Path | Body | Purpose |
|---|---|---|---|
| POST | `/api/import/preview` | `ImportPreviewRequest` | Parse/validate manual, paste, CSV, or TXT input without committing contacts. |
| POST | `/api/import/commit` | `ImportCommitRequest` | Commit accepted preview rows to contacts/import tables. |

### Contacts

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/api/contacts` | none | List active contacts. |
| GET | `/api/contacts/recently-deleted` | none | List soft-deleted contacts. |
| POST | `/api/contacts` | `ContactCreate` | Create one contact. |
| PATCH | `/api/contacts/{contact_id}` | `ContactPatch` | Update status, notes, personalization, auto-reply override. |
| DELETE | `/api/contacts/{contact_id}` | none | Soft-delete a contact. |
| POST | `/api/contacts/{contact_id}/restore` | none | Restore a soft-deleted contact. |

### Conversations

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/api/conversations` | none | List per-contact conversation summaries. |
| GET | `/api/conversations/{contact_id}` | none | Read one contact's conversation timeline. |
| POST | `/api/conversations/{contact_id}/generate-reply` | `ConversationGenerate` | Generate a context-aware reply draft. |
| POST | `/api/conversations/{contact_id}/send` | `ConversationSend` | Send a direct engaged conversation reply through Gmail after policy gates. |

### Auto-Reply

| Method | Path | Body | Purpose |
|---|---|---|---|
| POST | `/api/auto-reply/approve/{draft_id}` | none | Approve/send an auto-reply draft. |
| POST | `/api/auto-reply/reject/{draft_id}` | none | Reject an auto-reply draft. |
| GET | `/api/auto-reply/pending` | none | List pending auto-reply drafts. |
| GET | `/api/auto-reply/log` | none | List auto-reply related audit entries. |

### Drafts

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/api/drafts` | none | List drafts. |
| POST | `/api/drafts` | `DraftCreate` | Create manual draft. |
| PATCH | `/api/drafts/{draft_id}` | `DraftPatch` | Edit draft subject/body/warnings. |
| POST | `/api/drafts/generate` | `DraftGenerate` | Generate one AI/manual draft. |
| POST | `/api/drafts/generate-bulk` | `BulkDraftGenerate` | Start in-memory bulk generation job. |
| GET | `/api/drafts/bulk-status/{job_id}` | none | Read bulk job status. |
| POST | `/api/drafts/approve-bulk` | `BulkApprove` | Approve selected drafts and queue them. |
| POST | `/api/drafts/{draft_id}/subject-variants` | none | Generate subject variants. |
| POST | `/api/drafts/{draft_id}/approve` | `DraftApprove` | Approve a draft and create queue entry. |

### Templates

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/api/templates` | none | List templates. |
| POST | `/api/templates` | `TemplateCreate` | Create template or derive from approved draft. |

### Campaigns

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/api/campaigns` | none | List campaign plans. |
| POST | `/api/campaigns` | `CampaignCreate` | Create a campaign plan. |
| GET | `/api/campaigns/{campaign_id}` | none | Read one campaign. |
| PATCH | `/api/campaigns/{campaign_id}` | `CampaignPatch` | Update campaign fields/steps/status. |
| POST | `/api/campaigns/{campaign_id}/activate` | none | Create drafts for matching contacts. |

### Queue

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/api/queue` | none | List queue entries. |
| POST | `/api/queue` | `QueueCreate` | Create queue entry manually. |
| GET | `/api/queue/{queue_id}` | none | Read one queue entry. |
| POST | `/api/queue/process` | none | Process due queue entries now. |

### Follow-Ups

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/api/followups` | none | List follow-up sequence rows. |
| GET | `/api/followups/{sequence_id}` | none | Read one follow-up row. |
| PATCH | `/api/followups/{sequence_id}` | `FollowUpPatch` | Update due/status/stop reason. |
| POST | `/api/followups/{sequence_id}/approve-draft` | none | Approve pending follow-up draft and queue it. |
| POST | `/api/followups/process` | none | Process due follow-ups now. |

### Suppressions

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/api/suppressions` | none | List suppressions. |
| POST | `/api/suppressions` | `SuppressionCreate` | Add suppression. |
| DELETE | `/api/suppressions/{suppression_id}` | none | Delete suppression. |

### Replies

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/api/replies` | query params | List replies with filters: `include_archived`, `archived_only`, `contact_id`, `classified_as`. |
| POST | `/api/replies` | `ReplyCreate` | Create manual reply/stop record. |
| POST | `/api/replies/fetch` | none | Fetch and classify Gmail IMAP replies. |
| POST | `/api/replies/{reply_id}/archive` | none | Archive reply. |
| POST | `/api/replies/{reply_id}/restore` | none | Restore archived reply. |
| DELETE | `/api/replies/{reply_id}` | none | Delete reply. |

### Audit

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/api/audit` | none | List audit events. |

### Governed Agent Assistant

| Method | Path | Body | Purpose |
|---|---|---|---|
| POST | `/api/agent/chat` | `AgentChatRequest` | Run assistant turn: awareness, read, draft, queue/follow-up, or clarification. |
| POST | `/api/agent/confirm` | `AgentConfirmRequest` | Confirm a pending send action. Returns HTTP 409 for invalid confirmations. |
| DELETE | `/api/agent/cancel` | `AgentCancelRequest` | Consume/cancel pending action and clear agent session state. |

## Request Body Schema Summary

Schemas from generated OpenAPI:

| Schema | Required fields | Important optional fields |
|---|---|---|
| `AgentChatRequest` | `session_token`, `message` | `provider`, `attachments` |
| `AgentConfirmRequest` | `session_token`, `action_id` | none |
| `AgentCancelRequest` | `session_token` | none |
| `ContactCreate` | `email` | `creator_name`, `business_name`, `website_url`, `source`, `provenance`, `notes`, `personalization`, `lead_category`, `tags` |
| `ContactPatch` | none | `status`, `notes`, `personalization`, `auto_reply_override` |
| `DraftCreate` | `contact_id` | `subject`, `body`, `warnings` |
| `DraftGenerate` | `contact_id` | `provider`, `tone`, `length`, `instruction` |
| `DraftPatch` | none | `subject`, `body`, `warnings` |
| `DraftApprove` | none | `sequence_num` |
| `BulkDraftGenerate` | `contact_ids` | `provider`, `tone` |
| `BulkApprove` | `draft_ids` | none |
| `ConversationGenerate` | none | `provider`, `instruction`, `language` |
| `ConversationSend` | `subject`, `body` | none |
| `ImportPreviewRequest` | none | `format`, `rows`, `content`, `filename` |
| `ImportCommitRequest` | none | `batch_id_temp`, `rows`, `format`, `filename` |
| `ReplyCreate` | `contact_id`, `classified_as` | `raw_summary`, `intent` |
| `SuppressionCreate` | `email` | `reason`, `source` |
| `TemplateCreate` | `name` | `subject_template`, `body_template`, `draft_id` |
| `CampaignCreate` | `name`, `goal` | `target_tags`, `sender_name`, `sender_role`, `sender_offer` |
| `CampaignPatch` | none | `name`, `goal`, `target_tags`, `step_1_draft`, `step_2_draft`, `step_3_draft`, `status` |
| `QueueCreate` | `contact_id`, `draft_id` | `sequence_num` |
| `FollowUpPatch` | none | `due_at`, `status`, `stop_reason` |

## Key Runtime Flows

### Normal cold-email flow

1. Operator saves settings.
2. Backend encrypts secrets in `settings`.
3. Operator verifies SMTP.
4. Operator sends canary.
5. Operator imports contacts.
6. Operator generates or writes drafts.
7. Operator approves drafts.
8. Approval creates `send_queue` rows.
9. Queue worker evaluates policy gates.
10. In DRY-RUN, sends are skipped/blocked safely.
11. In LIVE, Gmail sends through SMTP.
12. Successful send writes `send_attempts`, `conversation_messages`, audit events, contact state, and follow-up schedule.
13. Replies are manually added or fetched through IMAP.
14. Replies update contact lifecycle and stop follow-ups.

### Agent draft and send flow

1. `POST /api/agent/chat` loads or creates hashed session.
2. Assistant routes awareness/task/action.
3. Capability catalog denies unknown capabilities.
4. Slots are extracted; missing contact/detail returns clarification.
5. Read tools return bounded/redacted data.
6. `email_generate_draft` creates an unapproved draft.
7. Backend creates `pending_email_actions` row with 180 second TTL.
8. Widget renders confirmation card.
9. `POST /api/agent/confirm` validates same session, not expired, not consumed, matching draft id, matching current params hash.
10. Send is executed only after `claim_pending_action()` consumes the action.
11. Send path emits `agent.confirmation_valid`, `send.attempt`, `agent.send_executed` or failure events.

### Policy gates

Queue sends check:

- sender verified
- canary verified
- draft approved
- contact not deleted
- no suppression or blocked domain
- no bounce/complaint
- no active reply/unsubscribe where applicable
- not manually paused
- daily cap
- hourly cap
- send window
- send delay
- idempotency duplicate

Agent engaged sends check a similar subset:

- canary verified
- sender configured
- dry run disabled
- contact not deleted
- no suppression/domain block
- not unsubscribed
- no bounce/complaint
- not manually paused
- daily/hourly cap
- send window

## Hosting Readiness Checklist

Must do before public production:

- Add authentication or restrict the backend to trusted users/networks. This is the main blocker.
- Decide DB strategy:
  - Preferred: PostgreSQL on Render using the included `psycopg[binary]` dependency.
  - Stopgap: SQLite on a persistent disk with exactly one backend instance.
- Set a durable `FERNET_KEY`.
- Set `ALLOWED_ORIGINS` to the exact frontend domain.
- Set frontend `VITE_API_URL` to the backend HTTPS URL.
- Keep `dry_run=true` until SMTP, canary, queue, agent confirmation, and audit are verified on the hosted stack.
- Do not run multiple backend instances unless queue/follow-up workers are externalized or made distributed-lock safe.
- Do not place Gmail, Groq, Gemini, or Fernet secrets in frontend env vars.
- Do not commit `backend/.env`, DB files, logs, or `KEYS.md`.
- Commit and push the repository before Git-based Vercel/Render deploy.

Useful smoke tests after deployment:

1. `GET https://<backend>/api/health` returns `{"status":"ok"}`.
2. Vercel frontend loads and calls backend without CORS errors.
3. `GET /api/settings` shows safe booleans/counts/fingerprints only.
4. Save settings with DRY-RUN enabled.
5. Verify SMTP.
6. Send canary.
7. Import one test contact.
8. Generate draft.
9. Approve draft and process queue in DRY-RUN.
10. Disable DRY-RUN only after canary and policy behavior are proven.
11. Test agent draft generation and confirm/cancel behavior with a disposable recipient.
12. Verify audit events for every state-changing step.

## What Is Not Yet Deployment-Grade

These are source-backed risks, not speculation:

- No auth layer protects public API routes.
- PostgreSQL production path has a driver dependency in `backend/requirements.txt`; production database configuration still needs to be tested on the target host.
- Runtime schema creation is active; production migrations are not the sole schema control path.
- Background workers run inside the web process. Multiple backend replicas could duplicate queue/follow-up work.
- Bulk draft job state is in process memory, so jobs will be lost on restart.
- SQLite local DB files are ignored and should not be treated as portable production state.
- Git-based hosting workflows require the public repository to stay pushed and current.

## Clean Verification Results

Backend:

```text
cd backend && python -m pytest -q
190 passed, 112 warnings in 150.17s (0:02:30)
```

Warnings are deprecation warnings around `datetime.utcnow()`, not test failures.

Frontend:

```text
cd frontend && npm run build
tsc && vite build
1553 modules transformed
dist/index.html
dist/assets/index-wKirdkBP.css
dist/assets/index-DOd9DrJr.js
built successfully
```

Secret prefix source scan:

```text
rg -n "gsk_|AIza" backend/app frontend/src
no matches
```

## Bottom Line

This is a real full-stack app:

- Frontend: Vite React dashboard plus floating assistant.
- Backend: FastAPI email-ops system with settings, import, contacts, drafts, queue, follow-ups, replies, conversations, campaigns, auto-reply, audit, and governed assistant APIs.
- Database: 17-table SQLAlchemy schema, currently active in SQLite, with Alembic files present.
- APIs: 60 FastAPI routes.

It is build/test clean locally. It is not safe to put on the open internet with real credentials until the authentication/access-control gap is closed.
