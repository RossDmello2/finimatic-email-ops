# Render, Vercel, Supabase Deploy Handoff

This is the current deployment handoff for Finimatic using:

- Supabase Postgres for persistent production data
- Render Free for the FastAPI backend
- Vercel for the Vite/React frontend

Do not put Gmail, Groq, Gemini, SMTP, IMAP, Fernet, Render, Vercel, or database secrets in frontend code or repository files.

Current production links:

- Frontend: `https://finimatic-frontend.vercel.app/`
- Backend: `https://finimatic-backend.onrender.com`
- Database: Supabase project `finimatic`

## Verified Current State

Supabase project:

| Field | Value |
| --- | --- |
| Project name | `finimatic` |
| Project ref | `hpamfbjawuyztqowtrth` |
| Region | `ap-south-1` |
| API URL | `https://hpamfbjawuyztqowtrth.supabase.co` |
| Database host | `db.hpamfbjawuyztqowtrth.supabase.co` |
| Status | `ACTIVE_HEALTHY` |
| Public app tables | `18` |
| Alembic version | `0003_reply_followup_campaigns` |

GitHub repository:

```text
https://github.com/RossDmello2/finimatic-email-ops
```

Deployment files are present in the repository:

- `render.yaml`
- `vercel.json`
- `netlify.toml`
- `backend/requirements.txt` with `psycopg[binary]`
- `.github/workflows/manual-platform-deploy.yml`
- `.github/workflows/deploy-frontend-pages.yml`
- `scripts/set-deploy-secrets.ps1`
- `scripts/verify-deploy.ps1`
- `.vercelignore`

## Architecture

```text
Browser
  -> Vercel or Netlify static frontend
  -> VITE_API_URL
  -> Render FastAPI backend
  -> DATABASE_URL
  -> Supabase Postgres
```

The frontend must use only:

```text
VITE_API_URL=https://<render-backend>.onrender.com
```

The backend owns all secrets and side effects.

## Required Backend Env Vars On Render

Set these on the Render web service:

| Key | Value |
| --- | --- |
| `FERNET_KEY` | Optional for first deploy if using `render.yaml` or the GitHub Actions workflow; provide the existing stable key only when reusing encrypted settings |
| `DATABASE_URL` | Supabase Postgres Session Pooler connection string |
| `ALLOWED_ORIGINS` | Final Vercel frontend origin, comma-separated if more than one |
| `FRONTEND_URL` | `https://finimatic-frontend.vercel.app/` |
| `GMAIL_OAUTH_REDIRECT_URI` | `https://finimatic-backend.onrender.com/api/settings/gmail-api/oauth/callback` |
| `FINIMATIC_DISABLE_SCHEDULER` | `0` for normal app behavior, `1` only for diagnostics |

Generate `FERNET_KEY` locally:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Use Supabase Dashboard -> Connect -> Session Pooler for `DATABASE_URL`.

Recommended connection type for Render Free:

```text
postgres://postgres.<project-ref>:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:5432/postgres
```

The exact hostname can vary. Copy it from Supabase rather than guessing.

## Gmail API OAuth For Hosted Sending

Hosted sending should use Gmail API, not Gmail SMTP. The Settings page has a `Connect Gmail API` action that:

- builds a Google OAuth consent URL with `access_type=offline` and `prompt=consent`
- requests `https://www.googleapis.com/auth/gmail.send` and `https://www.googleapis.com/auth/gmail.metadata`
- exchanges the callback code on the Render backend
- stores the refresh token only in encrypted backend settings
- switches `email_transport` to `gmail_api`
- verifies the Gmail API provider before returning to the Vercel app

In Google Cloud Console, the OAuth client must include this exact Authorized Redirect URI:

```text
https://finimatic-backend.onrender.com/api/settings/gmail-api/oauth/callback
```

The Gmail app password remains valid only for SMTP/IMAP. It is not used by Gmail API.

## Render Backend Deploy

Use the Blueprint because `render.yaml` is already committed.

Dashboard link:

```text
https://dashboard.render.com/blueprint/new?repo=https://github.com/RossDmello2/finimatic-email-ops
```

