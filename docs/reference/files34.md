# Finimatic File Inventory For Conflict Analysis

Generated: 2026-05-26 15:39:52 +05:30

Purpose: identify the files, functions, responsibilities, side effects, and relationships a local LLM needs to reason about hidden flaws and conflicting behavior. Live source files are preferred over older documentation.

## How To Read This File

- Use `architecture_test.md` for the architecture and flow overview.
- Use this file to locate relevant source.
- Use `relation.md` for conflicts and cross-feature relation edges.
- Treat files under `backend/app` and `frontend/src` as current implementation truth.
- Treat root Markdown docs as historical/spec evidence that may conflict with source.
- Do not use `backend/finimatic_browser.db` as current evidence; it is stale.

## Root Project Files

| Path | Role | Use For |
|---|---|---|
| `AGENTS.md` | Session/project instructions for agent addition and strict capability/security rules. | Compare requested contract to current source. |
| `PROJECT_IMPLEMENTATION_REPORT.md` | Historical implementation report. | Useful baseline, but stale for agent/campaign/auto-reply additions. |
| `SCHEMA.md` | Historical DB schema doc. | Stale against current models; useful for identifying drift. |
| `STACK.md` | Historical stack contract. | Check dependency/env drift. |
| `DATA_FLOW.md` | Historical data flow. | Useful for intended flow; stale for current file layout and newer features. |
| `AI_INTEGRATION.md` | AI pool/provider contract. | Compare pool/scheduler intent against current AIGateway/provider use. |
| `AGENT_SCHEMA_EXTENSION.md` | Agent table/file/pipeline spec. | Compare exact agent requirements against current source. |
| `EMAIL_AGENTIC_ASSISTANT_HANDOFF.md` | Large governed assistant reference design. | Broad reference; contains capabilities/env examples not all applicable here. |
| `VERBA_ASSISTANT_REPLICATION_GUIDE.md` | Floating widget UI guide. | Compare frontend widget behavior/storage/CSS with guide. |
| `GOAL_PROMPT.md` | Original app goal prompt. | Historical intent and security guardrails. |
| `PRD.md` | Product requirements. | Historical product expectations. |
| `TESTING.md` | Manual/QA plan. | Some expectations conflict with current test fixture fake key strings. |
| `GROQ_GEMINI_KEY_POOLING_GUIDE.md` | Key pooling guidance. | Compare to current AI key use. |
| `IMPORT_REPAIR.md` | Import repair notes. | Use for import-specific context. |
| `KEYS.md` | Local ignored secrets file. | High-risk local file; do not print or ingest raw values into an LLM. |

## Backend Config And Runtime Files

| Path | Role | Notes |
|---|---|---|
| `backend/requirements.txt` | Backend dependencies. | Uses `google-genai`, while `STACK.md` mentions `google-generativeai`. |
| `backend/pytest.ini` | Test config. | `asyncio_mode=auto`, test paths `tests`, temp path `.pytest-tmp`. |
| `backend/sample.env.example` | Sample backend env. | Includes `FERNET_KEY`, `PORT`, `ALLOWED_ORIGINS`, `DATABASE_URL`; no Groq/Gemini env vars. |
| `backend/alembic.ini` | Alembic config. | Alembic chain is not complete schema truth. |
| `backend/finimatic.db` | Active SQLite DB. | 17 current tables, including agent/campaign/conversation/template tables. |
| `backend/finimatic_browser.db` | Older SQLite DB. | Stale; missing newer tables. |
| `backend/logs/*.log`, `backend/uvicorn*.log` | Runtime logs. | Useful for prior SQLite lock/IMAP errors; may contain historical rather than current state. |

## Backend Package Marker Files

These files are mostly import/package markers. They carry no current business logic in this workspace, but they are listed so a path-based local LLM does not treat them as missing.

