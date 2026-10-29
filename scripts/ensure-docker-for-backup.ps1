# Recover a Docker Desktop backend before initiating an irreversible backup.
# Never stop WSL, prune images or restart an already healthy daemon here.
[CmdletBinding()]
param(
    [ValidateRange(30, 300)][int]$TimeoutSeconds = 150,
    [string]$RequiredContainer = 'bankrotai-home-postgres'
)
$ErrorActionPreference = 'Stop'

function Test-Server {
    # Limit Docker CLI hangs if the named pipe is unavailable.
    $job = Start-Job -ScriptBlock {
        & docker version --format '{{.Server.Version}}' 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    }
    try {
        if (-not (Wait-Job $job -Timeout 12)) { return $false }
        return ($job.State -eq 'Completed' -and ((Receive-Job $job) -contains $true))
    } finally {
        Stop-Job $job -ErrorAction SilentlyContinue
        Remove-Job $job -Force -ErrorAction SilentlyContinue
    }
}

function Test-Database {
    $state = (& docker inspect --format '{{.State.Status}}' $RequiredContainer 2>$null | Out-String).Trim()
    return ($LASTEXITCODE -eq 0 -and $state -eq 'running')
}

if (-not (Test-Server)) {
    Write-Warning 'Docker Desktop engine unavailable before scheduled PostgreSQL backup'
    $svc = Get-Service 'com.docker.service' -ErrorAction SilentlyContinue
    if ($null -ne $svc -and $svc.Status -ne 'Running') {
        Start-Service -Name 'com.docker.service' -ErrorAction Stop
    }
    $exe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path -LiteralPath $exe) {
        try { Start-Process -FilePath $exe -WindowStyle Hidden | Out-Null }
        catch { Write-Warning ('Docker Desktop GUI launch failed: ' + $_.Exception.Message) }
    }
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-Server) { break }
        Start-Sleep -Seconds 6
    } while ((Get-Date) -lt $deadline)
}

if (-not (Test-Server)) {
    throw 'Refusing backup: Docker daemon remains unavailable after bounded safe startup attempt'
}
if (-not (Test-Database)) {
    throw "Refusing backup: required PostgreSQL container $RequiredContainer is not running"
}
Write-Output 'Docker engine and PostgreSQL container are available for backup'