Expected service from `render.yaml`:

| Setting | Value |
| --- | --- |
| Service name | `finimatic-backend` |
| Type | `web` |
| Runtime | `python` |
| Plan | `free` |
| Root directory | `backend` |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health check | `/api/health` |

After deploy, verify:

```powershell
Invoke-RestMethod https://<render-backend>.onrender.com/api/health
```

Expected response:

```json
{"status":"ok"}
```

## Render CLI Backend Deploy

The repository contains `scripts/create-render-backend.ps1` for a non-dashboard Render CLI path.

Prerequisites:

- `render login` has completed in the current Windows user session.
- `DATABASE_URL` is set in the current PowerShell process to the Supabase Session Pooler connection string.
- Optional: `FERNET_KEY` is set if you want to reuse existing encrypted settings.

Run:

```powershell
$env:DATABASE_URL = "<supabase session pooler url>"
.\scripts\create-render-backend.ps1
```

The script creates the `finimatic-backend` Render Free web service from the GitHub repository with the same build/start/health-check settings as `render.yaml`.

## Vercel Frontend Deploy

Import the same GitHub repository into Vercel.

Use the root repository as the project root because `vercel.json` is at the repo root and already runs commands inside `frontend`.

Expected Vercel settings from `vercel.json`:

| Setting | Value |
| --- | --- |
| Framework | `vite` |
| Install command | `cd frontend && npm ci` |
| Build command | `cd frontend && npm run build` |
| Output directory | `frontend/dist` |

Set the Vercel environment variable:

```text
VITE_API_URL=https://<render-backend>.onrender.com
```

After Vercel gives the final frontend URL, update Render:

```text
ALLOWED_ORIGINS=https://<vercel-frontend>.vercel.app
```

Then redeploy or restart the Render service.

## GitHub Pages Frontend Fallback

The repository contains `.github/workflows/deploy-frontend-pages.yml`, which builds the Vite frontend without platform secrets and deploys it to GitHub Pages.

Legacy Pages URL:

```text
https://rossdmello2.github.io/email-automation/
```

The workflow builds with:

```text
VITE_API_URL=https://finimatic-backend.onrender.com
```

This is a frontend fallback, not a backend replacement. The primary frontend remains `https://finimatic-frontend.vercel.app/`.

## GitHub User-Site Published Frontend

The `RossDmello2/RossDmello2.github.io` repository also contains an older `.github/workflows/deploy-email-automation.yml`.

That legacy workflow checks out `RossDmello2/email-automation`, builds `frontend` with:

```text
VITE_API_URL=https://finimatic-backend.onrender.com
```

and publishes the static build into:

```text
RossDmello2.github.io/email-automation/
```

Current repository evidence confirms `email-automation/index.html` exists in `RossDmello2/RossDmello2.github.io` and references the built Vite assets under `/email-automation/assets/`.

This proves an older static frontend artifact was published in the GitHub Pages repository. The active app link is the Vercel deployment.

## Netlify Frontend Deploy

A Netlify project has been created:

| Field | Value |
| --- | --- |
| Site name | `finimatic-rossdmello2` |
| Site ID | `10af2f2a-c249-4f4d-91df-508f1c147271` |
| Primary URL | `https://finimatic-rossdmello2.netlify.app` |
| Project dashboard | `https://app.netlify.com/projects/finimatic-rossdmello2` |

The repository contains `netlify.toml`:

```toml
[build]
  base = "frontend"
  command = "npm run build"
  publish = "dist"

[[redirects]]
  from = "/*"
  to = "/index.html"
  status = 200
```

Set this Netlify environment variable after the Render backend URL exists:

```text
VITE_API_URL=https://<render-backend>.onrender.com
```

Then trigger a Netlify deploy from the project dashboard or a network-capable CLI.

## GitHub Actions Fallback

The repository contains `.github/workflows/manual-platform-deploy.yml`.

This workflow is intended for a GitHub-hosted runner because this local sandbox cannot reach the required platform APIs or package registries.

Add these GitHub repository secrets before running it:

