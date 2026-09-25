[CmdletBinding()]
param(
    [string]$BackupDirectory = 'C:\ProgramData\BankrotAI\dr-backups',
    [string]$LogDirectory = 'C:\BankrotAI\logs\phase3-health',
    [string]$OutputPath = '',
    [int]$MaxBackupAgeHours = 30,
    [int]$MaxVerifiedRestoreAgeHours = 192
)

$ErrorActionPreference = 'Stop'
$checks = @()
$criticalFailures = 0
$warnings = 0

function Add-Check {
    param(
        [string]$Name,
        [bool]$Ok,
        [string]$Severity = 'critical',
        [hashtable]$Details = @{}
    )
    $entry = [ordered]@{ name = $Name; ok = $Ok; severity = $Severity }
    foreach ($key in $Details.Keys) { $entry[$key] = $Details[$key] }
    $script:checks += $entry
    if (-not $Ok) {
        if ($Severity -eq 'warning') { $script:warnings++ } else { $script:criticalFailures++ }
    }
}

$requiredContainers = @(
    'bankrotai-home-postgres',
    'bankrotai-home-redis',
    'bankrotai-photon',
    'bankrotai-home-secondary',
    'bankrotai-home-ingestion-worker',
    'bankrotai-home-geocoding-worker',
    'bankrotai-home-map-worker'
)
foreach ($name in $requiredContainers) {
    $status = docker inspect --format '{{.State.Status}}' $name 2>$null
    $health = docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}not-configured{{end}}' $name 2>$null
    $ok = $LASTEXITCODE -eq 0 -and $status -eq 'running' -and $health -notin @('unhealthy', 'starting')
    Add-Check -Name "container:$name" -Ok $ok -Details @{ status = $status; health = $health }
}

$disk = Get-PSDrive -Name C
$diskPercentFree = if (($disk.Used + $disk.Free) -gt 0) {
    [math]::Round(($disk.Free / ($disk.Used + $disk.Free)) * 100, 1)
} else { 0 }
Add-Check -Name 'disk-c' -Ok ($diskPercentFree -ge 10) -Details @{ percent_free = $diskPercentFree }

foreach ($url in @('http://127.0.0.1:18000/health/live', 'http://127.0.0.1:18000/health/ready')) {
    try { $statusCode = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 -Uri $url).StatusCode } catch { $statusCode = 0 }
    Add-Check -Name $url -Ok ($statusCode -eq 200) -Details @{ status = $statusCode }
}

$appHealth = $null
try {
    $appJson = docker exec bankrotai-home-ingestion-worker python -c "import json; from bankrotai.db import SessionLocal; from bankrotai.services.production_health import build_phase3_health; s=SessionLocal(); print(json.dumps(build_phase3_health(s), default=str)); s.close()"
    if ($LASTEXITCODE -ne 0) { throw 'Application health query failed' }
    $appHealth = ($appJson | Select-Object -Last 1) | ConvertFrom-Json
    Add-Check -Name 'application-data-health' -Ok ([bool]$appHealth.healthy) -Details @{
        critical_failure_count = [int]$appHealth.critical_failure_count
        warning_count = [int]$appHealth.warning_count
        summary = $appHealth.summary
    }
} catch {
    Add-Check -Name 'application-data-health' -Ok $false -Details @{ error = $_.Exception.Message }
}

$now = (Get-Date).ToUniversalTime()
if (-not (Test-Path -LiteralPath $BackupDirectory)) {
    Add-Check -Name 'backup-recent' -Ok $false -Details @{ reason = 'backup-directory-missing' }
    Add-Check -Name 'restore-verification-recent' -Ok $false -Details @{ reason = 'backup-directory-missing' }
} else {
    $latestDump = Get-ChildItem -LiteralPath $BackupDirectory -File -Filter 'bankrotai-*.dump' |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    $dumpAgeHours = if ($latestDump) { ($now - $latestDump.LastWriteTimeUtc).TotalHours } else { $null }
    Add-Check -Name 'backup-recent' -Ok ($null -ne $dumpAgeHours -and $dumpAgeHours -le $MaxBackupAgeHours) -Details @{
        file = if ($latestDump) { $latestDump.Name } else { $null }
        age_hours = if ($null -ne $dumpAgeHours) { [math]::Round($dumpAgeHours, 2) } else { $null }
        max_age_hours = $MaxBackupAgeHours
    }

    $verified = $null
    foreach ($metadataFile in (Get-ChildItem -LiteralPath $BackupDirectory -File -Filter 'bankrotai-*.json' | Sort-Object LastWriteTimeUtc -Descending)) {
        try {
            $metadata = Get-Content -LiteralPath $metadataFile.FullName -Raw | ConvertFrom-Json
            if ($metadata.restore_verification -eq 'passed') {
                $verified = [ordered]@{ file = $metadataFile; metadata = $metadata }
                break
            }
        } catch {}
    }
    $verifyAgeHours = if ($verified) { ($now - $verified.file.LastWriteTimeUtc).TotalHours } else { $null }
    Add-Check -Name 'restore-verification-recent' -Ok ($null -ne $verifyAgeHours -and $verifyAgeHours -le $MaxVerifiedRestoreAgeHours) -Details @{
        file = if ($verified) { $verified.file.Name } else { $null }
        age_hours = if ($null -ne $verifyAgeHours) { [math]::Round($verifyAgeHours, 2) } else { $null }
        max_age_hours = $MaxVerifiedRestoreAgeHours
        schema_revision = if ($verified) { $verified.metadata.restored_schema_revision } else { $null }
        sha256 = if ($verified) { $verified.metadata.sha256 } else { $null }
    }
}

$result = [ordered]@{
    checked_at = $now.ToString('o')
    healthy = ($criticalFailures -eq 0)
    critical_failure_count = $criticalFailures
    warning_count = $warnings
    checks = $checks
    application = $appHealth
}

New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$logPath = Join-Path $LogDirectory ("health-{0}.json" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$result | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $logPath -Encoding utf8
if ($OutputPath) {
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) { New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null }
    $result | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutputPath -Encoding utf8
}
Get-ChildItem -LiteralPath $LogDirectory -File -Filter 'health-*.json' |
    Where-Object LastWriteTimeUtc -lt $now.AddDays(-14) |
    Remove-Item -Force

$result | ConvertTo-Json -Depth 12
if ($criticalFailures -gt 0) { exit 1 }
