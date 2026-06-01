# Contributing

Thanks for taking the time to improve Finimatic.

## Start Here

Read these first:

- [README.md](README.md)
- [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/DATA_MODEL.md](docs/DATA_MODEL.md)
- [docs/API_REFERENCE.md](docs/API_REFERENCE.md)
- [docs/TESTING.md](docs/TESTING.md)
- [docs/reference/](docs/reference/) for historical specs and audits

## Development Setup

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp sample.env.example .env
python -m pytest -q
```

Windows PowerShell activation:

```powershell
.\.venv\Scripts\Activate.ps1
```

Frontend:

```bash
cd frontend
npm install
cp .env.example .env
npm run build
```

## Contribution Rules

- Do not commit secrets, database files, logs, build output, or `KEYS.md`.
- Do not add Gmail, Groq, or Gemini keys to frontend code or Vite env variables.
- Keep `VITE_API_URL` as the only frontend environment variable.
- Preserve the backend-owned send policy and confirmation harness.
- Do not make AI output authoritative for side effects.
- Keep assistant data bounded and redacted.
- Add or update tests for behavior changes.
- Keep documentation aligned with actual code paths.

## Backend Checks

```bash
cd backend
python -m pytest -q
```

## Frontend Checks

```bash
cd frontend
npm run build
```

## Pull Request Checklist

- The change is scoped and described clearly.
- Backend tests pass if backend code changed.
- Frontend build passes if frontend code changed.
- Documentation was updated when behavior changed.
- No raw keys, passwords, tokens, or database files were added.
- Email sends remain gated by policy and confirmation where applicable.
- UI changes include sanitized screenshots when they affect visible behavior.

## Security-Sensitive Changes

Open a focused PR and explain:

- what data is read
- what data is written
- whether credentials are touched
- whether email can be sent
- what tests prove the behavior

Changes that weaken credential handling, audit redaction, send policy, or assistant confirmation should not be merged without careful review.
