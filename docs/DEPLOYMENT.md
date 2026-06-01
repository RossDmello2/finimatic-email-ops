# Deployment Guide

This project is easiest to deploy as two services:

- FastAPI backend on Render or a similar Python host.
- Vite frontend on Vercel or Netlify.

Do not deploy the frontend as the only service. The backend is required for the database, credentials, Gmail, AI providers, policy gates, and assistant confirmation harness.

## Production Readiness Checklist

Before exposing this app beyond private use:

- Add authentication or deploy behind private access.
- Use a persistent production database.
- Set a stable `FERNET_KEY`.
- Keep Gmail/Groq/Gemini credentials out of repository files.
- Configure CORS to allow only the deployed frontend domain.
- Confirm email sending in dry-run and canary mode before live sending.
- Review audit logs after test sends.
- Verify that the backend should run scheduled queue, follow-up, and IMAP work before setting `FINIMATIC_DISABLE_SCHEDULER=0`.

## Backend On Render

Recommended service type:

```text
Web Service
```

Root directory:

```text
backend
```

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Required environment variables:

| Variable | Example | Notes |
| --- | --- | --- |
| `FERNET_KEY` | generated Fernet key | Must remain stable for encrypted settings. |
| `DATABASE_URL` | `sqlite:///./finimatic.db` or production DB URL | Use persistent storage for production. |
| `ALLOWED_ORIGINS` | `https://your-frontend.vercel.app` | Must match the Vercel URL. |
| `FINIMATIC_DISABLE_SCHEDULER` | `0` | Set `1` only for no-worker deployments or diagnostics. |

Optional runtime variables:

| Variable | Notes |
| --- | --- |
| `GROQ_MODEL_FAST` | Optional Groq model override for fast classification paths. |
| `GROQ_TIMEOUT_S` | Optional Groq timeout override. |

For local fake/test behavior only:

| Variable | Value |
| --- | --- |
| `FINIMATIC_TRANSPORT` | `fake` |
| `FINIMATIC_FAKE_AI` | `1` |

Do not set those fake variables for live production.

## Database Notes

The app defaults to SQLite:

```text
sqlite:///./finimatic.db
```

SQLite is fine for local testing. In production, use persistent storage. If deploying with PostgreSQL, verify that the backend environment has a PostgreSQL SQLAlchemy driver installed before using a PostgreSQL `DATABASE_URL`.

## Frontend On Vercel

Root directory:

```text
frontend
```

Install command:

```bash
npm ci
```

Build command:

```bash
npm run build
```

Output directory:

```text
dist
```

Environment variable:

```text
VITE_API_URL=https://your-backend-service.onrender.com
```

Only `VITE_API_URL` belongs in Vercel. Never add Gmail, Groq, Gemini, SMTP, IMAP, or Fernet secrets to frontend environment variables.

## CORS

The backend reads `ALLOWED_ORIGINS`.

For local development:

```text
ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

For production:

```text
ALLOWED_ORIGINS=https://your-frontend.vercel.app
```

If the frontend loads but API requests fail, check the browser console and the backend CORS allow-list.

## Gmail Setup

Use the dashboard Settings screen for:

- Gmail sender email
- Gmail app password
- report recipient
- Groq keys
- Gemini keys

Those values are encrypted in the backend database. They should not appear in `.env`, GitHub Actions, Vercel, or frontend code.

## Smoke Test After Deployment

1. Open the frontend URL.
2. Confirm the dashboard loads.
3. Confirm `GET /api/health` returns `{"status":"ok"}` on the backend URL.
4. Save Settings without exposing secrets in responses.
5. Verify SMTP.
6. Send a canary email.
7. Create a test contact.
8. Generate or write a draft.
9. Approve in dry-run first.
10. Review Audit Logs.

## Known Caveats

- The repository does not include an authentication layer.
- SQLite files on ephemeral hosts may be lost between deploys unless persistent disk is configured.
- Production PostgreSQL needs the appropriate Python driver installed.
- Gmail app passwords can be revoked or rate-limited by Google.
- Public screenshots committed to this repo are sanitized local examples, not live delivery or deployment checks.
- Background workers can trigger email or IMAP side effects after deployment if credentials and settings enable them.
