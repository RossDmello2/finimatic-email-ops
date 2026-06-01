# Finimatic — Database Schema

Engine: SQLite (dev) / PostgreSQL (prod)
ORM: SQLAlchemy 2.x with Alembic migrations
All tables use `id` as UUID primary key (string).

---

## settings

```sql
CREATE TABLE settings (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    key         TEXT NOT NULL UNIQUE,
    value       TEXT,               -- Fernet-encrypted for secrets
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Known keys (seeded on first run):
`gmail_user`, `gmail_app_password`, `groq_keys`, `gemini_keys`,
`daily_send_cap`, `hourly_send_cap`, `send_delay_s`,
`followup_interval_days`, `max_followups_per_lead`,
`dry_run`, `canary_verified`, `report_recipient`

---

## contacts

```sql
CREATE TABLE contacts (
    id                  TEXT PRIMARY KEY,
    email               TEXT NOT NULL UNIQUE,
    creator_name        TEXT,
    business_name       TEXT,
    website_url         TEXT,
    source              TEXT NOT NULL,           -- 'csv_import' | 'txt_import' | 'paste' | 'manual'
    provenance          TEXT,                    -- filename or description
    notes               TEXT,
    personalization     TEXT,                    -- evidence for AI prompt
    lead_category       TEXT,
    custom_fields       TEXT,                    -- JSON blob
    status              TEXT NOT NULL DEFAULT 'imported',
    import_batch_id     TEXT REFERENCES import_batches(id),
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Valid `status` values (enforced in application layer):
`imported`, `needs_review`, `draft_needed`, `draft_ready`, `approved`,
`queued`, `sent`, `replied`, `bounced`, `unsubscribed`, `suppressed`,
`manually_paused`, `blocked_by_policy`, `follow_up_due`,
`follow_up_stopped`, `complete`

---

## import_batches

```sql
CREATE TABLE import_batches (
    id          TEXT PRIMARY KEY,
    filename    TEXT,
    format      TEXT NOT NULL,   -- 'csv' | 'txt' | 'paste' | 'manual'
    total       INTEGER NOT NULL DEFAULT 0,
    accepted    INTEGER NOT NULL DEFAULT 0,
    rejected    INTEGER NOT NULL DEFAULT 0,
    duplicate   INTEGER NOT NULL DEFAULT 0,
    suppressed  INTEGER NOT NULL DEFAULT 0,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

---

## import_rows

```sql
CREATE TABLE import_rows (
    id          TEXT PRIMARY KEY,
    batch_id    TEXT NOT NULL REFERENCES import_batches(id),
    row_num     INTEGER NOT NULL,
    raw_data    TEXT,
    email       TEXT,
    status      TEXT NOT NULL,   -- 'accepted'|'invalid_email'|'duplicate'|'suppressed'|'missing_field'|'parse_error'|'manual_review'
    reason      TEXT,
    contact_id  TEXT REFERENCES contacts(id)
);
```

---

## drafts

```sql
CREATE TABLE drafts (
    id          TEXT PRIMARY KEY,
    contact_id  TEXT NOT NULL REFERENCES contacts(id),
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    ai_provider TEXT,            -- 'groq' | 'gemini' | 'manual' | NULL
    ai_model    TEXT,
    warnings    TEXT,            -- JSON array of warning strings
    approved    BOOLEAN NOT NULL DEFAULT FALSE,
    approved_at DATETIME,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

---

## send_queue

```sql
CREATE TABLE send_queue (
    id                  TEXT PRIMARY KEY,
    contact_id          TEXT NOT NULL REFERENCES contacts(id),
    draft_id            TEXT NOT NULL REFERENCES drafts(id),
    sequence_num        INTEGER NOT NULL DEFAULT 1,
    scheduled_at        DATETIME NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',  -- 'pending'|'sent'|'failed'|'blocked'|'skipped'|'cancelled'
    idempotency_key     TEXT NOT NULL UNIQUE,             -- sha256(contact_id + sequence_num + draft_id)
    policy_block_reasons TEXT,                            -- JSON array of reason_code strings
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(contact_id, sequence_num)
);
```

---

## send_attempts

```sql
CREATE TABLE send_attempts (
    id              TEXT PRIMARY KEY,
    queue_id        TEXT NOT NULL REFERENCES send_queue(id),
    contact_id      TEXT NOT NULL,
    draft_id        TEXT NOT NULL,
    provider_msg_id TEXT,
    smtp_response   TEXT,
    status          TEXT NOT NULL,    -- 'success' | 'failed' | 'blocked_dry_run'
    sender_identity TEXT NOT NULL,
    sent_at         DATETIME,
    error_code      TEXT,
    error_detail    TEXT              -- redacted
);
```

---

## follow_up_sequences

```sql
CREATE TABLE follow_up_sequences (
    id           TEXT PRIMARY KEY,
    contact_id   TEXT NOT NULL REFERENCES contacts(id),
    sequence_num INTEGER NOT NULL,
    due_at       DATETIME NOT NULL,
    draft_id     TEXT REFERENCES drafts(id),
    status       TEXT NOT NULL DEFAULT 'due',  -- 'due'|'sent'|'stopped'|'skipped'
    stop_reason  TEXT,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(contact_id, sequence_num)
);
```

---

## suppressions

```sql
CREATE TABLE suppressions (
    id         TEXT PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE,
    reason     TEXT NOT NULL,   -- 'unsubscribe'|'bounce'|'complaint'|'manual'|'import'
    source     TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

---

## replies

```sql
CREATE TABLE replies (
    id              TEXT PRIMARY KEY,
    contact_id      TEXT NOT NULL REFERENCES contacts(id),
    received_at     DATETIME NOT NULL,
    classified_as   TEXT NOT NULL,   -- 'reply'|'unsubscribe'|'bounce'|'auto_reply'|'complaint'|'unknown'
    raw_summary     TEXT,            -- NEVER store full email body; store safe classification summary
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

---

## audit_events

```sql
CREATE TABLE audit_events (
    id          TEXT PRIMARY KEY,
    event_type  TEXT NOT NULL,       -- see Event Types below
    entity_type TEXT,
    entity_id   TEXT,
    actor       TEXT NOT NULL DEFAULT 'system',
    payload     TEXT,                -- JSON, secrets redacted
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_audit_entity ON audit_events(entity_type, entity_id);
CREATE INDEX idx_audit_type ON audit_events(event_type, created_at);
```

**Required event_types** (application must emit ALL of these):
```
settings.updated          sender.smtp_verified       sender.smtp_failed
canary.attempt            canary.success             canary.duplicate_blocked
import.preview            import.committed           import.row_rejected
suppression.added         suppression.matched
draft.created             draft.edited               draft.ai_generated
draft.ai_failed           draft.approved
queue.entry_created       queue.policy_evaluated     queue.gate_blocked
send.attempt              send.success               send.failed
send.dry_run_blocked      send.duplicate_blocked
followup.due_calculated   followup.dispatched        followup.stopped
reply.received            reply.classified
provider.health_changed   ai.rate_limited            ai.keys_exhausted
```

---

## provider_health

```sql
CREATE TABLE provider_health (
    id           TEXT PRIMARY KEY,
    provider     TEXT NOT NULL UNIQUE,  -- 'groq' | 'gemini' | 'gmail'
    status       TEXT NOT NULL,         -- 'ok' | 'degraded' | 'failed' | 'unknown'
    last_checked DATETIME,
    error_code   TEXT,
    details      TEXT                   -- redacted human-readable note
);
```

---

## Key Design Rules

1. No raw secrets ever stored in `payload` column of audit_events.
2. `gmail_app_password` and all key values encrypted with Fernet before INSERT.
3. Fernet key loaded from `FERNET_KEY` env var at startup; auto-generated and logged once if missing.
4. `suppression` check happens at import commit time AND at every pre-send gate.
5. `idempotency_key` in send_queue is the enforced duplicate-send guard — no second attempt for same key.
