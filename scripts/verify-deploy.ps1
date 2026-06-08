param(
    [Parameter(Mandatory = $true)]
    [string]$BackendUrl,

    [Parameter(Mandatory = $false)]
    [string]$FrontendUrl,

    [Parameter(Mandatory = $false)]
    [string]$SupabaseUrl = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Normalize-Origin {
    param([string]$Url)
    return $Url.Trim().TrimEnd("/")
}

function Add-Check {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Detail
    )

    $script:Results += [pscustomobject]@{
        check = $Name
        passed = $Passed
        detail = $Detail
    }
}

$Results = @()
$Backend = Normalize-Origin $BackendUrl
$Frontend = if ($FrontendUrl) { Normalize-Origin $FrontendUrl } else { "" }
$Supabase = if ($SupabaseUrl) { Normalize-Origin $SupabaseUrl } else { "" }

try {
    $health = Invoke-RestMethod -Method Get -Uri "$Backend/api/health" -TimeoutSec 20
    Add-Check "backend_health" ($health.status -eq "ok") "GET $Backend/api/health returned status=$($health.status)"
} catch {
    Add-Check "backend_health" $false $_.Exception.Message
}

try {
    $settingsResponse = Invoke-WebRequest -Method Get -Uri "$Backend/api/settings" -TimeoutSec 20 -SkipHttpErrorCheck
    $settingsProtected = $settingsResponse.StatusCode -in @(401, 403)
    Add-Check "settings_requires_authentication" $settingsProtected "GET /api/settings returned HTTP $($settingsResponse.StatusCode)"
} catch {
    $status = $null
    if ($_.Exception.Response) {
        $status = [int]$_.Exception.Response.StatusCode
    }
    Add-Check "settings_requires_authentication" ($status -in @(401, 403)) "GET /api/settings returned HTTP $status"
}

if ($Frontend) {
    try {
        $front = Invoke-WebRequest -Method Get -Uri $Frontend -TimeoutSec 20
        $hasRoot = ([string]$front.Content) -match 'id="root"'
        Add-Check "frontend_loads" (($front.StatusCode -ge 200) -and ($front.StatusCode -lt 400) -and $hasRoot) "GET $Frontend returned HTTP $($front.StatusCode)"
    } catch {
        Add-Check "frontend_loads" $false $_.Exception.Message
    }

    try {
        $proxiedHealth = Invoke-RestMethod -Method Get -Uri "$Frontend/api/health" -TimeoutSec 20
        Add-Check "frontend_api_proxy" ($proxiedHealth.status -eq "ok") "GET $Frontend/api/health returned status=$($proxiedHealth.status)"
    } catch {
        Add-Check "frontend_api_proxy" $false $_.Exception.Message
    }
} else {
    Add-Check "frontend_loads" $false "Skipped: pass -FrontendUrl after Vercel deploy"
    Add-Check "frontend_api_proxy" $false "Skipped: pass -FrontendUrl after Vercel deploy"
}

if ($Supabase) {
    try {
        $supabaseRest = Invoke-WebRequest -Method Get -Uri "$Supabase/rest/v1/" -TimeoutSec 20 -SkipHttpErrorCheck
        Add-Check "supabase_api_reachable" ($supabaseRest.StatusCode -lt 500) "GET $Supabase/rest/v1/ returned HTTP $($supabaseRest.StatusCode)"
    } catch {
        Add-Check "supabase_api_reachable" $false $_.Exception.Message
    }
}

$Results | Format-Table -AutoSize

$failed = @($Results | Where-Object { -not $_.passed })
if ($failed.Count -gt 0) {
    Write-Error "Deployment verification failed: $($failed.Count) check(s) failed."
    exit 1
}

Write-Host "Deployment verification passed."