- `backend/app/__init__.py`
- `backend/app/agent/__init__.py`
- `backend/app/ai/__init__.py`
- `backend/app/audit/__init__.py`
- `backend/app/campaigns/__init__.py`
- `backend/app/contacts/__init__.py`
- `backend/app/conversations/__init__.py`
- `backend/app/core/__init__.py`
- `backend/app/db/__init__.py`
- `backend/app/drafts/__init__.py`
- `backend/app/followups/__init__.py`
- `backend/app/imports/__init__.py`
- `backend/app/provider_health/__init__.py`
- `backend/app/replies/__init__.py`
- `backend/app/send/__init__.py`
- `backend/app/settings/__init__.py`
- `backend/app/suppressions/__init__.py`
- `backend/app/templates/__init__.py`

## Backend Entrypoint And DB

| Path | Classes / Functions | Responsibility | Important Relations |
|---|---|---|---|
| `backend/app/main.py` | `_periodic_queue_worker`, `_periodic_followup_worker`, `_scheduled_imap_reply_fetch`, `lifespan`, `create_app` | App startup, router mount, background loops, scheduler. | Calls DB init/settings seed; mounts all routers; starts IMAP schedule unless disabled. |
| `backend/app/db/models.py` | `Setting`, `Contact`, `Draft`, `Template`, `SendQueue`, `SendAttempt`, `FollowUpSequence`, `Suppression`, `Reply`, `ConversationMessage`, `AuditEvent`, `ProviderHealth`, `CampaignPlan`, `AgentSession`, `PendingEmailActionRow` | SQLAlchemy model truth. | Compare against `SCHEMA.md` and Alembic. |
| `backend/app/db/session.py` | `configure_database`, `get_engine`, `init_db`, `_apply_lightweight_migrations`, `SessionLocal`, `get_db` | DB engine/session plus runtime schema repair. | Startup creates tables and adds missing columns outside Alembic. |
| `backend/app/db/migrations/env.py` | Alembic env. | Migration wiring. | Does not compensate for no-op base migration. |
| `backend/app/db/migrations/versions/0001_initial.py` | `upgrade`, `downgrade` | No-op base migration. | Production Alembic risk. |
| `backend/app/db/migrations/versions/0002_agent_tables.py` | `upgrade`, `downgrade` | Creates two agent tables. | Missing live `AgentSession` extra columns. |
| `backend/app/db/migrations/versions/0003_reply_followup_campaigns.py` | `upgrade`, `downgrade` | Alters replies/drafts/followups and creates campaign plans. | Conflicts with "two new tables only" agent-session instruction if interpreted globally. |

## Backend Core, Settings, Audit

| Path | Classes / Functions | Responsibility | Risk Questions |
|---|---|---|---|
| `backend/app/core/crypto.py` | `fingerprint`, `get_fernet_key`, `encrypt_secret`, `decrypt_secret`, `redacted_error` | Fernet secret encryption/decryption and redacted errors. | Writes `backend/.env` if key absent; review local secret sprawl. |
| `backend/app/core/idempotency.py` | `sha256_key` | Stable hash key generation. | Used by queue, canary, auto-reply, pending confirmation. |
| `backend/app/core/time.py` | `utcnow`, `parse_datetime`, `iso` | Time helpers. | Some agent code still uses `datetime.utcnow()` directly. |
| `backend/app/settings/router.py` | `SettingsWrite`, `read_settings`, `update_settings`, `verify_smtp` | Settings API. | Verify response never returns raw secrets. |
| `backend/app/settings/service.py` | `seed_settings`, `set_settings`, `settings_read`, `get_secret`, `get_key_list`, `mode_label` | Settings persistence, encryption, typed getters. | Central secret read path for AI/send modules. |
| `backend/app/audit/router.py` | `list_audit` | Audit list API. | Redacted events only. |
| `backend/app/audit/service.py` | `redact_payload`, `emit_event`, `audit_to_dict` | Secret redaction and audit insertion. | Check all send/agent failure paths call it. |
| `backend/app/provider_health/router.py` | `provider_health_to_dict`, `list_provider_health` | Provider health API. | Current runtime reports IMAP `failed` / `TimeoutError`; use when checking mailbox assumptions. |
| `backend/app/suppressions/router.py` | `SuppressionCreate`, `suppression_to_dict`, `list_suppressions`, `create_suppression`, `delete_suppression` | Suppression list/add/delete API. | Suppression changes should affect queue, follow-up, conversation, auto-reply, and agent send gates. |

