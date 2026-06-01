param(
    [string]$Repo = "RossDmello2/finimatic-email-ops"
)

$ErrorActionPreference = "Stop"

$required = @("RENDER_API_KEY", "DATABASE_URL", "VERCEL_TOKEN")
$optional = @("RENDER_OWNER_ID", "FERNET_KEY", "VERCEL_SCOPE")

function Get-EnvValue {
    param([string]$Name)
    return [Environment]::GetEnvironmentVariable($Name, "Process")
}

function Set-GitHubSecret {
    param(
        [string]$Name,
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return
    }

    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = "gh"
    foreach ($arg in @("secret", "set", $Name, "--repo", $Repo, "--app", "actions")) {
        [void]$psi.ArgumentList.Add($arg)
    }
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false

    $proc = [System.Diagnostics.Process]::new()
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $proc.StandardInput.Write($Value)
    $proc.StandardInput.Close()
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()

    if ($proc.ExitCode -ne 0) {
        throw "Failed to set GitHub secret $Name. $stderr"
    }
    if ($stdout.Trim()) {
        Write-Host $stdout.Trim()
    } else {
        Write-Host "Set GitHub secret $Name"
    }
}

gh auth status | Out-Null

$missing = @()
foreach ($name in $required) {
    if ([string]::IsNullOrWhiteSpace((Get-EnvValue $name))) {
        $missing += $name
    }
}

if ($missing.Count -gt 0) {
    throw "Missing required environment variable(s): $($missing -join ', ')"
}

foreach ($name in ($required + $optional)) {
    Set-GitHubSecret -Name $name -Value (Get-EnvValue $name)
}

Write-Host "Deploy secrets are configured for $Repo"
