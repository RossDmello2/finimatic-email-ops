# Finimatic — Data Flow & Architecture

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Browser UI  (React 18 + TypeScript + Vite)   │
│  Settings | Import | Contacts | Drafts | Queue | Audit | Health  │
│          NO secrets in Vite env vars — all POST to backend       │
└─────────────────────────┬───────────────────────────────────────┘
                          │  REST API (JSON)
┌─────────────────────────▼───────────────────────────────────────┐
│                    FastAPI Backend (Python 3.11+)                │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────────┐ │
│  │  Settings   │  │  Import     │  │  Contacts / State Machine │ │
│  │  Service    │  │  Service    │  │  Service                  │ │
│  └─────────────┘  └─────────────┘  └──────────────────────────┘ │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────────┐ │
│  │  Draft      │  │  AI         │  │  Queue / Policy Engine   │ │
│  │  Service    │  │  Gateway    │  │  + Background Worker     │ │
│  └─────────────┘  └──────┬──────┘  └──────────────────────────┘ │
│                          │         ┌──────────────────────────┐  │
│  ┌─────────────┐         │         │  Follow-up Scheduler     │  │
│  │  Send       │         │         │  (APScheduler)           │  │
│  │  Service    │         │         └──────────────────────────┘  │
│  │  (SMTP)     │         │         ┌──────────────────────────┐  │
│  └─────────────┘         │         │  Audit Service           │  │
│                          │         └──────────────────────────┘  │
└──────────────────────────┼──────────────────────────────────────┘
                           │
              ┌────────────▼──────────────┐
              │      AI Gateway           │
              │  ┌──────────┐ ┌────────┐  │
              │  │ Groq     │ │Gemini  │  │
              │  │ Key Pool │ │Key Pool│  │
              │  │+Scheduler│ │+Sched. │  │
              │  └──────────┘ └────────┘  │
              └───────────────────────────┘

              ┌──────────────┐   ┌──────────────┐
              │  SQLite/PG   │   │  Gmail SMTP  │
              │  (via SQLAlch│   │  smtp.gmail  │
              │  emy)        │   │  .com:587    │
              └──────────────┘   └──────────────┘
```

---

## Settings Flow

```
Operator: paste gmail_user + app_password + groq_keys[] + gemini_keys[]
    ↓
POST /api/settings
    ↓
Settings Service:
  - Fernet-encrypt app_password before storage
  - Fernet-encrypt each Groq key before storage
  - Fernet-encrypt each Gemini key before storage
  - Store in settings table
    ↓
POST /api/settings/verify-smtp
    ↓
Send Service: smtplib SMTP_SSL(smtp.gmail.com, 465) → login(user, raw_password)
  → success: sender_readiness = smtp_verified, audit event
  → failure: sender_readiness = failed, redacted error, audit event
    ↓
API returns: { status: "smtp_verified" | "failed", error_code?: string }
  — raw password NEVER in response
```

---

## Canary Send Flow

```
Operator: clicks "Test Send" → confirmation modal → confirms
    ↓
POST /api/canary/send
    ↓
Backend:
  1. Check idempotency: SELECT FROM send_attempts WHERE idempotency_key = canary_key
     → exists: return { status: "duplicate_blocked", previous_attempt_id }
  2. Load gmail_user + decrypt gmail_app_password from settings
  3. SMTP send: to=report_recipient, subject="Finimatic Canary {nonce} {timestamp}",
     body includes nonce+timestamp+sender_identity
  4. Record send_attempt(status="success", idempotency_key=canary_key, provider_msg_id)
  5. Update settings: canary_verified=true
  6. Emit audit_event(canary.success)
    ↓
API returns: { nonce, sent_at, sender_identity, status, message_id? }
```

---

## Import Flow

```
Operator: uploads CSV/TXT OR pastes text OR enters single contact
    ↓
POST /api/import/preview   ← does NOT commit to DB
    ↓
Import Service:
  For each row:
    - parse email, creator_name/business_name, source
    - validate email format → invalid_email
    - check duplicates (contacts table) → duplicate
    - check suppressions table → suppressed
    - check required fields → missing_field
    → result: { row_num, status, reason, parsed_data }
    ↓