## Backend AI

| Path | Classes / Functions | Responsibility | Risk Questions |
|---|---|---|---|
| `backend/app/ai/gateway.py` | `AIGateway`, `_call_groq`, `_call_gemini`, sanitizers | Draft generation, subject variants, enrichment, provider fallback. | Groq draft generation rotates keys; some helper paths still use first key directly. |
| `backend/app/ai/groq_pool.py` | `KeyState`, `GroqKeyPool` | Groq key pool state. | Check whether all Groq callers use it or bypass it. |
| `backend/app/ai/groq_scheduler.py` | `GroqAdmissionGovernor` | Groq admission throttling. | Intended reuse vs current call sites. |
| `backend/app/ai/gemini_pool.py` | `GeminiKeyPool` | Gemini key pool alias/subclass. | Check consistency with Gemini calls. |
| `backend/app/ai/gemini_scheduler.py` | `GeminiAdmissionGovernor` | Gemini scheduling alias/subclass. | Check consistency with Gemini calls. |
| `backend/app/ai/key_utils.py` | `parse_keys`, `fingerprints` | Key parsing/fingerprints. | Used for settings read redaction. |
| `backend/app/ai/prompts.py` | `SenderProfile`, `system_prompt`, `draft_user_prompt` | Prompt building and private-field marking. | Important for leakage and unsupported-claim prevention. |
| `backend/app/ai/schema.py` | `DraftSuggestion`, `AIFailure` | AI response schemas. | Check validation and fallback paths. |

## Backend Contacts, Imports, Drafts, Templates, Campaigns

| Path | Classes / Functions | Responsibility | Risk Questions |
|---|---|---|---|
| `backend/app/imports/service.py` | `normalize_email`, `parse_payload`, `evaluate_rows`, `preview_import`, `commit_import`, `enrich_imported_contacts` | Import parse/preview/commit/enrich. | Preview cache is process-local; stale preview risk. |
| `backend/app/imports/router.py` | `preview`, `commit` | Import endpoints. | Commit can accept rows directly or temp batch. |
| `backend/app/contacts/router.py` | `ContactCreate`, `ContactPatch`, `list_contacts`, `create_contact`, `patch_contact`, `delete_contact`, `restore_contact` | Contact API and soft delete. | Delete cancels queue/followups; verify all send paths check deleted contact. |
| `backend/app/contacts/utils.py` | `parse_tags`, `contact_tags`, `is_domain_blocked`, `resolve_tokens`, `send_window_open` | Tags, suppression domain, template token resolution, send window. | Used across queue, conversation, agent. |
| `backend/app/drafts/router.py` | `DraftCreate`, `DraftGenerate`, `BulkDraftGenerate`, `store_generated_draft`, `_queue_approved_draft`, `generate_draft`, `generate_bulk`, `approve_draft` | Draft CRUD/generation/bulk/approval. | Bulk jobs use in-memory global dict/thread. |
| `backend/app/templates/router.py` | `TemplateCreate`, `list_templates`, `create_template` | Template API. | Template from draft requires approved draft. |
| `backend/app/campaigns/router.py` | `CampaignCreate`, `CampaignPatch`, `create_campaign`, `patch_campaign`, `activate_campaign`, `suggest_campaign_steps` | Campaign plan CRUD and AI step suggestions. | Campaign features are newer than old reports. |

## Backend Sending, Follow-Ups, Replies, Conversations

