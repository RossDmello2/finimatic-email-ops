# Current Data Model

This page documents the current SQLAlchemy model surface for Finimatic. The source of truth is `backend/app/db/models.py`, with Alembic migrations under `backend/app/db/migrations/versions/`.

Local development defaults to SQLite:

```text
sqlite:///./finimatic.db
```

Production deployments should use durable storage. PostgreSQL is supported through SQLAlchemy when `DATABASE_URL` points at a PostgreSQL database and dependencies are installed.

## Table Groups

| Group | Tables | Purpose |
| --- | --- | --- |
| Settings | `settings`, `provider_health` | Runtime configuration, encrypted credentials, provider health snapshots. |
| Lead data | `contacts`, `import_batches`, `import_rows`, `suppressions` | Contact records, import audit, suppression state. |
| Drafting | `drafts`, `templates` | Manual and AI-assisted drafts plus reusable templates. |
| Sending | `send_queue`, `send_attempts` | Scheduled sends, policy outcomes, provider responses, idempotency. |
| Replies | `replies`, `conversation_messages` | Manual/IMAP replies and conversation thread records. |
| Follow-ups | `follow_up_sequences`, `campaign_plans` | Follow-up timing, campaign plans, stop reasons. |
| Audit | `audit_events` | Redacted event stream for settings, imports, drafts, sends, assistant actions, and errors. |
| Assistant | `agent_sessions`, `pending_email_actions` | Floating assistant memory and confirmation-bound pending sends. |

## Security-Sensitive Fields

Credentials are stored in the `settings` table through backend encryption helpers. Settings API responses should expose configured flags, counts, and fingerprints rather than raw secret values.

Common secret-bearing settings include:

- Gmail app password
- Gmail API client ID, client secret, and refresh token
- Groq keys
- Gemini keys
- Fernet key in backend runtime configuration

Do not commit local database files because they can contain encrypted but still private operational data.

## Migrations

The repository includes Alembic configuration in `backend/alembic.ini` and migration files under:

```text
backend/app/db/migrations/versions/
```

Current migration groups include:

- initial application tables
- assistant session and pending-action tables
- reply, follow-up, and campaign additions

The application also initializes metadata at startup through `backend/app/db/session.py`. Review migrations and startup initialization together before changing the schema.

## Production Notes

- Use a persistent database for real campaigns.
- Keep `FERNET_KEY` stable for an existing database.
- Back up the database before migrations or deployment changes.
- Treat local `.db`, `.sqlite`, and `.sqlite3` files as private artifacts.
- Do not use ephemeral SQLite storage for production email operations unless data loss is acceptable.
