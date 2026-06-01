# Testing Guide

Finimatic includes both safe automated checks and optional live email checks. Public contributors should use fake transport, fake AI, disposable data, and local databases by default.

Do not use private inboxes, real customer data, production API keys, or personal screenshots for routine testing. Never commit `.env`, `KEYS.md`, database files, logs, browser traces, or captured inbox evidence.

## Safe Automated Checks

Backend:

```powershell
cd backend
$env:FINIMATIC_DISABLE_SCHEDULER = "1"
$env:FINIMATIC_TRANSPORT = "fake"
$env:FINIMATIC_FAKE_AI = "1"
python -m pytest -q
```

Frontend:

```powershell
cd frontend
npm install
npm run build
```

Use `npm ci` instead of `npm install` when a lockfile is present and you want an exact dependency restore.

## Local Browser Smoke Test

Start the backend:

```powershell
cd backend
$env:FINIMATIC_DISABLE_SCHEDULER = "1"
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Start the frontend in another terminal:

```powershell
cd frontend
npm run dev
```

Then open `http://localhost:5173` and verify:

- The Setup dashboard loads.
- Settings accepts placeholder or disposable credentials only.
- `GET http://localhost:8000/api/health` returns `{"status":"ok"}`.
- The floating assistant opens without exposing secrets.
- Navigation between dashboard surfaces does not show blocking errors.

## Live Email Checks

Live SMTP, IMAP, canary, queue, and assistant-send checks are side-effecting. Run them only with explicit maintainer approval and disposable sender/recipient accounts.

Before a live check:

- Use a fresh local or disposable database.
- Use a Gmail app password created only for this test.
- Send first through canary or dry-run workflows.
- Confirm no raw Gmail password, Groq key, Gemini key, or app token appears in API responses, browser storage, console logs, screenshots, or audit payloads.
- Remove all local artifacts that contain private addresses or inbox content before committing.

## Security Regression Scan

Run a source scan before staging public changes:

```powershell
rg -n "gsk_[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|-----BEGIN (RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----" .
```

A clean scan is required for source and documentation. If the scan finds an intentionally documented placeholder, make it obvious that it is not a real credential.

## Expected Public Baseline

A public contribution is ready to review when:

- Backend tests pass or any failure is documented with the exact command and reason.
- Frontend build passes or the exact dependency/build blocker is documented.
- `git diff --check` passes.
- The staged file list excludes local databases, credentials, dependency folders, build output, and private browser evidence.
- Screenshots under `docs/assets/screenshots/` are sanitized and do not reveal private accounts or secrets.
