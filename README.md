# Finimatic

[![CI](https://github.com/RossDmello2/email-automation/actions/workflows/ci.yml/badge.svg)](https://github.com/RossDmello2/email-automation/actions/workflows/ci.yml)
[![Backend](https://img.shields.io/badge/backend-FastAPI-009688)](https://fastapi.tiangolo.com/)
[![Frontend](https://img.shields.io/badge/frontend-React%20%2B%20Vite-646CFF)](https://vitejs.dev/)
[![Database](https://img.shields.io/badge/database-SQLAlchemy%20%2B%20SQLite%2FPostgreSQL-336791)](https://www.sqlalchemy.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Finimatic is a governed cold-email operations system. It helps an operator import leads, write and review AI-assisted drafts, enforce send policy, track replies, schedule follow-ups, and use a floating assistant to ask campaign questions or draft responses.

The core safety idea is simple: AI may suggest text and summarize bounded data, but backend code owns every credential, policy check, audit record, confirmation, and send action.

> Public deployment warning: this repository does not include an authentication layer. Run it locally, behind private access, or add authentication before exposing the backend with real Gmail or provider credentials.

## Preview

The screenshots below are captured from the running app with demo-safe or redacted values.

![Finimatic setup dashboard](docs/assets/screenshots/home.png)

![Finimatic assistant panel with demo-safe data](docs/assets/screenshots/main-workflow.png)

![Finimatic responsive mobile dashboard](docs/assets/screenshots/mobile.png)

## What This Project Does

- Imports contacts from manual entry, pasted text, CSV, or TXT.
- Stores Gmail, Groq, and Gemini credentials encrypted in the backend database through the Settings screen.
- Generates outreach drafts with Groq, Gemini, or manual mode.
- Keeps drafts unapproved until the operator explicitly approves them.
- Queues approved sends and checks policy gates before dispatch.
- Supports dry-run, canary, and live sender modes.
- Tracks replies through manual entry and Gmail IMAP fetch.
- Stops follow-ups when a lead replies, unsubscribes, bounces, is suppressed, or is manually paused.
- Provides conversation threads and context-aware reply generation.
- Adds a floating assistant that can answer campaign questions, read bounded thread evidence, draft replies, and require confirmation before sending.
- Writes audit events for settings, imports, drafts, queue decisions, sends, replies, follow-ups, and assistant actions.

## Safety Model

Finimatic is designed around explicit operator control:

```text
Lead data -> Draft suggestion -> Human review -> Approval -> Policy gates -> Send -> Audit -> Follow-up/reply state
```

For assistant-driven sends:

```text
User message -> Intent/slots -> Redacted evidence -> Draft -> Pending action -> Confirm -> Hash/session/expiry checks -> Send -> Audit
```

Important boundaries:

- The browser never receives Gmail app passwords, Groq keys, or Gemini keys.
- `VITE_API_URL` is the only frontend environment variable.
- Gmail/Groq/Gemini credentials are stored through backend Settings and encrypted with Fernet.
- AI output is treated as a proposal, not as authority.
- Email sending requires deterministic backend checks and, for the assistant, a valid unconsumed pending action.
- Raw email bodies and secrets are not exposed in assistant responses or audit payloads.

## Tech Stack

| Area | Stack |
| --- | --- |
| Backend | Python, FastAPI, SQLAlchemy, Pydantic, APScheduler |
| Frontend | React 18, TypeScript, Vite, TanStack Query, lucide-react, sonner |
| Database | SQLite for local development, PostgreSQL-capable SQLAlchemy URL for production |
| Email | Gmail SMTP/IMAP adapters with fake transport support for tests |
| AI | Groq and Gemini, configured through the app Settings screen |
| Tests | pytest, FastAPI TestClient, Vite/TypeScript build |

## Repository Layout

```text
.
|-- backend/
|   |-- app/
|   |   |-- agent/              # Governed floating assistant backend
|   |   |-- ai/                 # Groq/Gemini gateway and provider helpers
|   |   |-- audit/              # Redacted audit event API
|   |   |-- contacts/           # Contact CRUD and lifecycle state
|   |   |-- conversations/      # Conversation threads and reply sends
|   |   |-- db/                 # SQLAlchemy models, sessions, migrations
|   |   |-- drafts/             # Manual and AI draft workflows
|   |   |-- followups/          # Follow-up scheduling and stop checks
|   |   |-- imports/            # Lead import preview and commit
|   |   |-- replies/            # Manual/IMAP reply lifecycle
|   |   |-- send/               # SMTP adapter, queue worker, policy gates
|   |   |-- settings/           # Encrypted settings and sender verification
|   |   `-- main.py             # FastAPI app factory and router mounts
|   |-- tests/                  # Backend test suite
|   `-- sample.env.example      # Backend environment template
|-- frontend/
|   |-- src/
|   |   |-- api/client.ts       # Typed API client
|   |   |-- features/floating-assistant/
|   |   `-- App.tsx             # Single-page dashboard shell
|   |-- package.json
|   `-- .env.example
|-- docs/
|   |-- API_REFERENCE.md
|   |-- ARCHITECTURE.md
|   |-- DEPLOYMENT.md
|   `-- GETTING_STARTED.md
|-- SCHEMA.md
|-- STACK.md
|-- DATA_FLOW.md
|-- PROJECT_IMPLEMENTATION_REPORT.md
`-- DEPLOYMENT_INTELLIGENCE_REPORT.md
```

## Prerequisites

- Python 3.11 or newer
- Node.js 20 or newer
- npm
- A Gmail account with an app password if you want real SMTP/IMAP behavior
- Optional Groq and Gemini API keys for AI draft generation

The app works in manual mode without AI keys.

## Quick Start

Clone the repository:

```bash
git clone https://github.com/RossDmello2/email-automation.git
cd email-automation
```

Set up the backend:

```bash
cd backend
python -m venv .venv
```

Activate the virtual environment.

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

On macOS/Linux:

```bash
source .venv/bin/activate
```

Install backend dependencies:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Create backend environment file:

```bash
cp sample.env.example .env
```

Generate a Fernet key and put it in `backend/.env`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Start the backend:

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal, set up the frontend:

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Open the app:

[http://localhost:5173](http://localhost:5173)

The backend health endpoint should return `{"status":"ok"}`:

[http://localhost:8000/api/health](http://localhost:8000/api/health)

## First Run Checklist

1. Open the dashboard.
2. Go to Settings.
3. Add Gmail sender email and Gmail app password.
4. Add optional Groq/Gemini keys if you want AI drafts.
5. Save settings.
6. Verify SMTP.
7. Send a canary email before enabling real live sends.
8. Import or create contacts.
9. Generate or write drafts.
10. Approve drafts only after review.
11. Process queue or let the worker process due entries.
12. Use Replies, Conversations, and the assistant to manage responses.

## Common Commands

Backend:

```bash
cd backend
python -m pytest -q
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev
npm run build
npm run preview
```

## Environment Variables

Backend variables:

| Variable | Required | Purpose |
| --- | --- | --- |
| `FERNET_KEY` | Yes | Encrypts stored Gmail/Groq/Gemini secrets. |
| `DATABASE_URL` | No | Defaults to `sqlite:///./finimatic.db`. |
| `ALLOWED_ORIGINS` | No | Comma-separated CORS allow-list. |
| `PORT` | No | Runtime port used by some hosts. |
| `FINIMATIC_DISABLE_SCHEDULER` | No | Set to `1` for tests or one-off commands. |
| `FINIMATIC_TRANSPORT` | No | Set to `fake` for test transport. |
| `FINIMATIC_FAKE_AI` | No | Set to `1` for deterministic fake AI in tests. |

Frontend variables:

| Variable | Required | Purpose |
| --- | --- | --- |
| `VITE_API_URL` | Yes in production | Backend API base URL. Example: `https://your-api.onrender.com`. |

Do not add Gmail, Groq, or Gemini keys to frontend environment variables.

## API Overview

The backend exposes REST APIs under `/api` for:

- health
- settings
- provider health
- canary sends
- imports
- contacts
- drafts
- templates
- campaigns
- queue
- follow-ups
- suppressions
- replies
- conversations
- auto-reply review
- audit events
- floating assistant chat/confirm/cancel

See [docs/API_REFERENCE.md](docs/API_REFERENCE.md) for the route map.

## Database

Local development uses SQLite by default. The SQLAlchemy models are in `backend/app/db/models.py`; the documented schema is in [SCHEMA.md](SCHEMA.md).

For production, use a persistent database. The backend requirements include `psycopg[binary]` for PostgreSQL-backed deployments.

## Deployment

The most common split deployment is:

- Render for the FastAPI backend.
- Vercel for the Vite frontend.
- A persistent database for production data.

Read [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) before deploying. The deployment guide calls out required environment variables, CORS, scheduler behavior, and production caveats.

## Project Documentation

- [Getting Started](docs/GETTING_STARTED.md)
- [Architecture](docs/ARCHITECTURE.md)
- [API Reference](docs/API_REFERENCE.md)
- [Deployment Guide](docs/DEPLOYMENT.md)
- [Database Schema](SCHEMA.md)
- [Tech Stack](STACK.md)
- [Testing Guide](TESTING.md)
- [Data Flow](DATA_FLOW.md)
- [Implementation Report](PROJECT_IMPLEMENTATION_REPORT.md)
- [Deployment Intelligence Report](DEPLOYMENT_INTELLIGENCE_REPORT.md) historical deployment audit with current caveats

## Security Notes

- Never commit `.env`, database files, logs, or `KEYS.md`.
- Use the Settings UI for Gmail/Groq/Gemini credentials.
- Keep `FERNET_KEY` stable for an existing database; changing it makes encrypted settings unreadable.
- Do not expose this dashboard publicly without adding authentication and access control.
- Treat live email sending as a side-effecting operation. Test with fake/dry-run/canary modes first.

See [SECURITY.md](SECURITY.md) for reporting and operational guidance.

## Current Status

READY WITH GAPS.

This repository contains a working local-first email operations app with backend tests and a production buildable frontend. It is not a hosted SaaS template out of the box.

Known deployment considerations:

- Authentication is not included. Put the app behind private access or add auth before public use.
- SQLite is fine for local development, but production should use durable storage.
- Gmail app passwords and provider API keys must be configured through Settings, not committed.
- Public screenshots are sanitized examples, not live Gmail delivery guarantees.

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), run the backend tests and frontend build, and keep all secret-handling rules intact.

## License

This project is released under the [MIT License](LICENSE).
