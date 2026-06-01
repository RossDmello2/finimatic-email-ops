# Getting Started

This guide is for a beginner opening the repository for the first time.

## 1. Understand The App

Finimatic is not just a mail sender. It is an operator dashboard for safe cold-email operations:

1. Configure sender and provider settings.
2. Import contacts.
3. Generate or write drafts.
4. Review and approve drafts.
5. Process a policy-checked send queue.
6. Track replies, suppressions, follow-ups, conversations, and audit events.
7. Use the assistant for campaign questions and confirmation-bound drafting/sending.

The backend is the authority. The frontend is an operator interface.

## 2. Install Backend

```bash
cd backend
python -m venv .venv
```

Activate the environment.

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Create `backend/.env`:

```bash
cp sample.env.example .env
```

Generate `FERNET_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste that value into `backend/.env`.

Start the backend:

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Check:

```bash
curl http://127.0.0.1:8000/api/health
```

Expected:

```json
{"status":"ok"}
```

## 3. Install Frontend

Open another terminal:

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Open:

```text
http://localhost:5173
```

## 4. Configure The App Safely

In the dashboard:

1. Open Settings.
2. Keep dry-run enabled while learning the workflow.
3. Use fake or disposable contact data.
4. Add Gmail sender credentials only when you are ready to test email.
5. Optionally add Groq and Gemini keys.
6. Save settings.
7. Verify email transport.
8. Send a canary email before live sends.

Credentials are sent to the backend and encrypted. The frontend does not store provider keys.

## 5. Try A Safe Workflow

Recommended first workflow:

1. Keep dry-run enabled.
2. Create a manual test contact.
3. Generate or write a draft.
4. Approve the draft.
5. Process the queue.
6. Review Audit Logs.
7. Add a manual reply.
8. Open Conversations.
9. Ask the floating assistant: `who replied today?`

Only move toward live sending after you understand dry-run, canary behavior, suppressions, caps, and audit logs.

## 6. Learn More

- [Architecture](ARCHITECTURE.md)
- [API Reference](API_REFERENCE.md)
- [Current Data Model](DATA_MODEL.md)
- [Troubleshooting](TROUBLESHOOTING.md)
- [Testing Guide](TESTING.md)

## 7. Run Verification

Backend:

```bash
cd backend
python -m pytest -q
```

Frontend:

```bash
cd frontend
npm run build
```

## 8. Common Problems

### Backend cannot decrypt settings

The `FERNET_KEY` changed after credentials were saved. Use the original key for the existing database or create a fresh local database.

### Frontend cannot reach backend

Check `frontend/.env`:

```text
VITE_API_URL=http://localhost:8000
```

Also check backend `ALLOWED_ORIGINS`.

### Live email did not send

Check:

- SMTP verified.
- Canary verified.
- Dry-run disabled only when you intend live behavior.
- Contact is not suppressed, bounced, paused, unsubscribed, or already replied.
- Daily/hourly caps and send window allow sending.

### AI draft generation fails

Manual drafting still works. Check provider keys in Settings and provider health.
