# Finimatic Functionality And Feature Map For Local LLM Review

Generated: 2026-05-26 16:21:02 +05:30

Purpose: this is the single bridge document a local LLM should read first when trying to understand what Finimatic does, how every feature relates to the others, and where conflicting changes can cause broken behavior. It complements:

- `architecture_test.md` for architecture and verification evidence.
- `files34.md` for file/function inventory.
- `relation.md` for the detailed conflict ledger.

Treat live source as current truth. Treat older Markdown reports as historical/spec evidence unless this file says they match current source.

## Current Product In One Paragraph

Finimatic is a local cold-email operations dashboard. It imports leads, stores contact state, generates AI-assisted drafts, approves drafts into a send queue, sends Gmail messages after canary/policy gates, tracks follow-ups, fetches and classifies replies, builds conversation threads, supports guarded conversation replies, supports proposed or autonomous auto-replies, manages suppression records, records audit events, and includes a floating assistant that can read campaign state, generate drafts, and send only after a pending-confirmation harness validates a user click.

## Current Browser Context

The in-app browser is open at `http://localhost:5173/`. The visible surface is the Drafts dashboard, with a toast saying `draft saved, approved, and queued`. The assistant launcher is visible at bottom-right. This matters because the currently visible workflow is:

```text
Drafts page
  -> Save / Approve draft
  -> Draft becomes approved
  -> Queue entry is created
  -> Queue processing can send in LIVE mode if policy gates pass
```

The runtime was previously verified as `LIVE`, `canary_verified`, and `dry_run=false`. Avoid live send probes unless explicitly intended.

## Source Of Truth Priority

Use this order when sources disagree:

1. Live source under `backend/app` and `frontend/src`.
2. Current tests under `backend/tests`.
3. Active DB schema from `backend/app/db/models.py` plus startup migrations in `backend/app/db/session.py`.
4. Alembic migrations, with the known caveat that they are incomplete.
5. Root Markdown docs, which are partially stale.
6. Historical logs/screenshots, which may show real prior failures but not current truth.

## Feature Dependency Graph

```text
Settings
  -> enables Gmail SMTP, IMAP, Groq, Gemini, caps, send window, auto-reply mode
  -> used by Drafts, Queue, Replies, Conversations, Auto-Reply, Agent

Imports
  -> creates Contacts
  -> contacts feed Drafts, Campaigns, Queue, Follow-ups, Replies, Conversations, Agent

Drafts
  -> may be manual, AI generated, follow-up generated, auto-reply proposed, or agent generated
  -> approval creates Queue entries for cold outbound
  -> agent-generated drafts create Pending Email Actions instead of sending immediately

Queue
  -> evaluates canonical cold-send policy
  -> sends Gmail messages
  -> writes SendAttempts and outbound ConversationMessages
  -> schedules Follow-ups

Replies / IMAP
  -> creates Reply records
  -> updates Contact status
  -> stops Follow-ups
  -> may create Suppressions
  -> writes inbound ConversationMessages
  -> may trigger Auto-Reply

Conversations
  -> backfills from SendAttempts, Drafts, Replies
  -> generates engaged replies
  -> can send direct Gmail replies outside Queue

Auto-Reply
  -> reads Replies, Contacts, Settings, Conversation context
  -> can propose Drafts
  -> can autonomously send in enabled mode

Agent
  -> reads Contacts, Replies, Conversations, Queue, Follow-ups
  -> can generate Drafts
  -> creates Pending Email Actions for send confirmation
  -> confirmed send writes SendAttempts and ConversationMessages

Frontend
  -> single App.tsx dashboard controls all feature surfaces
  -> AssistantWidget talks to /api/agent/*
```

## Feature Map

### 1. Settings And Credentials

Primary files:

- `backend/app/settings/router.py`
- `backend/app/settings/service.py`
- `backend/app/core/crypto.py`
- `frontend/src/App.tsx` `SettingsPanel`
- `frontend/src/api/client.ts`

What it does:

- Stores Gmail user/app password, Groq keys, Gemini keys, caps, send window, dry-run/canary state, IMAP interval, auto-reply settings, and sender profile.
- Encrypts secrets using Fernet.
- Returns only configured flags, counts, and fingerprints.

