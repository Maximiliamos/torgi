[CmdletBinding()]
param(
    [string]$BackupDirectory = 'C:\ProgramData\BankrotAI\dr-backups',
    [string]$LogDirectory = 'C:\BankrotAI\logs\phase3-health',
    [string]$OutputPath = '',
    [int]$MaxBackupAgeHours = 30,
    [int]$MaxVerifiedRestoreAgeHours = 192,
    [int]$MaxMapDatasetCountWarning = 5,
    [long]$MaxNonCurrentMapTilesWarning = 500000
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
$diskFreeGb = [math]::Round($disk.Free / 1GB, 2)
Add-Check -Name 'disk-c' -Ok ($diskFreeGb -ge 15) -Details @{ free_gb = $diskFreeGb; minimum_gb = 15 }
Add-Check -Name 'disk-c-headroom' -Ok ($diskFreeGb -ge 25) -Severity 'warning' -Details @{
    free_gb = $diskFreeGb
    recommended_gb = 25
}

foreach ($url in @('http://127.0.0.1:18000/health/live', 'http://127.0.0.1:18000/health/ready')) {
    try { $statusCode = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 -Uri $url).StatusCode } catch { $statusCode = 0 }
    Add-Check -Name $url -Ok ($statusCode -eq 200) -Details @{ status = $statusCode }
}

$appHealth = $null
try {
    $appJson = docker exec bankrotai-home-ingestion-worker python -c "import json; from bankrotai.db import SessionLocal; from bankrotai.services.ingestion import default_source_specs; from bankrotai.services.production_health import build_phase3_health; from bankrotai.tasks import _unpaused_source_specs; s=SessionLocal(); expected={x.source_id for x in _unpaused_source_specs(default_source_specs())}; print(json.dumps(build_phase3_health(s, expected_sources=expected), default=str)); s.close()"
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

$mapStorage = $null
try {
    $mapStorageRaw = docker exec bankrotai-home-postgres psql -U bankrotai -d bankrotai -Atc "SELECT concat_ws(',',count(*),count(*) FILTER (WHERE is_current),coalesce(sum(tile_count) FILTER (WHERE NOT is_current),0),coalesce(sum(tile_count),0)) FROM map_datasets"
    if ($LASTEXITCODE -ne 0 -or -not $mapStorageRaw) { throw 'Map storage query failed' }
    $parts = $mapStorageRaw.Trim().Split(',')
    if ($parts.Count -ne 4) { throw "Unexpected map storage result: $mapStorageRaw" }
    $mapStorage = [ordered]@{
        dataset_count = [int]$parts[0]
        current_dataset_count = [int]$parts[1]
        non_current_tile_count = [long]$parts[2]
        declared_tile_count = [long]$parts[3]
    }
    Add-Check -Name 'map-current-dataset' -Ok ($mapStorage.current_dataset_count -eq 1) -Details @{
        current_dataset_count = $mapStorage.current_dataset_count
    }
    Add-Check -Name 'map-storage-dataset-count' -Ok ($mapStorage.dataset_count -le $MaxMapDatasetCountWarning) -Severity 'warning' -Details @{
        dataset_count = $mapStorage.dataset_count
        recommended_max = $MaxMapDatasetCountWarning
    }
    Add-Check -Name 'map-storage-non-current-tiles' -Ok ($mapStorage.non_current_tile_count -le $MaxNonCurrentMapTilesWarning) -Severity 'warning' -Details @{
        non_current_tile_count = $mapStorage.non_current_tile_count
        recommended_max = $MaxNonCurrentMapTilesWarning
    }
} catch {
    Add-Check -Name 'map-storage' -Ok $false -Severity 'warning' -Details @{ error = $_.Exception.Message }
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
    $verifiedChecksumOk = $false
    if ($verified -and $verified.metadata.backup_file -and $verified.metadata.sha256 -and (Test-Path -LiteralPath $verified.metadata.backup_file)) {
        $actualHash = (Get-FileHash -LiteralPath $verified.metadata.backup_file -Algorithm SHA256).Hash.ToLowerInvariant()
        $verifiedChecksumOk = $actualHash -eq ([string]$verified.metadata.sha256).ToLowerInvariant()
    }
    Add-Check -Name 'restore-verification-recent' -Ok (
        $null -ne $verifyAgeHours -and
        $verifyAgeHours -le $MaxVerifiedRestoreAgeHours -and
        $verifiedChecksumOk
    ) -Details @{
        file = if ($verified) { $verified.file.Name } else { $null }
        age_hours = if ($null -ne $verifyAgeHours) { [math]::Round($verifyAgeHours, 2) } else { $null }
        max_age_hours = $MaxVerifiedRestoreAgeHours
        schema_revision = if ($verified) { $verified.metadata.restored_schema_revision } else { $null }
        sha256 = if ($verified) { $verified.metadata.sha256 } else { $null }
        checksum_ok = $verifiedChecksumOk
    }
}

$result = [ordered]@{
    checked_at = $now.ToString('o')
    healthy = ($criticalFailures -eq 0)
    critical_failure_count = $criticalFailures
    warning_count = $warnings
    checks = $checks
    application = $appHealth
    map_storage = $mapStorage
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
