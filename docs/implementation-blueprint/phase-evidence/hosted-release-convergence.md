# Hosted Release Convergence Evidence

HOSTED_RELEASE_CONVERGENCE_STATUS: BLOCKED

## Verdict

The release branch is locally implemented and verified, but production cutover is blocked.

The blocker is not code test failure. The blocker is hosted configuration and external proof:

- Render production must be pointed at the fresh Supabase database with the final secret `DATABASE_URL`.
- A dedicated application-login Google OIDC client must be configured for Finimatic operator sign-in.
- Gmail and AI provider credentials must be configured through encrypted backend Settings after deployment.
- A real Gmail API proof still requires action-time confirmation before sending one message.

No production Vercel or Render deploy was performed from this branch.

## Repo And Branch

- Worktree: `C:\Users\rossd\AppData\Local\Temp\finimatic-hosted-release-convergence-20260608`
- Branch: `codex/hosted-release-convergence`
- Base commit: `0bb584f3d4c1496c29edf65cbacebe90f5857063`
- Remote: `https://github.com/RossDmello2/finimatic-email-ops.git`
- GitHub repo verified by CLI: `RossDmello2/finimatic-email-ops`
- GitHub viewer permission: `ADMIN`
- Working tree changed-file count after this artifact: 117 paths

## Platform State Verified

- Vercel account: `crce9955ce-8405`
- Vercel project: `finimatic-frontend`
- Vercel production domain: `https://finimatic-frontend.vercel.app/`
- Render workspace: `My Workspace`
- Render backend service: `finimatic-backend`
- Render service URL: `https://finimatic-backend.onrender.com`
- Render branch: `main`
- Render plan: Free web service
- Existing Supabase archive project retained: `hpamfbjawuyztqowtrth`
- Fresh Supabase project created: `finimatic-prod-20260608`
- Fresh Supabase ref: `ylyfkkzkmcbvoxhgnghm`

## Fresh Supabase Database Evidence

- Applied migrations:
  - `finimatic_initial_schema_chunk_01`
  - `finimatic_initial_schema_chunk_02`
  - `finimatic_initial_schema_chunk_03`
  - `finimatic_enable_public_rls`
- `public.alembic_version`: `0013_supabase_public_rls`
- Public table count: 44
- RLS-enabled public table count: 44
- Security advisors after RLS: INFO-level `rls_enabled_no_policy` only. This is expected because the browser must not use Supabase client keys for app data; backend SQLAlchemy owns access.

## Implemented Contract

- Preserved provider-truth send outcome fields across send paths.
- Gmail API success requires provider-native message evidence before durable acceptance.
- Fake and dry-run outcomes remain simulated and cannot become provider accepted.
- Single draft `Approve & Send` synchronously dispatches the selected row.
- Queue remains the surface for bulk work, intentional schedule, deferral, retry, cancel, clear, and reconciliation.
- Added `DELETE /api/queue` and `DELETE /api/queue/{queue_id}`.
- Clear/remove preserve provider-accepted and uncertain provider-contact evidence.
- Repeated manual sends use new intents instead of reusing old sequence rows.
- Historical high sequence numbers no longer create labels like `Follow-up #992919`.
- Queue Process now reports truthful zero-work and scheduling counts.
- Render Free defaults are manual/proposal-first, not always-on automation.
- Vercel `/api/*` same-origin proxy is configured in `vercel.json`.
- Local direct use remains development-only and loopback-oriented.

## Authentication

Implemented production-oriented OIDC/BFF pieces:

- Authorization Code + PKCE start/callback flow.
- State and nonce validation.
- Server-side opaque operator sessions.
- HttpOnly cookie session transport.
- CSRF protection for mutating requests.
- Operator/admin authorization checks.
- Local direct-use escape hatch only when auth is explicitly disabled in development.

Hosted production is still blocked until a dedicated application-login Google OIDC client is configured. Gmail OAuth transport credentials are intentionally not reused as Finimatic application-login credentials.

## Hosted Deployment Blockers

- Render Free does not support using a pre-deploy command for migrations, so the fresh Supabase schema must be initialized before production cutover.
- Render production `DATABASE_URL` cannot be switched until the fresh Supabase connection string is available as a secret.
- Render production must receive OIDC app-login env vars and authorized operator/admin subject values.
- Vercel project is verified, but this clean worktree is not linked with `.vercel/project.json`; deployment should go through the existing Git integration after branch review or an explicitly linked Vercel deploy command.
- The real Gmail proof was not performed because action-time confirmation is required.

## Test Results

- Focused backend:
  - Command: `python -m pytest -q tests/test_phase17_queue_operations.py tests/test_phase17_auth.py tests/test_schema_migration.py tests/test_phase11_transport_truth.py tests/test_settings_smtp_canary.py --basetemp C:\Users\rossd\AppData\Local\Temp\finimatic-pytest-focused-c`
  - Result: `94 passed, 2 warnings`
