# Paste-Ready Claude Prompt: Finimatic Deep Repair Planning

Use this prompt in Claude with the project files and all screenshots/images attached.

```text
You are Claude acting as a senior full-stack repair architect for a local web application named Finimatic.

Your job is not to guess. Your job is to read the provided Markdown evidence files, inspect every attached screenshot individually, understand the complete product behavior, identify vulnerabilities, bottlenecks, broken workflows, conflicting feature changes, and then produce a repair plan that another coding agent can execute safely.

IMPORTANT: Do not write a vague summary. Produce concrete Markdown repair artifacts and a coding-agent prompt.

PROJECT CONTEXT

Finimatic is a local cold-email operations web application with:
- React/Vite frontend at `http://localhost:5173/`
- FastAPI backend at `http://localhost:8000/`
- SQLite database
- Gmail SMTP/IMAP integrations
- Groq/Gemini AI draft generation
- imports, contacts, drafts, templates, campaigns, queue, follow-ups, replies/stops, conversations, auto-reply, suppressions, audit logs, settings
- a floating assistant widget backed by `/api/agent/*`

The user has attached screenshots/images from the web application. You must inspect every image one by one, extract the visible UI state, identify which dashboard surface it shows, note any visible toast/status/error, and connect the screenshot to source-level workflows.

The user is seeing real workflow failures, including:

1. Queue and follow-up sections are causing unknown issues.
2. After creating a draft, the user cannot directly send the email. They must approve it and send through the queue, and this may or may not be intended.
3. Sometimes after pressing Approve / Approve & Queue, the UI says the draft is saved/approved/queued, but the email does not reach the end user.
4. Sometimes uploaded CSV files with multiple columns preview correctly, and commit says it succeeded, but the emails/contacts are not actually added to Contacts.
5. There are many feature additions that may now conflict with each other: queue, follow-ups, replies, conversations, auto-reply, campaigns, assistant, imports, drafts, and direct send paths.
6. The user wants a precise repair plan, not broad advice.

FILES YOU MUST READ FIRST

Read these four generated evidence files before making claims:

1. `llm_functionality_feature_map.md`
   - Read this first.
   - It explains the full functionality, feature dependencies, shared state, likely breakage zones, and source hotspots.

2. `architecture_test.md`
   - Read this second.
   - It explains the current architecture, runtime verification, route map, database structure, architecture tests, and known stale docs.

3. `files34.md`
   - Read this third.
   - It maps files, functions, responsibilities, tests, and where to inspect each feature.

4. `relation.md`
   - Read this fourth.
   - It contains the feature relation graph and conflict ledger.

Then read the live source files those docs point to. Do not rely only on the Markdown summaries.

MANDATORY SOURCE HOTSPOTS TO INSPECT

Backend:
- `backend/app/main.py`
- `backend/app/db/models.py`
- `backend/app/db/session.py`
- `backend/app/db/migrations/versions/0001_initial.py`
- `backend/app/db/migrations/versions/0002_agent_tables.py`
- `backend/app/db/migrations/versions/0003_reply_followup_campaigns.py`
- `backend/app/settings/service.py`
- `backend/app/imports/router.py`
- `backend/app/imports/service.py`
- `backend/app/contacts/router.py`
- `backend/app/drafts/router.py`
- `backend/app/send/policy.py`
- `backend/app/send/queue_worker.py`
- `backend/app/send/router.py`
- `backend/app/send/smtp_adapter.py`
- `backend/app/followups/router.py`
- `backend/app/followups/service.py`
- `backend/app/replies/router.py`
- `backend/app/replies/service.py`
- `backend/app/replies/imap_fetcher.py`
- `backend/app/conversations/router.py`
- `backend/app/conversations/auto_reply_service.py`
- `backend/app/conversations/auto_reply_router.py`
- `backend/app/campaigns/router.py`
- `backend/app/agent/service.py`
- `backend/app/agent/tools.py`
- `backend/app/agent/pending.py`
- `backend/app/agent/catalog.py`
- `backend/app/agent/schemas.py`
- `backend/app/agent/memory.py`

Frontend:
- `frontend/src/App.tsx`
- `frontend/src/api/client.ts`
- `frontend/src/features/floating-assistant/AssistantWidget.tsx`
- `frontend/src/features/floating-assistant/assistantApi.ts`
- `frontend/src/features/floating-assistant/assistantStore.ts`
- `frontend/src/features/floating-assistant/AssistantWidget.css`
- `frontend/src/styles.css`

