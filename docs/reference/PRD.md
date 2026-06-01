# Finimatic — Product Requirements Document

## Product Goal
A configurable cold-email operations system. A non-technical operator
configures everything from the dashboard UI — no .env editing, no terminal.
The backend securely stores credentials and executes all sending logic.

---

## Core Workflow (in order)

1. Operator opens Settings → pastes Gmail user + app password → system verifies SMTP
2. Operator pastes Groq keys + Gemini keys (one per line) → pooled internally
3. Operator imports leads (CSV / TXT / paste / manual entry)
4. System generates personalized draft (AI-assisted or manual)
5. Operator reviews + explicitly approves draft
6. System sends from authenticated Gmail account (only after all policy gates pass)
7. System tracks recipient lifecycle and fires follow-ups per policy
8. Operator sees full audit trail, errors, and provider health on dashboard

---

## Recipient Lifecycle States (state machine — transitions only forward or to blocked)

```
imported
  → needs_review
  → draft_needed
  → draft_ready
  → approved
  → queued
  → sent
  → follow_up_due
  → follow_up_stopped
  → complete

  (from any state) → replied
  (from any state) → bounced
  (from any state) → unsubscribed
  (from any state) → suppressed
  (from any state) → manually_paused
  (from any state) → blocked_by_policy
```

Every transition MUST produce an audit_event record with stable reason_code.

---

## Settings (all stored in backend DB, configurable only via Settings UI)

| Setting key             | Type            | Description                                          |
|-------------------------|-----------------|------------------------------------------------------|
| gmail_user              | string          | Authenticated sender address                         |
| gmail_app_password      | string          | Fernet-encrypted; NEVER returned by API              |
| groq_keys               | string[]        | JSON array; each key Fernet-encrypted                |
| gemini_keys             | string[]        | JSON array; each key Fernet-encrypted                |
| daily_send_cap          | int             | Max sends per 24-hour window (default: 50)           |
| hourly_send_cap         | int             | Max sends per hour (default: 10)                     |
| send_delay_s            | int             | Seconds between sends (default: 60)                  |
| followup_interval_days  | int             | Days between follow-ups (default: 3)                 |
| max_followups_per_lead  | int             | Max follow-up touches (default: 2)                   |
| dry_run                 | bool            | If true, queue runs but SMTP never called            |
| canary_verified         | bool            | Set by system after successful canary send           |
| report_recipient        | string          | Canary test destination                              |

---

## AI Integration Rules (non-negotiable)

- AI MAY: suggest draft text, rewrite/humanize/shorten/lengthen, flag risks, summarize lead evidence, suggest subject variants
- AI MUST NOT: approve drafts, trigger sends, suppress contacts, override policy gates, decide follow-up eligibility, or mark recipients as stopped
- Groq/Gemini unavailable (rate-limited, unconfigured, malformed output) → manual workflow continues unblocked
- Malformed AI output → log safe error, fall back to empty draft, never crash pipeline

---

## Policy Gates (ALL must pass before any live send attempt)

```
sender_verified       → smtp_verified or canary_verified in settings
canary_passed         → canary_verified = true
draft_approved        → draft.approved = true AND draft.approved_at IS NOT NULL
no_suppression        → email NOT in suppressions table
no_bounce             → no bounce record for this email
no_reply              → no replied/unsubscribe record for this contact
no_manual_pause       → contact.status != manually_paused
cap_ok                → daily + hourly counts within limits
window_ok             → send_delay_s elapsed since last send
idempotency_ok        → no existing sent/pending record for same (contact_id, sequence_num)
```

Gate failure → stable `reason_code` stored in send_queue.policy_block_reasons (JSON array) + audit event.

---

## Follow-up Stop Conditions (checked immediately before every follow-up dispatch)

Any of these blocks the follow-up:
- contact status = replied
- contact status = unsubscribed OR suppression record exists
- bounce record exists
- complaint record exists
- contact status = manually_paused
- cap/window policy block
- sender health = failed
- idempotency duplicate detected
- max_followups_per_lead reached

---

## Dashboard Surfaces (required, visible to non-technical operator)

| Surface         | What it shows                                                       |
|-----------------|---------------------------------------------------------------------|
| Setup           | Sender status, SMTP verified badge, canary status, mode label       |
| Provider Health | Groq pool status, Gemini pool status, Gmail adapter status          |
| Import          | Upload / paste / manual entry + row-level result preview            |
| Contacts        | All contacts with status, last action, next action                  |
| Drafts          | Draft list, approve button, AI rewrite panel, warnings              |
| Queue           | Pending sends, gate check results, block reasons                    |
| Follow-ups      | Sequence state, due dates, stop reasons                             |
| Replies/Stops   | Replies, unsubscribes, bounces, manual stops                        |
| Suppressions    | Full suppression list, add/remove                                   |
| Audit Logs      | Timestamped event stream, filterable by type                        |
| Errors          | Redacted error log with actionable descriptions                     |
| Settings        | All configurable fields (see Settings table above)                  |

**Mode label** (always visible in header): `DRY-RUN` / `CANARY` / `LIVE`

---

## Canary Send Behavior

1. Operator clicks "Test Send" in Setup surface
2. Frontend shows confirmation modal with exact recipient address
3. On confirm: POST /api/canary/send
4. Backend: verify sender configured → SMTP auth → generate unique nonce (uuid + timestamp) → send single email to report_recipient
5. Store: provider attempt record, message_id if available, idempotency_key, audit event
6. Retry: idempotency key blocks any duplicate canary from sending second email
7. Frontend: shows nonce, timestamp, sender identity, delivery status
8. Live sending to imported leads only enabled AFTER canary_verified = true

---

## Phase Scope for V1 (MVP)

| Capability          | V1 Status          | Notes                                   |
|---------------------|--------------------|-----------------------------------------|
| Gmail SMTP send     | ✓ implemented      | Canary first, then leads                |
| Settings UI         | ✓ implemented      | All creds via frontend form             |
| CSV/TXT/manual import | ✓ implemented    |                                         |
| AI draft (Groq)     | ✓ implemented      | Suggestion only                         |
| AI draft (Gemini)   | ✓ implemented      | Suggestion only                         |
| Send queue + policy | ✓ implemented      |                                         |
| Follow-up sequences | ✓ implemented      | Manual stop marking if no reply sync    |
| Reply sync (IMAP)   | ✗ deferred V2      | Manual stop marking in V1               |
| Governed campaign scheduler | ✗ V2     |                                         |
| Lead scraping       | ✗ never in this build |                                      |