- Alembic local empty SQLite upgrade:
  - Command: `python -m alembic upgrade head`
  - Result: passed through `0013_supabase_public_rls`
- Full backend round 1:
  - Command: `python -m pytest -q --basetemp C:\Users\rossd\AppData\Local\Temp\finimatic-pytest-full-c`
  - Result: `438 passed, 177 warnings`
- Full backend round 2:
  - Command: `python -m pytest -q --basetemp C:\Users\rossd\AppData\Local\Temp\finimatic-pytest-full-d`
  - Result: `438 passed, 177 warnings`
- Frontend production build:
  - Command: `npm run build`
  - Result: passed
- Diff hygiene:
  - Command: `git diff --check`
  - Result: passed with CRLF normalization warnings only

## Browser Evidence

Local stack:

- Frontend: `http://127.0.0.1:5173/`
- Backend: `http://127.0.0.1:8017/`
- Database: disposable SQLite database
- Scheduler: disabled
- Auto-processing: disabled
- Transport override: fake
- Configured transport in Settings: `gmail_api`
- Effective transport in UI: `fake`
- Identity in UI: synthetic test identity
- Provider execution in UI: simulated, provider not contacted

Browser checks:

- All 20 sidebar panels selected successfully.
- Floating Assistant opened successfully.
- Refresh preserved transport truth.
- Console errors: 0
- Representative proxied API response secret-pattern hits: 0
- Desktop and narrow screenshots captured.

Screenshots:

- `docs/implementation-blueprint/phase-evidence/screenshots/hosted-release-local-setup-desktop.png`
- `docs/implementation-blueprint/phase-evidence/screenshots/hosted-release-local-queue-desktop.png`
- `docs/implementation-blueprint/phase-evidence/screenshots/hosted-release-local-settings-desktop.png`
- `docs/implementation-blueprint/phase-evidence/screenshots/hosted-release-local-settings-narrow.png`

## Secret Scan

Diff-scoped pattern counts:

- `gsk_`: 2, classified as fake test fixtures in `backend/tests/test_agent.py`.
- `AIza`: 2, classified as fake test fixtures in `backend/tests/test_agent.py`.
- `app password`: 2, classified as UI wording in `frontend/src/App.tsx`.
- `refresh token`: 0
- `database URL`: 0
- `Fernet key`: 0
- `OAuth client secret`: 0
- `Render token`: 0
- `Vercel token`: 0
- `Supabase token`: 0
- `GitHub token`: 0
- `GOCSPX`: 0
- `1//`: 0

No raw credential values were printed, committed, logged, or screenshot.

## Changed File Ledger

Final changed paths: 117.

High-level groups:

- GitHub workflow cleanup and hosted verification.
- Backend security/auth, send truth, queue lifecycle, follow-up safety, stop-fence, workflow/integration preservation, migrations, and tests.
- Frontend API/session handling, all-panel UI, queue controls, direct send, and responsive styles.
- Render/Vercel hosted topology.
- Hosted release plan and evidence screenshots.

Exact path list is the current `git status --short` list for this worktree. The release should be reviewed as one branch, not cherry-picked from the original dirty checkout.

## Current Changed Path List