| Secret | Purpose |
| --- | --- |
| `RENDER_API_KEY` | Creates the Render web service |
| `RENDER_OWNER_ID` | Optional unless the API key can access multiple Render workspaces |
| `DATABASE_URL` | Supabase Postgres connection string |
| `FERNET_KEY` | Optional stable backend encryption key; workflow generates one if missing |
| `VERCEL_TOKEN` | Deploys the frontend to Vercel |
| `VERCEL_SCOPE` | Optional override for the Vercel team/user scope; the workflow defaults to `crce9955ce-8405s-projects` |

Important: the provided Supabase personal access token is not the same as a Postgres `DATABASE_URL`. Copy the Session Pooler connection string from the Supabase dashboard, including the database password.

The repository also contains `.vercelignore`, which keeps Vercel frontend deploy uploads scoped to `frontend/**` plus `vercel.json`.

`VERCEL_TOKEN` is optional for the workflow. If it is not configured, the workflow skips Vercel deployment after the Render backend step and points operators to the already-published GitHub Pages frontend:

```text
https://rossdmello2.github.io/email-automation/
```

For the backend deployment path, the required secrets are therefore `RENDER_API_KEY` and `DATABASE_URL`. `RENDER_OWNER_ID` is required only if the Render API key can see multiple workspaces.

If GitHub CLI is authenticated, the helper script can configure the repository secrets from process environment variables without printing the values:

```powershell
$env:RENDER_API_KEY = "<render api key>"
$env:DATABASE_URL = "<supabase session pooler url>"
$env:VERCEL_TOKEN = "<vercel token>"
.\scripts\set-deploy-secrets.ps1
```

## Post-Deploy Verification

Minimum proof before calling deployment complete:

- Render latest deploy is live.
- `GET https://<render-backend>.onrender.com/api/health` returns `{"status":"ok"}`.
- Vercel production URL, Netlify URL, or GitHub Pages fallback URL loads the dashboard.
- Browser network calls go to the Render backend URL, not `localhost:8000`.
- Backend CORS allows the exact Vercel origin.
- Supabase still shows `ACTIVE_HEALTHY`.
- `GET /api/settings` returns fingerprints/counts only, not raw keys.
- Frontend source and build contain no Gmail/Groq/Gemini secrets.
- `scripts/verify-deploy.ps1` passes against the deployed backend and frontend.

## Render Free SMTP Limitation

Render Free is acceptable for the backend/API deployment, but not for live Gmail SMTP sending.

Render Free blocks outbound SMTP ports `25`, `465`, and `587`. Browser verification showed hosted SMTP failing with `[Errno 101] Network is unreachable`, while the same Gmail app password works on localhost. This is a host egress limitation, not a bad password.

The production-compatible path is Gmail API over HTTPS OAuth calls. Leave the hosted app on:

- `Email Transport = Gmail API`
- `sender_readiness = provider_verified`
- `VITE_API_URL=https://finimatic-backend.onrender.com`

## Current Automation Blocker

This environment could not complete the live Render/Vercel deployment directly:

- Render CLI is installed at `C:\Users\rossd\AppData\Local\Programs\render-cli\render.exe`, but it is not authenticated in this session.
- Vercel CLI is installed at `C:\Users\rossd\AppData\Roaming\npm\vercel.cmd`, but it has no cached login in this session.
- Supabase CLI is installed at `C:\Users\rossd\AppData\Local\Programs\supabase-cli\supabase.exe`, but no access token is configured in the process environment.
- Direct REST calls to `api.render.com`, `api.vercel.com`, and `api.supabase.com` fail with a network-level connection error.
- Direct npm package fetches from `registry.npmjs.org` fail with a network-level connection error.
- `winget` cannot run in this non-interactive session.
- Chocolatey cannot access its remote package index due forbidden outbound socket access.
- The available Vercel connector can list projects but cannot create this project here.
- No Render MCP write tools are available in this session.
- GitHub Pages can publish the static frontend, but the app is not fully usable until the Render backend is live.

The remaining deployment action is therefore dashboard-side unless a network-capable runner or configured Render/Vercel MCP write tool becomes available.