Relations:

- Draft generation needs Groq/Gemini keys.
- SMTP send paths need Gmail user/app password.
- IMAP fetch needs Gmail credentials.
- Send policy needs caps, dry-run, canary, and send window.
- Auto-reply needs mode, caps, safety config, and gap settings.

Conflict checks:

- Confirm all secret fields clear from frontend state after every successful save/verify path.
- Confirm no frontend code uses Groq/Gemini env vars beyond `VITE_API_URL`.
- Confirm `backend/.env` auto-generation in `core/crypto.py` is acceptable for local secret handling.

### 2. Imports And Contacts

Primary files:

- `backend/app/imports/router.py`
- `backend/app/imports/service.py`
- `backend/app/contacts/router.py`
- `backend/app/contacts/utils.py`
- `frontend/src/App.tsx` `ImportPanel`, `ContactsPanel`

What it does:

- Parses CSV/text rows.
- Normalizes email/contact fields.
- Checks duplicate and suppression state.
- Stores contacts with tags, personalization, source, status, soft-delete state, and auto-reply override.

Relations:

- Contacts are the central join point for Drafts, Queue, Follow-ups, Replies, Conversations, Campaigns, Auto-Reply, and Agent tools.
- Soft deletion cancels pending queue/follow-ups in the contacts route, but every direct-send path must still check deleted status.

Conflict checks:

- Import preview is process-local memory; backend restart can lose preview batch state.
- Re-check whether commit repeats enough validation if suppressions or contacts changed after preview.
- Personalization can later enter AI prompts and agent evidence; verify bounding/redaction.

### 3. Drafts, Templates, And AI Generation

Primary files:

- `backend/app/drafts/router.py`
- `backend/app/templates/router.py`
- `backend/app/ai/gateway.py`
- `backend/app/ai/prompts.py`
- `frontend/src/App.tsx` `DraftsPanel`, `TemplatesPanel`

What it does:

- Creates manual drafts.
- Generates AI drafts from contact/sender/campaign context.
- Runs subject variants.
- Saves approved drafts as templates.
- Bulk-generates drafts.
- Approves drafts into the send queue.

Relations:

- Draft approval creates `send_queue` entries.
- Follow-up and auto-reply can create draft rows.
- Agent draft generation creates draft rows and pending actions.
- Templates depend on approved drafts.

Conflict checks:

- Bulk draft jobs use in-memory global job state; restart loses progress.
- Confirm rejected/deleted-contact drafts cannot be approved through any route.
- Compare AI key rotation in normal draft generation vs first-key helper paths.

### 4. Queue And Cold Outbound Sends

Primary files:

- `backend/app/send/policy.py`
- `backend/app/send/queue_worker.py`
- `backend/app/send/router.py`
- `backend/app/send/canary_router.py`
- `backend/app/send/smtp_adapter.py`
- `frontend/src/App.tsx` `QueuePanel`

What it does:

- Sends approved drafts through a queue.
- Enforces canonical cold-send policy gates.
- Writes send attempts and conversation messages.
- Schedules follow-ups after successful cold sends.

Canonical policy gate categories:

- sender configured
- canary verified
- draft approved
- contact not deleted
- suppression/domain block
- bounce/unsubscribe/reply/no active reply
- manual pause
- daily/hourly caps
- send window
- idempotency duplicate

Relations:

- Queue depends on Drafts, Contacts, Settings, Suppressions.
- Queue writes SendAttempts and ConversationMessages.
- Queue schedules FollowUpSequences.

Conflict checks:

- Queue policy is stricter than direct conversation/agent send policies.
- Decide which gates are universal hard gates and which are cold-only gates.
- In LIVE mode, queue processing is a real send path.

### 5. Follow-Ups

Primary files:

- `backend/app/followups/router.py`
- `backend/app/followups/service.py`
- `frontend/src/App.tsx` `FollowupsPanel`

What it does:

- Tracks follow-up sequences after successful sends.
- Processes due follow-ups.
- Proposes follow-up drafts.
- Allows approval of pending follow-up drafts into the queue.

Relations:

- Created by queue success.
- Stopped by replies, suppressions, negative/hostile/unsubscribe intent, or contact state.
- Can create drafts and queue entries.

