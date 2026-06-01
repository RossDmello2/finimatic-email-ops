param(
    [string]$ServiceName = "finimatic-backend",
    [string]$Repo = "https://github.com/RossDmello2/email-automation",
    [string]$AllowedOrigins = "https://rossdmello2.github.io,https://finimatic-rossdmello2.netlify.app,https://finimatic-frontend.vercel.app",
    [string]$Region = "singapore"
)

$ErrorActionPreference = "Stop"

function Get-EnvValue {
    param([string]$Name)
    return [Environment]::GetEnvironmentVariable($Name, "Process")
}

function New-FernetKey {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "FERNET_KEY is not set and python is unavailable to generate one."
    }

    $script = "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    return (& $python.Source -c $script).Trim()
}

$databaseUrl = Get-EnvValue "DATABASE_URL"
if ([string]::IsNullOrWhiteSpace($databaseUrl)) {
    throw "DATABASE_URL is required. Use the Supabase Session Pooler connection string."
}

$fernetKey = Get-EnvValue "FERNET_KEY"
if ([string]::IsNullOrWhiteSpace($fernetKey)) {
    $fernetKey = New-FernetKey
}

$render = Get-Command render -ErrorAction SilentlyContinue
if (-not $render) {
    $defaultRender = "C:\Users\rossd\AppData\Local\Programs\render-cli\render.exe"
    if (Test-Path -LiteralPath $defaultRender) {
        $renderPath = $defaultRender
    } else {
        throw "Render CLI was not found on PATH."
    }
} else {
    $renderPath = $render.Source
}

& $renderPath whoami --output json | Out-Null

$args = @(
    "services", "create",
    "--name", $ServiceName,
    "--type", "web_service",
    "--repo", $Repo,
    "--branch", "main",
    "--root-directory", "backend",
    "--runtime", "python",
    "--plan", "free",
    "--region", $Region,
    "--build-command", "pip install -r requirements.txt",
    "--start-command", 'uvicorn app.main:app --host 0.0.0.0 --port $PORT',
    "--health-check-path", "/api/health",
    "--env-var", "FERNET_KEY=$fernetKey",
    "--env-var", "DATABASE_URL=$databaseUrl",
    "--env-var", "ALLOWED_ORIGINS=$AllowedOrigins",
    "--env-var", "FINIMATIC_DISABLE_SCHEDULER=0",
    "--confirm",
    "--output", "json"
)

& $renderPath @args
