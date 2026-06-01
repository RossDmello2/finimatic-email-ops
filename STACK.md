# Finimatic Tech Stack

This file records the source-backed runtime stack for the repository. It should stay aligned with `backend/requirements.txt`, `frontend/package.json`, and the environment examples.

## Backend

| Component | Library / Tool |
| --- | --- |
| Web framework | FastAPI |
| ASGI server | Uvicorn |
| ORM | SQLAlchemy 2.x |
| Migrations | Alembic |
| Local database | SQLite via `sqlite:///./finimatic.db` |
| Production database option | PostgreSQL through SQLAlchemy with `psycopg[binary]` |
| Validation and settings | Pydantic v2 and pydantic-settings |
| Secret encryption | `cryptography` Fernet |
| Email transport | Gmail SMTP/IMAP adapters, plus fake transport for tests |
| Background jobs | APScheduler |
| AI providers | Groq SDK and Google Gen AI SDK |
| HTTP client | httpx |
| Tests | pytest and pytest-asyncio |

Backend install:

```powershell
cd backend
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Backend run:

```powershell
cd backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Backend test:

```powershell
cd backend
$env:FINIMATIC_DISABLE_SCHEDULER = "1"
$env:FINIMATIC_TRANSPORT = "fake"
$env:FINIMATIC_FAKE_AI = "1"
python -m pytest -q
```

## Frontend

| Component | Library / Tool |
| --- | --- |
| Framework | React 18 |
| Language | TypeScript |
| Build tool | Vite 5 |
| Data fetching | TanStack Query |
| Routing | React Router |
| Icons | lucide-react |
| Toasts | sonner |
| Styling | Tailwind/PostCSS plus local CSS |

Frontend install:

```powershell
cd frontend
npm install
```

Frontend run:

```powershell
cd frontend
npm run dev
```

Frontend build:

```powershell
cd frontend
npm run build
```

## Environment

Backend environment template: `backend/sample.env.example`

```bash
FERNET_KEY=__generate_with_command_above__
PORT=8000
ALLOWED_ORIGINS=http://localhost:5173
DATABASE_URL=sqlite:///./finimatic.db
```

Frontend environment template: `frontend/.env.example`

```bash
VITE_API_URL=http://localhost:8000
```

Gmail, Groq, and Gemini credentials are not frontend or source-control environment variables. They are entered through the Settings UI and stored encrypted by the backend.

## Security Boundaries

- `VITE_API_URL` is the only frontend environment variable.
- Do not commit `.env`, local databases, `KEYS.md`, logs, dependency folders, or build output.
- Keep `FERNET_KEY` stable for any existing database because it decrypts saved provider credentials.
- Do not expose the backend publicly with real credentials until authentication or private network access is added.
