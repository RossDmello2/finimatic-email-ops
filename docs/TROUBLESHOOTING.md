# Troubleshooting

This guide covers common local development and deployment problems.

## Backend Will Not Start

Check that dependencies are installed from `backend/requirements.txt`:

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

If startup creates or rewrites `backend/.env`, set a stable `FERNET_KEY` yourself. Changing `FERNET_KEY` after credentials are saved makes existing encrypted settings unreadable.

## Frontend Cannot Reach Backend

Check `frontend/.env`:

```text
VITE_API_URL=http://localhost:8000
```

Check the backend health endpoint:

```bash
curl http://127.0.0.1:8000/api/health
```

If the backend is running but browser requests fail, check `ALLOWED_ORIGINS` in `backend/.env`.

## Tests Trigger Background Work

Set scheduler and side-effect guards before automated tests:

```powershell
$env:FINIMATIC_DISABLE_SCHEDULER = "1"
$env:FINIMATIC_TRANSPORT = "fake"
$env:FINIMATIC_FAKE_AI = "1"
python -m pytest -q
```

On macOS/Linux:

```bash
FINIMATIC_DISABLE_SCHEDULER=1 FINIMATIC_TRANSPORT=fake FINIMATIC_FAKE_AI=1 python -m pytest -q
```

## Email Verification Fails

Check:

- the sender email is correct
- the selected transport is SMTP or Gmail API as intended
- the Gmail app password or OAuth values are configured in Settings
- Gmail has not revoked the credential
- the backend can reach Gmail from the current network

Never paste real credentials into a public issue.

## Live Send Does Not Execute

Check:

- dry-run is disabled only when live sending is intended
- canary verification is complete
- the contact is not suppressed, bounced, paused, unsubscribed, or already replied
- daily and hourly caps are not exhausted
- the send window and timezone allow sending now
- the draft was explicitly approved
- audit logs show the policy decision

## Assistant Send Is Rejected

Assistant sends require a pending action. Rejections are expected when:

- the action expired
- the action was already consumed
- the session token does not match
- the draft changed after the pending action was created
- the capability did not require or pass confirmation

This is part of the safety model.

## Deployment Works But API Calls Fail

Check:

- frontend `VITE_API_URL` points at the backend origin
- backend `ALLOWED_ORIGINS` includes the deployed frontend origin
- backend `/api/health` is reachable
- backend has a stable `FERNET_KEY`
- production database storage is durable
- scheduler settings match the deployment plan

## Before Opening An Issue

Include:

- operating system
- Python version
- Node version
- backend command
- frontend command
- whether you used fake, dry-run, canary, or live transport
- redacted logs or screenshots

Do not include `.env`, `KEYS.md`, raw database files, inbox contents, or credentials.
