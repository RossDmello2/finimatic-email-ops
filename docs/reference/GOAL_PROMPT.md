/goal Build Finimatic — configurable cold-email ops system. Read AGENTS.md first, then every file it lists, before writing any code.

P0 Map workspace: what exists, what is dry-run only, what is missing, what is stale. Define acceptance criteria before editing.

P1 Backend: FastAPI + SQLAlchemy 2 (SCHEMA.md) + Alembic. Settings stores: gmail_user, gmail_app_password (Fernet-encrypted), groq_keys[] (JSON encrypted), gemini_keys[] (JSON encrypted), caps, followup config, dry_run, canary_verified. API returns sha256[:12] fingerprints only — never raw secrets. FakeTransport for tests. FERNET_KEY from env; auto-generate if absent.

P2 Gmail + canary: GmailAdapter.verify()/send_message()/canary_send(). Readiness: not_configured|configured|smtp_verified|canary_verified|failed. Canary: one email to report_recipient, nonce+timestamp subject, idempotency key blocks duplicate, sets canary_verified=true. Lead sends blocked until canary_verified=true.

P3 Import: CSV/TXT/paste/manual. Required: email+(creator_name or business_name)+source. Row statuses: accepted|invalid_email|duplicate|suppressed|missing_field|parse_error. /preview no-commit; /commit re-checks+replay-safe.

P4 Drafts + AI: Manual + Groq/Gemini (AI_INTEGRATION.md). Groq pool: DB keys, LRU, per-key 429 cooldown. Gemini: same pattern. DraftSuggestion(subject,body,warnings) pydantic-validated; malformed→AIFailure+audit+empty draft. AI cannot approve/send/suppress. POST /drafts/{id}/approve required before queue.

P5 Queue + policy: idempotency_key=sha256(contact_id+seq+draft_id). Worker every 30s. Gates: sender_verified, canary_verified, draft_approved, no_suppression, no_bounce, no_reply, no_pause, cap_daily, cap_hourly, window_ok, idempotency_ok. Fail→blocked+reason_codes+audit. dry_run→skipped.

P6 Follow-ups: due_at=last_sent_at+interval_days. APScheduler every 5min. Recheck before dispatch: replied|unsubscribed|suppressed|bounced|paused|cap_block|max_reached → stop+reason.

P7 Dashboard (React 18+TS+Vite+Tailwind): Setup|ProviderHealth|Import|Contacts|Drafts|Queue|FollowUps|Replies|Suppressions|Audit|Errors|Settings. Settings: gmail_user, app_password (type=password, cleared after save), Groq textarea, Gemini textarea, caps, followup config, dry-run toggle. Fingerprints only. Mode label always visible: DRY-RUN|CANARY|LIVE. Canary needs confirmation modal.

P8 Tests: SMTP auth (FakeTransport), canary dup blocked, all import statuses, each policy gate, AI malformed fallback, all-keys-exhausted→model_unavailable_rate_limited, audit events. Frontend: build clean, password cleared, no raw secret in DOM.

P9 Browser: Load KEYS.md creds into Settings UI → verify SMTP → canary → confirm sender Sent Mail → confirm report_recipient inbox → no duplicate → dry-run sends nothing. Record nonce+idempotency proof.

SECURITY: app_password Fernet-encrypted, never in API/logs/tests/fixtures. No .env committed. VITE_API_URL only. AI cannot trigger sends. No UI bypass of hard policy stops.

FINAL REPORT: Current truth | Changed files | Sender verification | Canary evidence | Feature matrix | Test commands+exit codes | Browser evidence | Secret scan | Blockers only. NOT COMPLETE if any required dimension fails.