| Path | Classes / Functions | Responsibility | Risk Questions |
|---|---|---|---|
| `backend/app/send/policy.py` | `GateResult`, `PolicyDecision`, `evaluate_policy`, `store_policy_result` | Canonical queue policy gates. | Compare against conversation/agent direct-send gates. |
| `backend/app/send/queue_worker.py` | `create_queue_entry`, `process_pending_queue`, `_schedule_followup` | Queue processing and follow-up scheduling. | Queue path enforces canonical policy. |
| `backend/app/send/router.py` | `QueueCreate`, `list_queue`, `create_queue`, `get_queue`, `process_queue` | Queue API. | `process_queue` can send in live mode. |
| `backend/app/send/canary_router.py` | `send_canary` | Canary send and duplicate block. | Live SMTP send path. |
| `backend/app/send/smtp_adapter.py` | `SendResult`, `CanaryResult`, `SMTPTransport`, `GmailAdapter` | Gmail SMTP verify/send/canary. | Uses executor for blocking SMTP work. |
| `backend/app/send/fake_transport.py` | `FakeTransport` | Test transport. | Tests avoid live SMTP. |
| `backend/app/followups/service.py` | `process_due_followups`, `_make_followup_draft`, `approve_followup_draft` | Follow-up proposal and approval. | Follow-up drafts may queue after approval. |
| `backend/app/followups/router.py` | `list_followups`, `get_followup`, `patch_followup`, `approve_draft`, `process_followups` | Follow-up API. | `process_followups` can create drafts. |
| `backend/app/replies/imap_fetcher.py` | `run_imap_fetch_with_lock`, `IMAPReplyFetcher` | IMAP fetch and lock. | Synchronous IMAP path conflicts with executor instruction. |
| `backend/app/replies/service.py` | `create_reply_record`, `classify_intent`, `stop_followups_for_contact`, `refresh_contact_status_after_reply_change` | Reply creation/classification/stop/suppression/conversation message. | Central inbound reply side effects. |
| `backend/app/replies/router.py` | `list_replies`, `create_reply`, `fetch_replies`, `archive_reply`, `restore_reply`, `delete_reply` | Reply API and IMAP fetch trigger. | `fetch_replies` can mutate DB and provider health. |
| `backend/app/conversations/router.py` | `list_conversations`, `get_conversation`, `generate_conversation_reply`, `send_conversation_reply`, `_engaged_send_block_reasons`, `_generate_next_reply` | Conversation summaries, generation, direct engaged sends. | GET routes can commit backfills; direct-send policy differs from queue. |
| `backend/app/conversations/auto_reply_service.py` | `AutoReplyService`, `handle_reply`, `_can_auto_reply`, `_send_draft` | Auto-reply propose/autonomous send. | Autonomous mode can send outside queue. |
| `backend/app/conversations/auto_reply_router.py` | `approve_auto_reply`, `reject_auto_reply`, `pending_auto_replies`, `auto_reply_log` | Auto-reply dashboard endpoints. | Approval/rejection writes draft state. |

## Backend Agent Module