Conflict checks:

- Ensure reply archive/delete/restore consistently refreshes follow-up state.
- Ensure contact delete cancels due follow-ups and pending queue.
- Ensure follow-up approval cannot bypass suppression or deleted-contact checks.

### 6. Replies, IMAP, And Suppressions

Primary files:

- `backend/app/replies/router.py`
- `backend/app/replies/service.py`
- `backend/app/replies/imap_fetcher.py`
- `backend/app/suppressions/router.py`
- `backend/app/provider_health/router.py`
- `frontend/src/App.tsx` `RepliesPanel`, `SuppressionsPanel`, `HealthPanel`

What it does:

- Fetches or manually creates replies.
- Classifies reply intent.
- Stops follow-ups.
- Creates suppressions for unsubscribe/hostile negative cases.
- Writes inbound conversation messages.
- Updates provider health.

Relations:

- Replies affect Contacts, Follow-ups, Suppressions, ConversationMessages, Auto-Reply, Agent answers.
- Provider health tells the UI whether IMAP is functioning.

Conflict checks:

- Current provider health has shown IMAP `failed` / `TimeoutError`.
- IMAP fetch is synchronous in request/scheduler paths even though the project instruction says IMAP should run in executor.
- Verify duplicate external message id mapping across contacts.

### 7. Conversations

Primary files:

- `backend/app/conversations/router.py`
- `frontend/src/App.tsx` `ConversationsPanel`

What it does:

- Builds thread summaries.
- Backfills conversation messages from sends/drafts/replies.
- Generates engaged reply drafts.
- Sends direct engaged replies through Gmail.

Relations:

- Reads Contacts, Drafts, Replies, SendAttempts, Settings, ProviderHealth.
- Writes ConversationMessages, SendAttempts, Contact status, AuditEvents.
- Used by Auto-Reply and Agent as evidence.

Conflict checks:

- GET routes can backfill and commit DB changes; read-only page load is not fully read-only.
- Conversation direct-send gates duplicate agent direct-send gates and differ from queue cold-send policy.
- Conversation sends should be intentionally documented as engaged replies, not cold queue sends.

### 8. Auto-Reply

Primary files:

- `backend/app/conversations/auto_reply_service.py`
- `backend/app/conversations/auto_reply_router.py`
- `frontend/src/App.tsx` `AutoReplyPanel`

What it does:

- Evaluates inbound replies for auto-reply eligibility.
- Can store proposed drafts.
- Can autonomously send replies if settings and safety gates allow it.
- Supports approve/reject from UI.

Relations:

- Triggered by replies.
- Reads settings, contact state, suppressions, conversation context.
- Writes drafts, send attempts, conversation messages, audit.

Conflict checks:

- Older docs say AI is suggestion-only; current source has autonomous direct sends.
- Confirm global mode, contact override, canary, daily cap, gap, unsafe intent, suppression, and quality gates run before send.
- Confirm UI warning/consent is strong enough for autonomous sending.

### 9. Campaigns

Primary files:

- `backend/app/campaigns/router.py`
- `backend/app/agent/campaign_intelligence.py`
- `frontend/src/App.tsx` `CampaignsPanel`

What it does:

- Creates and updates campaign plans.
- Suggests campaign steps with AI.
- Activates matching contacts.
- Feeds awareness/campaign-intelligence answers in the assistant.

Relations:

- Reads contacts and tags.
- Writes campaign plans and may update contact workflow state.
- Agent awareness can summarize campaign state.

Conflict checks:

- Campaign features are newer than several historical docs.
- Confirm activation cannot override suppressed/deleted contacts.
- Confirm campaign intelligence does not expose private segmentation unnecessarily.

### 10. Floating Assistant And Agent Backend

Primary frontend files:

- `frontend/src/features/floating-assistant/AssistantWidget.tsx`
- `frontend/src/features/floating-assistant/assistantApi.ts`
- `frontend/src/features/floating-assistant/assistantStore.ts`
- `frontend/src/features/floating-assistant/AssistantWidget.css`

Primary backend files:

- `backend/app/agent/router.py`
- `backend/app/agent/service.py`
- `backend/app/agent/schemas.py`
- `backend/app/agent/catalog.py`
- `backend/app/agent/tools.py`
- `backend/app/agent/pending.py`
- `backend/app/agent/memory.py`
- `backend/app/agent/channel_router.py`
- `backend/app/agent/campaign_intelligence.py`

What it does:

- Gives user a floating assistant over the dashboard.
- Answers awareness questions from local DB evidence.
- Resolves contacts.
- Reads inbox/thread/queue/follow-up state.
- Generates email drafts.
- Creates confirmation cards for send actions.
- Sends only through `/api/agent/confirm` after pending action validation.

Agent chat path:

```text
AssistantWidget
  -> assistantApi.chat()
  -> POST /api/agent/chat
  -> AgentService.chat()
  -> AgentSession by hashed session token
  -> channel classification
  -> awareness answer OR governed tool path
  -> tool execution over bounded DB evidence
  -> optional pending action
  -> response + audit
```

Agent send path:

```text
email_generate_draft
  -> Draft row
  -> PendingEmailAction row with 180 second TTL
  -> frontend confirmation card
  -> POST /api/agent/confirm
  -> validate action_id + session + draft hash
  -> send through GmailAdapter
  -> consume action
  -> SendAttempt + ConversationMessage + audit
```

Relations:

- Reads almost every operational feature.
- Writes sessions, pending actions, drafts, send attempts, conversation messages, audit.
- Uses settings-backed provider keys, not frontend keys.

Conflict checks:

- Runtime pipeline is mostly deterministic/rule-based, while spec describes Groq-backed agent stages.
- `CAPABILITY_CATALOG` exact action list coexists with broader `CAPABILITY_TIERS`.
- Awareness route can answer some unsupported-looking questions instead of strict denial.
- `contact_resolve` evidence is wider than the original contract.
- Assistant accepts attachments but sends only metadata.
- LocalStorage stores pending action metadata, though draft body is stripped.

## Shared Tables And State Meanings

| Table | Main Owner | Meaning | Used By |
|---|---|---|---|
| `settings` | Settings | Encrypted credentials and operational config. | Almost every backend feature. |
| `contacts` | Imports/Contacts | Lead identity, status, tags, personalization, delete/override state. | Drafts, Queue, Replies, Conversations, Auto-Reply, Agent, Campaigns. |
| `drafts` | Drafts/AI/Followups/Auto-Reply/Agent | Email copy before or after approval. | Queue, Templates, Auto-Reply, Agent. |
| `send_queue` | Queue | Pending/blocked/sent cold outbound work. | Queue UI, policy, worker, Agent queue status. |
| `send_attempts` | Send paths | Evidence of attempted or successful sends. | Conversations, audit review, idempotency. |
| `follow_up_sequences` | Queue/Followups | Follow-up state after a send. | Followups, Replies, Agent. |
| `replies` | Replies/IMAP | Inbound reply records and intent. | Followups, Conversations, Auto-Reply, Agent. |
| `conversation_messages` | Queue/Replies/Conversations/Auto-Reply/Agent | Thread timeline. | Conversations, Agent, Auto-Reply. |
| `suppressions` | Replies/Suppressions | Do-not-contact records. | Policy, Imports, Followups, Conversations, Auto-Reply, Agent. |
| `audit_events` | All modules | Redacted event trail. | Audit and Errors surfaces. |
| `provider_health` | Provider checks | SMTP/IMAP/provider status. | Health and Conversations UI. |
| `campaign_plans` | Campaigns | Campaign plan and step state. | Campaigns UI, Agent awareness. |
| `agent_sessions` | Agent | Assistant session context and pending pointer. | Agent service. |
| `pending_email_actions` | Agent | One-time confirmation records for side effects. | Agent confirm/cancel/send. |

## Most Likely Breakage Zones

1. Send policy divergence
   - Queue, conversations, auto-reply, and agent all have send paths.
   - Not every path shares one policy function.
   - Risk: a contact blocked in one path can still send in another.

2. Schema drift
   - Runtime `create_all` and lightweight migrations carry truth not encoded in Alembic.
   - Risk: fresh Alembic DB breaks in production/deploy.

3. Stale docs
   - Older docs do not include agent, campaigns, current auto-reply, or current schema.
   - Risk: future fixes are implemented against false architecture.