Tests:
- `backend/tests/test_agent.py`
- `backend/tests/test_capability_tiers.py`
- `backend/tests/test_auto_reply.py`
- `backend/tests/test_import_policy_ai_followups.py`
- `backend/tests/test_reply_followup_campaigns.py`
- `backend/tests/test_contacts_delete.py`
- `backend/tests/test_settings_smtp_canary.py`

DO NOT READ OR QUOTE RAW SECRETS

- Do not ingest `KEYS.md`.
- Do not print `.env`.
- Do not print raw Gmail app passwords, Groq keys, Gemini keys, Fernet tokens, SMTP credentials, IMAP credentials, OAuth tokens, or cookies.
- If scanning for secrets, report only counts/categories, never values.
- Frontend must never gain Groq/Gemini keys. `VITE_API_URL` is the only intended frontend env var.

CURRENT VERIFIED FACTS YOU SHOULD USE

These facts were already verified and should be treated as current unless live source contradicts them:

- Backend tests previously passed: `python -m pytest` collected 182 and passed 182.
- Frontend build previously passed: `npm run build`.
- Backend health previously returned 200.
- OpenAPI includes `/api/agent/chat`, `/api/agent/confirm`, `/api/agent/cancel`.
- The app was observed in LIVE mode, canary verified, dry-run false.
- Provider health has shown IMAP failed / `TimeoutError`.
- The active DB is `backend/finimatic.db`; `backend/finimatic_browser.db` is stale and should not be used as current truth.
- Alembic migrations are not complete schema truth; runtime `Base.metadata.create_all()` and lightweight migrations add columns.
- `SCHEMA.md`, `PROJECT_IMPLEMENTATION_REPORT.md`, and parts of `DATA_FLOW.md` are stale relative to current source.

SCREENSHOT / IMAGE ANALYSIS REQUIREMENTS

For every attached image:

1. Assign an image id: `Image 01`, `Image 02`, etc.
2. Identify the visible page/surface, for example Drafts, Queue, Import, Follow-ups, Contacts, Auto-Reply, Settings.
3. Extract visible text, toast messages, button labels, badges, counts, selected provider, mode indicators, and any visible error or success state.
4. State the likely workflow represented by the image.
5. Connect the UI state to specific source files/functions.
6. Identify potential mismatch between the visible UI state and backend truth.
7. Do not infer that something happened unless the image or source evidence supports it. Mark uncertain claims as `UNVERIFIED`.

At minimum, for the visible Drafts screenshot, analyze:
- The left nav is on Drafts.
- The UI shows draft provider selection and AI provider tabs.
- The toast says `draft saved, approved, and queued`.
- A draft card shows an approved state.
- There are buttons like `Save`, `Save as Template`, `Approve`, `Generate`, `Save Draft`, and `Approve & Queue`.
- The user reports that after approving/queueing, the email may not reach the recipient.
- This requires tracing: Drafts UI -> draft save/approve route -> queue entry -> queue policy -> queue processing -> SMTP send -> SendAttempt -> ConversationMessage -> Follow-up schedule -> audit/provider health.

CORE QUESTIONS TO ANSWER

You must answer these with source evidence:

1. Is it intended that a draft cannot directly send and must first be approved into queue?
2. If the product should allow direct send from Drafts, what is the safest implementation path?
3. If the product should keep approve-and-queue only, what UI copy/state should change so the user understands what happens?
4. When a toast says `draft saved, approved, and queued`, what exact backend rows should exist afterward?
5. What conditions can make an approved/queued email not reach the recipient?
6. Does queue processing happen automatically, manually, or both?
7. Can queue policy block a queued email after the UI says it was queued?
8. Where are queue block reasons stored and surfaced?
9. Do follow-up records get created only after successful send, or can they be created for queued/blocked items?
10. Can replies, suppressions, deleted contacts, bounced contacts, unsubscribes, or manual pause silently block queue or follow-up behavior?
11. Why can CSV import preview show rows but commit not add contacts?
12. Is import commit using temporary in-memory preview state that can be lost?
13. Does import commit handle duplicate emails, suppressed emails, invalid emails, blocked domains, and previously deleted contacts correctly?
14. Does the frontend display committed counts clearly enough to distinguish imported, skipped, duplicate, invalid, suppressed, and blocked rows?
15. Are dashboard counts and contact lists invalidated/refetched after import commit?
16. Are Queue, Follow-ups, Replies, Conversations, Auto-Reply, and Agent using consistent contact status rules?
17. Are there multiple send paths with different policy gates?
18. Are there GET routes that write data and can cause surprising side effects?
19. Are there stale docs or migrations that could mislead a repair agent?
20. What are the highest-risk vulnerabilities or bottlenecks in the current system?