| Path | Classes / Functions | Responsibility | Risk Questions |
|---|---|---|---|
| `backend/app/agent/router.py` | `chat`, `confirm`, `cancel` | `/api/agent` public endpoints. | Mounted under `/api/agent` in main. |
| `backend/app/agent/service.py` | `AgentService`, `_prepare_agent_message` | Per-turn orchestrator, session, channel route, tool plan, pending/confirm/cancel. | Awareness bypass, pending ack, send confirmation. |
| `backend/app/agent/schemas.py` | `StrictModel`, `GoalFrame`, `IntentDecision`, `SlotAgentOutput`, `ToolPlan`, `EvidenceEnvelope`, `PendingEmailAction`, `AgentChatRequest`, `AgentChatResponse` | Strict Pydantic contracts. | `capability` fields are strings, validated procedurally. |
| `backend/app/agent/catalog.py` | `CAPABILITY_CATALOG`, `CAPABILITY_TIERS`, `validate_capability`, `check_capability_tiered` | Exact action catalog plus tiered routing. | Broader tier list conflicts with strict-catalog reading. |
| `backend/app/agent/memory.py` | `hash_session_token`, `get_or_create_session`, `update_session`, `expire_session`, `cleanup_expired_sessions` | Agent session storage. | Reuses expired rows; startup migrations add session columns. |
| `backend/app/agent/pending.py` | `params_hash`, `create_pending_action`, `validate_pending_action`, `consume_pending_action`, `cancel_pending_action` | Confirmation harness. | Consumed-before-send behavior; draft mismatch coverage. |
| `backend/app/agent/tools.py` | `AgenticToolExecutor`, `sanitize_text`, `sanitize_data`, `_read_inbox`, `_read_thread`, `_search_contacts`, `_generate_draft`, `_update_draft`, `_send_draft`, `_engaged_send_block_reasons` | Read/write tools for assistant. | Contact evidence bounds, duplicated send policy. |
| `backend/app/agent/goal_frame.py` | `GoalFrameAgent`, `_capability_from_text` | Deterministic capability guess. | Docs say Groq-backed; source is rule-based. |
| `backend/app/agent/intent.py` | `IntentAgent` | Intent validation/mirroring. | Rule-based. |
| `backend/app/agent/slot.py` | `SlotAgent`, extraction helpers | Slot extraction and clarification. | Name/email heuristic limits. |
| `backend/app/agent/orchestrator.py` | `OrchestratorAgent` | Single tool plan creation. | Rule-based. |
| `backend/app/agent/reasoning.py` | `ReasoningAgent` | Sufficient/insufficient tool result reasoning. | Rule-based. |
| `backend/app/agent/verifier.py` | `VerifierAgent` | Evidence sufficiency check. | Rule-based. |
| `backend/app/agent/response.py` | `ResponseAgent` | User response composition. | Can expose provider message id. |
| `backend/app/agent/channel_router.py` | `ChannelDecision`, `classify_channel` | Awareness/task/action classification; Groq optional. | Only pipeline stage that may call provider for routing. |
| `backend/app/agent/campaign_intelligence.py` | `build_campaign_snapshot`, `answer_awareness_query` | Awareness answers from campaign/reply/conversation data. | Broader than strict catalog. |
| `backend/app/agent/context_loader.py` | `build_context_card`, `generate_proactive_opening`, `is_context_stale` | Agent context card/proactive message. | Uses `datetime.utcnow()` warnings. |
| `backend/app/agent/fuzzy_resolver.py` | `fuzzy_resolve_contact` | Contact disambiguation. | Tests enforce no raw IDs in clarification. |
| `backend/app/agent/provider_router.py` | `call_provider_with_fallback` | Provider fallback for agent support calls. | Check key handling vs pool contract. |
| `backend/app/agent/layman_formatter.py` | `format_for_layman`, `build_contact_name_map` | Removes IDs/field-like text from responses. | Warnings from `datetime.utcnow()`. |
| `backend/app/agent/repair.py` | `RepairRouter` | Repair action routing. | Check whether integrated or dormant. |
| `backend/app/agent/NEW_AGENT_ARCHITECTURE.md` | Architecture note. | Documents newer three-channel agent behavior. | Source-aligned reference but not executable. |

## Frontend Files