API returns: { batch_id_temp, rows: [preview_results], summary }
    ↓
Operator reviews → clicks Commit
    ↓
POST /api/import/commit   { batch_id_temp }
    ↓
Import Service:
  - Re-run duplicate + suppression check (replay-safe)
  - INSERT accepted rows into contacts table
  - INSERT import_batch + import_rows
  - Emit audit_event(import.committed, { accepted, rejected, suppressed })
```

---

## Draft + AI Flow

```
Operator: selects contact → clicks "Generate Draft" → picks provider (Groq/Gemini/Manual)
    ↓
POST /api/drafts/generate  { contact_id, provider, tone?, length? }
    ↓
Draft Service → AI Gateway:
  1. Load personalization evidence from contacts.personalization
  2. Build prompt: system=role, user=evidence+instructions
  3. AI Gateway selects provider:
     → Groq: acquire from GroqKeyPool + admission governor (see AI_INTEGRATION.md)
     → Gemini: acquire from GeminiKeyPool + admission governor
     → Manual: return empty draft
  4. Validate AI response (pydantic schema)
     → malformed: emit audit_event(draft.ai_failed), return empty draft
  5. Store draft(approved=false, ai_provider, ai_model, warnings)
  6. Emit audit_event(draft.ai_generated)
    ↓
API returns: { draft_id, subject, body, warnings, ai_provider, ai_model }
    ↓
Operator edits in UI → saves edits → clicks "Approve"
    ↓
POST /api/drafts/{draft_id}/approve
    ↓
Draft Service:
  - Set approved=true, approved_at=now()
  - Update contact.status = "approved"
  - Emit audit_event(draft.approved)
```

---

## Send Queue + Policy Engine Flow

```
Approved draft → Queue Service: create send_queue entry
  idempotency_key = sha256(contact_id + sequence_num + draft_id)
  → UNIQUE constraint prevents duplicate entry
  → status = "pending", scheduled_at = now() + send_delay_s
    ↓
Background Worker (runs every 30s):
  SELECT * FROM send_queue WHERE status='pending' AND scheduled_at <= now()
  For each entry:
    ↓
  Policy Gate Check (ALL must pass):
    [1] sender_verified:  settings.canary_verified = true
    [2] draft_approved:   drafts.approved = true
    [3] no_suppression:   email NOT IN suppressions
    [4] no_bounce:        no bounce reply record
    [5] no_reply:         contact.status NOT IN (replied, unsubscribed)
    [6] no_pause:         contact.status != manually_paused
    [7] cap_daily:        count(sent today) < daily_send_cap
    [8] cap_hourly:       count(sent this hour) < hourly_send_cap
    [9] idempotency:      no existing sent attempt for this idempotency_key
        ↓
    Any gate fails → status="blocked", policy_block_reasons=[codes], audit_event(queue.gate_blocked)
    All gates pass → proceed
        ↓
    dry_run=true  → status="skipped", audit_event(send.dry_run_blocked)
    dry_run=false → Send Service:
                    - decrypt gmail_app_password
                    - smtplib SMTP_SSL send
                    - record send_attempt
                    - update contact.status="sent"
                    - status="sent", audit_event(send.success)
                    - schedule follow_up_sequences per config
```

---

## Follow-up Scheduler Flow

```
APScheduler job (every 5 min):
  SELECT * FROM follow_up_sequences WHERE status='due' AND due_at <= now()
  For each:
    ↓
    Stop condition checks (ALL must pass — any fails = stop):
      contact.status IN (replied, unsubscribed, suppressed, bounced, manually_paused)
      OR suppressions match
      OR bounce record exists
      OR follow_up_sequences count for contact >= max_followups_per_lead
      OR cap/window block
        ↓
      Any stop → status="stopped", stop_reason=code, contact.status="follow_up_stopped"
                  audit_event(followup.stopped)
      All pass  → acquire send slot → send follow-up → status="sent"
                  audit_event(followup.dispatched)
                  schedule next follow_up_sequences entry if max not reached
