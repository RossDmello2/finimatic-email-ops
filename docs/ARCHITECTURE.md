# Architecture

Finimatic is a single-operator email operations app with a FastAPI backend, a React/Vite frontend, and SQLAlchemy-managed storage.

## High-Level Shape

```mermaid
flowchart LR
    User["Operator Browser"] --> Frontend["React + Vite Dashboard"]
    Frontend --> API["FastAPI REST API"]
    API --> DB["SQLAlchemy Database"]
    API --> SMTP["Gmail SMTP"]
    API --> IMAP["Gmail IMAP"]
    API --> AI["Groq / Gemini"]
    API --> Audit["Audit Events"]
```

The frontend does not talk directly to Gmail, Groq, Gemini, or the database. All privileged work goes through the backend.

## Backend Responsibilities

The backend owns:

- app startup and router mounting
- CORS configuration
- database session setup
- settings encryption and decryption
- contact lifecycle state
- import validation
- draft storage and approval
- AI provider calls
- send policy evaluation
- Gmail SMTP and IMAP calls
- follow-up scheduling
- assistant routing, tools, pending confirmations, and send execution
- audit redaction and persistence

Main entrypoint:

```text
backend/app/main.py
```

## Frontend Responsibilities

The frontend owns:

- dashboard navigation
- forms and tables
- local UI state
- API calls through `frontend/src/api/client.ts`
- floating assistant widget rendering
- local assistant message history

The frontend must not own:

- Gmail passwords
- Groq keys
- Gemini keys
- SMTP/IMAP execution
- send policy decisions
- final send authority

## Data Flow

### Settings

```mermaid
sequenceDiagram
    participant UI as Frontend Settings
    participant API as FastAPI Settings API
    participant DB as settings table
    UI->>API: POST settings with credentials
    API->>API: Encrypt secrets with Fernet
    API->>DB: Store encrypted values
    API-->>UI: Return configured flags and fingerprints only
```

### Draft And Queue

```mermaid
sequenceDiagram
    participant UI as Dashboard
    participant API as Draft API
    participant AI as Groq/Gemini
    participant DB as Database
    UI->>API: Generate draft
    API->>AI: Optional provider call
    API->>DB: Store unapproved draft
    API-->>UI: Draft for review
    UI->>API: Approve draft
    API->>DB: Mark approved and create queue entry
```

### Send

```mermaid
sequenceDiagram
    participant Worker as Queue Worker
    participant DB as Database
    participant SMTP as Gmail SMTP
    participant Audit as Audit
    Worker->>DB: Load due queue entries
    Worker->>DB: Evaluate settings, contact, suppression, reply, caps, idempotency
    alt Policy blocked
        Worker->>DB: Mark blocked/skipped
        Worker->>Audit: Write gate event
    else Policy passed
        Worker->>SMTP: Send message
        Worker->>DB: Store send attempt and conversation message
        Worker->>Audit: Write success/failure event
    end
```

### Assistant Send

```mermaid
sequenceDiagram
    participant User
    participant Widget as Floating Assistant
    participant API as Agent API
    participant DB as Database
    participant SMTP as Gmail SMTP
    User->>Widget: Ask to draft/send
    Widget->>API: POST /api/agent/chat
    API->>DB: Read bounded evidence
    API->>DB: Create pending_email_action
    API-->>Widget: Draft card and confirmation prompt
    User->>Widget: Confirm
    Widget->>API: POST /api/agent/confirm
    API->>DB: Validate session, draft hash, expiry, consumed state
    API->>SMTP: Send exact confirmed draft
    API->>DB: Mark consumed, write send/audit records
```

## Database Tables

Current tables are documented in [DATA_MODEL.md](DATA_MODEL.md). Historical schema notes live in [reference/SCHEMA.md](reference/SCHEMA.md), but current source is `backend/app/db/models.py`. The main groups are:

- settings
- contacts and imports
- drafts and templates
- send queue and send attempts
- follow-up sequences
- replies and conversation messages
- suppressions
- audit events
- provider health
- agent sessions and pending email actions

## Background Work

The backend starts periodic workers unless `FINIMATIC_DISABLE_SCHEDULER=1`:

- queue worker every 30 seconds
- follow-up worker every 300 seconds
- IMAP reply fetch through APScheduler

For tests and one-off commands, disable the scheduler.

## Security Boundaries

- Secret values are encrypted before storage.
- Settings responses return counts and fingerprints, not raw keys.
- Audit payloads are redacted.
- Assistant tools return bounded evidence envelopes.
- The pending confirmation harness protects assistant sends from replay, expiry, session mismatch, and draft mutation.