| Path | Exports / Components | Responsibility | Risk Questions |
|---|---|---|---|
| `frontend/package.json` | scripts `dev`, `build`, `preview` | Frontend package scripts/deps. | React 18 runtime with React 19 type packages may be okay but is drift to watch. |
| `frontend/vite.config.ts` | Vite config | Vite dev/build. | Uses port 5173. |
| `frontend/tsconfig.json` | TypeScript config | Strict TS/noEmit. | Build passed. |
| `frontend/tailwind.config.js` | Tailwind config. | Styling. | Check scan paths if adding files. |
| `frontend/postcss.config.js` | PostCSS config. | CSS processing. | Low risk. |
| `frontend/index.html` | HTML shell. | App mount. | Low risk. |
| `frontend/src/main.tsx` | root render | React entrypoint. | Low risk. |
| `frontend/src/api/client.ts` | `api`, type exports | Dashboard API client. | Only `VITE_API_URL` env; no agent endpoints here. |
| `frontend/src/App.tsx` | `App`, all dashboard panels | Single-file dashboard UI. | Many features share one file; state conflicts and stale invalidations are important. |
| `frontend/src/styles.css` | global dashboard CSS | Dashboard styling. | App modal z-index 40, assistant z-index 80. |
| `frontend/src/vite-env.d.ts` | Vite typing. | Env typing. | Low risk. |
| `frontend/src/features/floating-assistant/assistantApi.ts` | `assistantApi`, `getSessionToken`, response types | Agent API client. | DELETE with JSON body; error JSON handled as normal response. |
| `frontend/src/features/floating-assistant/assistantStore.ts` | storage helpers, model list, message types | Assistant persistence. | Stores pending metadata; body stripped but action id retained. |
| `frontend/src/features/floating-assistant/AssistantWidget.tsx` | `AssistantWidget`, `PendingDraftCard`, formatter helpers | Floating assistant UI. | Attachments metadata only; voice interim duplication risk; confirm/cancel. |
| `frontend/src/features/floating-assistant/AssistantWidget.css` | `va-` CSS | Floating assistant styles. | z-index above modals; pointer-events contract verified. |

## Test Files

| Path | Coverage |
|---|---|
| `backend/tests/conftest.py` | Test client uses temp SQLite DB, fake transport, generated Fernet key, scheduler disabled. |
| `backend/tests/test_settings_smtp_canary.py` | Settings encryption, SMTP fake transport, canary duplicate block. |
| `backend/tests/test_import_policy_ai_followups.py` | Import, AI fallback/sanitizer, key rotation, queue policy, IMAP, conversation routing, provider switching. |
| `backend/tests/test_contacts_delete.py` | Contact delete/restore/cascade queue/follow-up cancellation. |
| `backend/tests/test_reply_followup_campaigns.py` | Reply intent, IMAP mapping, suppressions, follow-ups, campaign plan creation. |
| `backend/tests/test_auto_reply.py` | Auto-reply safety gates, quality gates, autonomous/propose modes, approve/reject, IMAP lock. |
| `backend/tests/test_agent.py` | Agent catalog deny, slots, inbox/thread tools, confirmation valid/consumed/expired/session mismatch/hash mismatch/cancel/no-key/generate-not-send/audit. |
| `backend/tests/test_agent_awareness_routing.py` | Agent channel routing and campaign intelligence awareness answers. |
| `backend/tests/test_capability_tiers.py` | Tiered capability routing including unknown task/awareness fallback. |
| `backend/tests/test_fuzzy_resolver.py` | Contact fuzzy resolver exact/partial/niche/multi/zero/stop-word cases. |
| `backend/tests/test_layman_formatter.py` | Agent response formatting removes IDs/field names. |
| `backend/tests/playwright_fallback.py` | Minimal Playwright browser check helper. |

Current test result:

- 182 tests passed with `python -m pytest`.

## Scenario And Verification Artifacts

| Path | Role |
|---|---|
| `backend/tests/scenarios/*.md` | Historical persona/subagent/manual scenario scripts and completion logs. |
| `backend/tests/scenarios/quality_gate.py` | Scenario quality evaluator. |
| `backend/tests/scenarios/sa5_collect_verifier_evidence.py` | Evidence collection helper. |
| `backend/tests/scenarios/sa5_verifier_evidence.sql` | Evidence SQL notes. |
| `verification_artifacts/*.md` | Prior live verification transcripts. |
| `verification_artifacts/*.png` | Prior browser/Gmail screenshots. |
| `browser-evidence/*.png` | Browser evidence screenshots, including current `architecture-doc-assistant-open.png`. |
| `frontend-token-verification.png` | Prior frontend token verification screenshot. |

## File-Level Hidden Conflict Checklist

Ask a local LLM to inspect these file pairs or groups together:

1. `backend/app/db/models.py` vs `SCHEMA.md` vs `backend/app/db/migrations/versions/*.py` vs `backend/app/db/session.py`.
2. `backend/app/send/policy.py` vs `backend/app/conversations/router.py` vs `backend/app/conversations/auto_reply_service.py` vs `backend/app/agent/tools.py`.
3. `backend/app/agent/catalog.py` vs `AGENTS.md` capability list vs `EMAIL_AGENTIC_ASSISTANT_HANDOFF.md` capability examples.
4. `backend/app/agent/service.py` vs `AGENT_SCHEMA_EXTENSION.md` pipeline order.
5. `backend/app/agent/tools.py` vs AGENTS read-only tool data contracts.
6. `backend/app/replies/imap_fetcher.py` vs AGENTS executor requirement.
7. `frontend/src/features/floating-assistant/assistantStore.ts` vs VERBA storage requirement.
8. `frontend/src/features/floating-assistant/AssistantWidget.tsx` vs backend attachment support.
9. `frontend/src/App.tsx` settings save/verify state clearing vs `STACK.md`/`PROJECT_IMPLEMENTATION_REPORT.md`.
10. `backend/app/ai/gateway.py` vs `backend/app/ai/*pool.py` and `AI_INTEGRATION.md`.
11. `backend/app/conversations/router.py` GET backfill commits vs frontend global `useAppData()` polling/loading.
12. `backend/app/conversations/auto_reply_service.py` autonomous send vs `AI_INTEGRATION.md` "AI suggestion only" framing.

## Exact Current Source Hotspots

- Router mount: `backend/app/main.py:103-118`.
- DB models: `backend/app/db/models.py:18`, `:41`, `:76`, `:96`, `:106`, `:123`, `:140`, `:166`, `:180`, `:196`, `:208`, `:219`, `:237`, `:256`.
- Runtime migrations: `backend/app/db/session.py:39`, `:43-92`.
- Alembic no-op: `backend/app/db/migrations/versions/0001_initial.py:20`.
- Agent migrations: `backend/app/db/migrations/versions/0002_agent_tables.py:18-38`.
- Campaign/follow-up migration: `backend/app/db/migrations/versions/0003_reply_followup_campaigns.py:19-31`.
- Agent endpoints: `backend/app/agent/router.py:15-28`.
- Agent chat orchestrator: `backend/app/agent/service.py:44`.
- Agent confirmation creation: `backend/app/agent/service.py:279`.
- Agent confirm route handler logic: `backend/app/agent/service.py:310-335`.
- Agent strict schemas: `backend/app/agent/schemas.py:9`.
- Agent catalog: `backend/app/agent/catalog.py:8`, `:84`, `:176`.
- Agent send tool: `backend/app/agent/tools.py:515-603`.
- Agent direct-send gate duplication: `backend/app/agent/tools.py:664`.
- Queue canonical policy: `backend/app/send/policy.py:33`, `:69-100`.
- Conversation direct send: `backend/app/conversations/router.py:224`, `:304`.
- Conversation GET backfill commits: `backend/app/conversations/router.py:176-191`.
- Auto-reply gates/send: `backend/app/conversations/auto_reply_service.py:319`, `:471`.
- IMAP fetch route: `backend/app/replies/router.py:65`.
- IMAP fetcher: `backend/app/replies/imap_fetcher.py:59`.
- Assistant mount: `frontend/src/App.tsx:52`, `:247`.
- Assistant API: `frontend/src/features/floating-assistant/assistantApi.ts:82-93`.
- Assistant storage: `frontend/src/features/floating-assistant/assistantStore.ts:100-124`.
- Assistant submit attachments: `frontend/src/features/floating-assistant/AssistantWidget.tsx:202-214`, `:251`, `:266`.
- Assistant speech: `frontend/src/features/floating-assistant/AssistantWidget.tsx:272-290`.
- Assistant CSS pointer/z-index: `frontend/src/features/floating-assistant/AssistantWidget.css:1-15`.
- App modal z-index: `frontend/src/styles.css:1187`.