4. Read routes with writes
   - Conversation GET routes can backfill and commit.
   - Risk: browser load, monitoring, or prefetch can mutate DB.

5. Assistant scope expansion
   - Strict catalog plus broader tiered awareness behavior.
   - Risk: unsupported requests may appear supported or route unexpectedly.

6. Attachment UX mismatch
   - UI accepts files, backend receives metadata only.
   - Risk: user thinks assistant reviewed file content when it did not.

7. Autonomous send consent
   - Auto-reply can send without per-message approval when enabled.
   - Risk: conflicts with older "AI suggestion only" mental model.

8. IMAP health and blocking
   - IMAP has shown timeout failures and synchronous execution.
   - Risk: reply/follow-up/auto-reply state becomes stale.

9. Local secret hygiene
   - `KEYS.md` exists locally and should not be fed to local LLMs.
   - Risk: accidental local model ingestion or copy/paste leak.

10. Single large frontend file
   - `App.tsx` owns all panels and many cross-feature mutations.
   - Risk: invalidation/state changes in one surface break another.

## Local LLM Review Instructions

Use this exact analysis procedure:

1. Read this file first.
2. Read `relation.md` next for conflict details.
3. Read `files34.md` to locate source files and functions.
4. Read `architecture_test.md` for verification evidence and runtime facts.
5. Do not trust `SCHEMA.md`, `PROJECT_IMPLEMENTATION_REPORT.md`, or `DATA_FLOW.md` unless the live source confirms them.
6. Build a table of every side-effecting path:
   - creates contacts
   - creates drafts
   - queues email
   - sends email
   - fetches IMAP
   - creates suppression
   - updates contact status
   - writes audit
7. Compare all side-effecting paths against shared safety gates.
8. For every suspected issue, output:
   - severity
   - feature
   - source files/functions
   - current behavior
   - expected behavior
   - reproduction idea
   - safest fix
   - tests to add or update

## Source Hotspots To Inspect First

- `backend/app/send/policy.py`
- `backend/app/agent/tools.py`
- `backend/app/conversations/router.py`
- `backend/app/conversations/auto_reply_service.py`
- `backend/app/db/models.py`
- `backend/app/db/session.py`
- `backend/app/db/migrations/versions/0001_initial.py`
- `backend/app/db/migrations/versions/0002_agent_tables.py`
- `backend/app/db/migrations/versions/0003_reply_followup_campaigns.py`
- `backend/app/replies/imap_fetcher.py`
- `backend/app/replies/service.py`
- `backend/app/agent/service.py`
- `backend/app/agent/catalog.py`
- `frontend/src/App.tsx`
- `frontend/src/features/floating-assistant/AssistantWidget.tsx`
- `frontend/src/features/floating-assistant/assistantStore.ts`
- `frontend/src/features/floating-assistant/assistantApi.ts`

## Verification To Run Before Trusting A Fix

Minimum:

```powershell
cd C:\Users\rossd\OneDrive\Documents\notes\email\backend
python -m pytest

cd C:\Users\rossd\OneDrive\Documents\notes\email\frontend
npm run build
```

Targeted checks for likely fixes:

- Send policy fix: add tests covering queue, conversation send, auto-reply send, and agent confirmed send.
- Schema fix: create a fresh DB from Alembic and compare columns to `models.py`.
- Assistant fix: browser-test localStorage/sessionStorage and confirmation card behavior.
- IMAP fix: test fetch lock, timeout behavior, provider health update, and no event-loop blocking.
- Attachment fix: test whether file content is processed or clearly rejected.

## Non-Negotiable Safety Rules For Future Changes

- Do not add Groq/Gemini keys to frontend env vars.
- Do not send email from chat text alone; require confirm button or explicit existing non-agent send workflow.
- Do not bypass canary/readiness gates for any Gmail send.
- Do not let deleted, suppressed, unsubscribed, bounced, or manually paused contacts send through accidental alternate paths.
- Do not feed `KEYS.md`, `.env`, raw DB secrets, or raw provider keys to a local LLM.
- Do not treat old docs as current schema or route truth.
- Do not make GET routes perform new side effects without explicit documentation and tests.