KNOWN CONFLICT AREAS TO INVESTIGATE

Investigate and either confirm, revise, or reject each item with source evidence:

1. Queue policy divergence:
   - Queue cold sends use `backend/app/send/policy.py`.
   - Conversation direct sends, auto-reply sends, and agent sends use separate gate logic.
   - Risk: one path sends while another blocks.

2. Approve/queue confusion:
   - Draft approval creates queue entries.
   - User expects a direct send option or clearer status.
   - Risk: UI toast says success but email is only queued or later blocked.

3. Queue processing ambiguity:
   - Determine whether processing is background, manual, or both.
   - Determine whether the frontend makes it obvious when an email is merely queued vs sent.

4. Follow-up issues:
   - Determine when follow-up sequences are created.
   - Determine when replies stop follow-ups.
   - Determine whether follow-up approval creates new queue entries safely.

5. CSV import commit mismatch:
   - Preview may show parsed rows.
   - Commit may skip duplicates/invalid/suppressed rows or lose temp preview state.
   - UI may say committed without surfacing per-row outcomes clearly.

6. Schema and migration drift:
   - `models.py` and active DB are ahead of Alembic/docs.
   - Fresh DB/migration behavior may differ from current local DB.

7. Conversation GET side effects:
   - GET routes can backfill and commit conversation messages.
   - Risk: browser loading can mutate state.

8. IMAP health:
   - IMAP has shown timeout/failure.
   - Reply/follow-up/auto-reply workflows may depend on working IMAP.

9. Auto-reply autonomous send:
   - Older docs say AI suggestion-only.
   - Current source may send autonomously when enabled.
   - Risk: consent/safety mismatch.

10. Floating assistant:
   - It has strict pending-confirm send harness.
   - It also has broader awareness routing.
   - It accepts attachments but may only send metadata.

SECURITY / VULNERABILITY FOCUS

Look for:

- Raw secret exposure in frontend, API responses, audit payloads, logs, localStorage, sessionStorage.
- Unintended live send paths.
- Missing confirmation gates.
- Policy bypass between queue, conversation, auto-reply, and agent.
- Contact suppression/deletion/unsubscribe bypass.
- Duplicate send/idempotency failure.
- Incomplete audit coverage.
- Blocking IMAP/SMTP calls on event loop or request path.
- SQLite lock risk under background workers and agent sessions.
- GET routes that mutate state.
- Stale migrations that break fresh environments.
- In-memory job/preview state that creates false UI success.
- UI state saying success when backend only queued or later blocked.

PERFORMANCE / BOTTLENECK FOCUS

Look for:

- SQLite lock or concurrency risks.
- Synchronous IMAP fetch bottlenecks.
- Background queue/follow-up loops colliding with request handlers.
- Large single-file frontend state causing stale query invalidation.
- Bulk draft generation stored in memory.
- Import preview stored in memory.
- Missing pagination/bounds in heavy list views.
- Repeated polling/refetch causing DB writes through GET backfill.

EXPECTED OUTPUT FILES FROM YOU

Create a complete repair documentation package. If you cannot create files directly, output the exact contents for each file.

Required files:

1. `repair.md`
   - Executive repair plan.
   - Clear severity-ranked issue list.
   - Final recommended implementation order.
   - Acceptance criteria.
   - Commands to verify.

2. `repair/01_image_evidence.md`
   - One section per attached image.
   - Visible UI facts.
   - Related source files.
   - Suspected mismatches.
   - Confidence level.

3. `repair/02_workflow_failure_map.md`
   - Map each user-reported failure to exact source workflows.
   - Include draft approve/queue/send, queue not reaching recipient, follow-up issues, CSV commit not adding contacts.

4. `repair/03_send_policy_unification.md`
   - Compare Queue, Conversation, Auto-Reply, Agent, Canary send paths.
   - Define universal hard gates.
   - Define cold-send-only gates.
   - Define engaged-reply gates.
   - Propose exact code-level consolidation.