```text
.github/workflows/deploy-frontend-pages.yml
.github/workflows/manual-platform-deploy.yml
.github/workflows/verify-hosted-deploy.yml
.gitignore
backend/app/agent/campaign_intelligence.py
backend/app/agent/catalog.py
backend/app/agent/context_loader.py
backend/app/agent/goal_frame.py
backend/app/agent/layman_formatter.py
backend/app/agent/pending.py
backend/app/agent/schemas.py
backend/app/agent/service.py
backend/app/agent/tools.py
backend/app/audit/service.py
backend/app/campaigns/router.py
backend/app/contacts/router.py
backend/app/conversations/auto_reply_router.py
backend/app/conversations/auto_reply_service.py
backend/app/conversations/router.py
backend/app/db/migrations/env.py
backend/app/db/migrations/schema_0001.py
backend/app/db/migrations/versions/0001_initial.py
backend/app/db/migrations/versions/0004_orange_slice_upgrade.py
backend/app/db/migrations/versions/0005_phase12_queue_claim_fencing.py
backend/app/db/migrations/versions/0006_phase12_governed_dispatch_lock.py
backend/app/db/migrations/versions/0007_phase12_contact_send_stop_fence.py
backend/app/db/migrations/versions/0008_phase13_reply_dedupe_key.py
backend/app/db/migrations/versions/0009_phase17_workflow_integration_leases.py
backend/app/db/migrations/versions/0010_phase17_schema_convergence.py
backend/app/db/migrations/versions/0011_phase17_operator_sessions.py
backend/app/db/migrations/versions/0012_phase17_queue_schedule_source.py
backend/app/db/migrations/versions/0013_supabase_public_rls.py
backend/app/db/models.py
backend/app/db/session.py
backend/app/deliverability/__init__.py
backend/app/deliverability/router.py
backend/app/deliverability/service.py
backend/app/drafts/router.py
backend/app/drafts/service.py
backend/app/enrichment/__init__.py
backend/app/enrichment/router.py
backend/app/enrichment/service.py
backend/app/followups/router.py
backend/app/followups/service.py
backend/app/imports/router.py
backend/app/imports/service.py
backend/app/integrations/__init__.py
backend/app/integrations/router.py
backend/app/integrations/service.py
backend/app/main.py
backend/app/replies/imap_fetcher.py
backend/app/replies/router.py
backend/app/replies/service.py
backend/app/security/__init__.py
backend/app/security/authorization.py
backend/app/security/browser_session.py
backend/app/security/oauth_state.py
backend/app/send/auto_process.py
backend/app/send/canary_router.py
backend/app/send/fake_transport.py
backend/app/send/governance.py
backend/app/send/governance_router.py
backend/app/send/outcomes.py
backend/app/send/policy.py
backend/app/send/queue_worker.py
backend/app/send/router.py
backend/app/send/sequence.py
backend/app/send/smtp_adapter.py
backend/app/send/stop_service.py
backend/app/send/truth.py
backend/app/settings/router.py
backend/app/settings/service.py
backend/app/suppressions/router.py
backend/app/verification/__init__.py
backend/app/verification/adapters.py
backend/app/verification/router.py
backend/app/verification/service.py
backend/app/workflows/__init__.py
backend/app/workflows/router.py
backend/app/workflows/service.py
backend/requirements.txt
backend/sample.env.example
backend/tests/conftest.py
backend/tests/loopback_oidc_provider.py
backend/tests/test_agent.py
backend/tests/test_auto_reply.py
backend/tests/test_gmail_api_oauth.py
backend/tests/test_gmail_api_transport.py
backend/tests/test_import_policy_ai_followups.py
backend/tests/test_orange_slice_upgrade.py
backend/tests/test_phase11_transport_truth.py
backend/tests/test_phase12_send_safety.py
backend/tests/test_phase17_auth.py
backend/tests/test_phase17_campaign_sequence.py
backend/tests/test_phase17_queue_operations.py
backend/tests/test_phase17_workflow_integration_leases.py
backend/tests/test_reply_followup_campaigns.py
backend/tests/test_schema_migration.py
backend/tests/test_settings_smtp_canary.py
docs/implementation-blueprint/HOSTED_RELEASE_CONVERGENCE.md
docs/implementation-blueprint/phase-evidence/hosted-release-convergence.md
docs/implementation-blueprint/phase-evidence/screenshots/hosted-release-local-queue-desktop.png
docs/implementation-blueprint/phase-evidence/screenshots/hosted-release-local-settings-desktop.png
docs/implementation-blueprint/phase-evidence/screenshots/hosted-release-local-settings-narrow.png
docs/implementation-blueprint/phase-evidence/screenshots/hosted-release-local-setup-desktop.png
frontend/.env.example
frontend/package-lock.json
frontend/package.json
frontend/src/App.tsx
frontend/src/api/client.ts
frontend/src/features/floating-assistant/AssistantWidget.tsx
frontend/src/features/floating-assistant/assistantApi.ts
frontend/src/features/floating-assistant/assistantStore.ts
frontend/src/styles.css
frontend/vite.config.ts
render.yaml
scripts/verify-deploy.ps1
vercel.json
```

## No External Side Effects

- Real email sent: NO
- Hosted Vercel production deploy: NO
- Hosted Render production deploy: NO
- Existing Supabase archive mutation: NO
- Fresh Supabase project mutation: YES, created and initialized as the planned new production database candidate.
- GitHub push/PR: not yet performed in this evidence snapshot.

## Remaining Release Blockers

1. Commit and push the branch for review.
2. Configure Render production secrets:
   - fresh Supabase `DATABASE_URL`;
   - `FERNET_KEY`;
   - OIDC app-login issuer/audience/JWKS/client/secret/redirect values;
   - operator/admin subjects.
3. Configure encrypted Settings after deploy:
   - Gmail API transport credentials;
   - AI provider keys;
   - sender/recipient identities.
4. Confirm one bounded Gmail API send at action time.
5. Run post-deploy auth, CSRF, all-panel, queue, Gmail provider-acceptance, audit, conversation, follow-up, console, CORS, cookie, and secret-exposure checks.

## Blunt Status

Code convergence is locally green. Hosted production release is blocked until hosted secret configuration and the one-message Gmail proof are completed.