```

---

## File Structure

```
finimatic/
├── backend/
│   ├── app/
│   │   ├── main.py                     # FastAPI app, lifespan, router mount
│   │   ├── db/
│   │   │   ├── models.py               # SQLAlchemy models (ALL tables)
│   │   │   ├── session.py              # get_db dependency
│   │   │   └── migrations/             # Alembic
│   │   ├── core/
│   │   │   ├── crypto.py               # Fernet encrypt/decrypt
│   │   │   ├── idempotency.py          # key generation helpers
│   │   │   └── config.py              # pydantic-settings (FERNET_KEY, PORT)
│   │   ├── settings/
│   │   │   ├── router.py              # GET/POST /api/settings, /verify-smtp
│   │   │   ├── service.py             # get/set/encrypt/decrypt logic
│   │   │   └── schema.py              # SettingsRead, SettingsWrite (no raw secrets)
│   │   ├── contacts/
│   │   │   ├── router.py              # CRUD + status updates
│   │   │   ├── service.py             # state machine transitions
│   │   │   └── schema.py
│   │   ├── imports/
│   │   │   ├── router.py              # /preview, /commit
│   │   │   ├── service.py             # parse + validate + suppress check
│   │   │   ├── parsers.py             # csv_parser, txt_parser, paste_parser
│   │   │   └── schema.py
│   │   ├── drafts/
│   │   │   ├── router.py              # CRUD + /approve + /generate
│   │   │   ├── service.py
│   │   │   └── schema.py
│   │   ├── ai/
│   │   │   ├── gateway.py             # provider dispatch, fallback
│   │   │   ├── groq_pool.py           # GroqKeyPool (from reference)
│   │   │   ├── groq_scheduler.py      # GroqAdmissionGovernor (from reference)
│   │   │   ├── gemini_pool.py         # GeminiKeyPool (same pattern)
│   │   │   ├── gemini_scheduler.py    # GeminiAdmissionGovernor
│   │   │   └── prompts.py             # system + user prompt builders
│   │   ├── send/
│   │   │   ├── smtp_adapter.py        # GmailAdapter (verify/send/canary)
│   │   │   ├── fake_transport.py      # FakeTransport for tests
│   │   │   ├── canary_router.py       # POST /api/canary/send
│   │   │   ├── queue_worker.py        # background polling + policy engine
│   │   │   └── policy.py             # PolicyGate dataclass + all gate checks
│   │   ├── followups/
│   │   │   ├── scheduler.py           # APScheduler job
│   │   │   └── service.py             # stop-condition checks + dispatch
│   │   ├── suppressions/
│   │   │   ├── router.py
│   │   │   └── service.py
│   │   ├── replies/
│   │   │   ├── router.py              # manual stop marking in V1
│   │   │   └── service.py
│   │   └── audit/
│   │       ├── router.py              # GET /api/audit
│   │       └── service.py             # emit_event(event_type, entity, payload)
│   ├── tests/
│   │   ├── test_settings.py
│   │   ├── test_smtp.py               # uses FakeTransport
│   │   ├── test_import.py
│   │   ├── test_policy.py
│   │   ├── test_ai.py
│   │   └── test_audit.py
│   ├── requirements.txt
│   └── sample.env.example             # FERNET_KEY only; no secrets
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Setup.tsx
│   │   │   ├── Settings.tsx           # all credential input forms
│   │   │   ├── Import.tsx
│   │   │   ├── Contacts.tsx
│   │   │   ├── Drafts.tsx
│   │   │   ├── Queue.tsx
│   │   │   ├── FollowUps.tsx
│   │   │   ├── Audit.tsx
│   │   │   └── Errors.tsx
│   │   ├── components/
│   │   │   ├── ModeLabel.tsx          # DRY-RUN / CANARY / LIVE badge
│   │   │   ├── ProviderHealth.tsx
│   │   │   ├── SenderStatus.tsx
│   │   │   └── CanaryModal.tsx
│   │   └── api/
│   │       └── client.ts              # typed fetch wrappers, no secrets stored
│   ├── package.json
│   └── vite.config.ts                 # VITE_API_URL only — no secret vars
└── docker-compose.yml
```
