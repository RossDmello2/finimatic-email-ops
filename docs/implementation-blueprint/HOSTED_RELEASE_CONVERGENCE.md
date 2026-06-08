# Finimatic Hosted Release Convergence

Status: IMPLEMENTING

## Release Baseline

- Release branch: `codex/hosted-release-convergence`
- Base commit: `0bb584f3d4c1496c29edf65cbacebe90f5857063`
- Production frontend: `https://finimatic-frontend.vercel.app/`
- Production backend: `https://finimatic-backend.onrender.com`
- Hosted transport: Gmail API only
- Backend plan: Render Free
- Database plan: fresh Supabase database; existing production database retained unchanged as archive
- Hosted access: private OIDC browser session
- Local access: explicit development-only loopback direct use

## Source Ledger

| Source | Use | Disposition |
| --- | --- | --- |
| `origin/main` | Clean release base | Keep |
| Phase 17 candidate | Send truth, queue lifecycle, OIDC BFF, migrations, 20-panel implementation, regression tests | Import selected application slices |
| Original dirty checkout | Queue clear/remove behavior and UX reference | Import only explicitly reviewed queue operations |
| Candidate documentation and queued prompts | Historical evidence only | Exclude |
| Candidate `AGENTS.md` | Repository instruction churn unrelated to release implementation | Exclude |
| Netlify configuration | Not part of the chosen topology | Exclude |
| Legacy GitHub Pages deployment | Replaced by Vercel production | Remove/disable |
| Manual service-creation scripts | Existing Render service is retained | Exclude |

## Planned Changed-File Groups

| Group | Reason | Required verification |
| --- | --- | --- |
| Send, Queue, follow-up, reply, conversation services | Durable provider acceptance and governed side effects | Phase 11/12/17 tests, duplicate dispatch tests |
| Draft, contact, campaign and settings routes | Direct Approve & Send, replacement rules, schedule recalculation | Queue operation and API tests |
| Security package and app mounting | OIDC Authorization Code + PKCE, sessions, CSRF and role enforcement | Authentication suite and browser flow |
| Database models and migrations | Fresh schema for provider truth, leases, auth sessions and schedule source | Upgrade from empty database and schema tests |
| Deliverability, enrichment, verification, workflow and integration modules | Preserve the required 20-panel product surface | API tests and browser panel checks |
| Frontend application and API client | Same-origin sessions, all panels, direct send and truthful queue UI | TypeScript build and Browser checks |
| `render.yaml` and `vercel.json` | Production environment, migration command and same-origin API proxy | Preview and production smoke tests |
| GitHub workflows and deployment verification | Remove stale GitHub Pages path and target Vercel | CI run |

## Send State Transitions

| Start | Condition | Result |
| --- | --- | --- |
| queued/pending | Current policy passes and selected dispatch is requested | processing |
| processing | Gmail API returns a nonempty provider-native ID | provider_accepted / sent projection |
| processing | Fake or dry-run outcome | simulated; never sent |
| processing | Provider definitively rejects/fails before acceptance | failed or blocked; retryable when safe |
| processing | Provider contact is ambiguous | reconciliation_required; no automatic resend |
| pending | Policy currently defers | pending with `schedule_source=policy_deferral` |
| pending | Explicit reevaluation passes | due now and selected dispatch |
| actionable, nonaccepted | Operator removes or clears | cancelled |
| provider accepted | Clear, remove, replacement or duplicate dispatch | refused; preserve evidence |

## Deployment Runbook

1. Build and test the release branch only.
2. Run the complete Alembic chain against a fresh disposable PostgreSQL database.
3. Create a fresh Supabase database and apply the same migration chain.
4. Configure only non-secret production topology values initially.
5. Deploy a Vercel preview and temporary Render Free preview service against the fresh database.
6. Configure the dedicated application-login OIDC client and authorized subject.
7. Configure provider credentials through encrypted backend Settings.
8. Verify authentication, all panels, dry-run/fake truth, queue behavior and no secret exposure.
9. Request immediate confirmation for one Gmail API proof.
10. After all acceptance gates pass, switch the existing Render service database and deploy the reviewed release to the existing production URLs.
11. Restore proposal-only automation and remove actionable test queue rows.

## Rollback

- Keep the previous Vercel and Render deploy IDs.
- Keep the old Supabase project and database URL unchanged.
- On failure, restore the previous Render and Vercel deployments and the previous Render `DATABASE_URL`.
- Never downgrade irreversible migrations in place; abandon the fresh database instead.

## Acceptance Matrix

- [ ] All focused backend tests pass.
- [ ] Full backend suite passes twice from fresh basetemps.
- [ ] Frontend production build passes.
- [ ] Empty-schema Alembic upgrade passes on PostgreSQL.
- [ ] Fake and dry-run cannot become sent or create follow-ups.
- [ ] Gmail acceptance requires a provider-native message ID.
- [ ] Single Approve & Send dispatches synchronously.
- [ ] Bulk queue-only and bulk immediate-send actions are distinct.
- [ ] Clear/remove preserves accepted and historical evidence.
- [ ] Repeated explicit sends use new intents and idempotency keys.
- [ ] All 20 panels and Assistant load locally and after refresh.
- [ ] Hosted anonymous operational access is rejected.
- [ ] Hosted sign-in, refresh restoration, CSRF and logout pass.
- [ ] No secrets appear in source, responses, logs, storage or screenshots.
- [ ] One separately confirmed Gmail API proof succeeds without duplicate delivery.
- [ ] Existing production URLs pass post-cutover checks.