5. `repair/04_import_contact_repair.md`
   - Analyze CSV preview/commit/contact creation.
   - Explain how multi-column imports should map to contacts.
   - Identify duplicate/suppression/invalid/deleted behavior.
   - Define UI copy and row outcome reporting.

6. `repair/05_queue_followup_repair.md`
   - Explain queue lifecycle, queue processing, block reasons, send attempts, follow-up creation, follow-up stopping.
   - Identify why queued emails might not reach recipients.
   - Define UI status improvements and backend fixes.

7. `repair/06_schema_migration_repair.md`
   - Compare current models, active DB, and Alembic.
   - Propose migration parity fix.
   - Define fresh-DB verification.

8. `repair/07_security_and_secret_review.md`
   - Secret exposure checks.
   - Send confirmation checks.
   - Audit redaction checks.
   - Frontend storage checks.

9. `repair/08_coding_agent_prompt.md`
   - A paste-ready prompt for a coding agent.
   - It must tell the coding agent to read the repair docs, make surgical fixes, preserve existing behavior, add tests, and verify.

10. `repair/09_test_plan.md`
   - Unit/integration/browser/manual checks.
   - Include exact commands.
   - Include regression tests for import commit, queue processing, follow-up stopping, send policy gates, and UI status.

REPAIR PLAN STYLE

Use this structure for every issue:

```text
Issue ID:
Title:
Severity: P0/P1/P2/P3
Status: CONFIRMED / CONFLICTED / RISK / UNVERIFIED
User symptom:
Source evidence:
Image evidence:
Likely root cause:
Affected files/functions:
Data tables affected:
Potential security/safety impact:
Smallest safe fix:
Tests to add/update:
Verification commands:
Rollback risk:
```

IMPLEMENTATION GUIDANCE

Do not recommend a massive rewrite. Recommend surgical fixes in this order unless evidence proves a different order:

1. Make UI truth accurate:
   - Clearly distinguish `saved`, `approved`, `queued`, `blocked`, `sent`, `failed`.
   - Do not show wording that implies delivery when only queue insertion happened.

2. Fix import commit visibility:
   - Show counts and row outcomes: created, updated, duplicate, invalid, suppressed, blocked, skipped.
   - Ensure contacts query invalidates/refetches after commit.
   - Ensure commit cannot depend silently on lost in-memory preview state without a clear error.

3. Unify send hard gates:
   - Extract shared hard blocks for all send paths.
   - Keep cold-only and engaged-only gates explicit.

4. Repair queue/follow-up state flow:
   - Make queue block reasons visible.
   - Ensure follow-ups are created only after successful sends.
   - Ensure replies/suppressions/deletes stop or prevent follow-ups consistently.

5. Repair migrations/schema parity:
   - Add migration(s) matching current models.
   - Add fresh DB migration verification.

6. Fix GET side effects if needed:
   - Move conversation backfill writes out of GET routes or explicitly gate them.

7. Fix assistant/attachment/storage issues after core send/import repair:
   - Do not let assistant UX imply file content analysis if backend only receives metadata.

DIRECT SEND REQUIREMENT

The user reports they cannot directly send an email after creating a draft; they must approve and send via queue.

Analyze this carefully:

- If current product design intentionally requires queue approval, say so and propose UI/copy improvements.
- If the product should support direct send from Drafts, propose the safest design:
  - button label: `Send Now`
  - confirmation modal required
  - uses the same universal hard send gates
  - writes `SendAttempt`
  - writes outbound `ConversationMessage`
  - creates follow-up only after successful send if appropriate
  - emits audit event
  - never bypasses suppression/deleted/unsubscribe/canary/cap/window/idempotency gates
  - tests cover success and every block reason

Do not add a direct send button without confirmation and policy gates.

FACT-CHECKING RULES

- Every claim must be backed by a file path, function, test, screenshot observation, or runtime check.
- If a claim is only inferred, label it `UNVERIFIED`.
- If docs and source disagree, label `CONFLICTED` and show both sides.
- Do not say "probably" without an evidence reason.
- Do not hide residual risk.

FINAL RESPONSE CONTRACT

Return:

1. A short summary of what you read.
2. The generated contents of `repair.md`.
3. The generated contents of each `repair/*.md` file.
4. A paste-ready coding-agent prompt in `repair/08_coding_agent_prompt.md`.
5. A prioritized implementation order.
6. A verification matrix.
7. A list of assumptions and remaining questions.

Do not implement code yet unless explicitly asked. This run is for deep repair planning and prompt generation.
```

