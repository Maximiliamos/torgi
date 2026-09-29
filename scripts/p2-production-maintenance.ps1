[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$OutputPath = '',
    [string]$LogDirectory = 'C:\BankrotAI\logs\p2-maintenance',
    [int]$CriticalFreePercent = 10,
    [int]$WarningFreePercent = 15,
    [int]$RetainLogDays = 14,
    [int]$DockerPruneAfterHours = 168
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

function Get-FreePercent {
    $drive = Get-PSDrive -Name C
    if (($drive.Used + $drive.Free) -le 0) { return 0.0 }
    return [math]::Round(($drive.Free / ($drive.Used + $drive.Free)) * 100, 1)
}

$beforeFree = Get-FreePercent
$dockerBefore = (docker system df 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    Add-Check -Name 'docker-system-df' -Ok $false -Details @{ phase = 'before' }
}

$runtimeContainers = @(
    'bankrotai-home-secondary',
    'bankrotai-home-ingestion-worker',
    'bankrotai-home-geocoding-worker',
    'bankrotai-home-map-worker'
)
foreach ($container in $runtimeContainers) {
    $configJson = docker inspect --format '{{json .HostConfig.LogConfig}}' $container 2>$null
    $ok = $false
    $maxSize = $null
    $maxFile = $null
    if ($LASTEXITCODE -eq 0 -and $configJson) {
        try {
            $config = $configJson | ConvertFrom-Json
            $maxSize = $config.Config.'max-size'
            $maxFile = $config.Config.'max-file'
            $ok = $maxSize -eq '20m' -and $maxFile -eq '5'
        } catch {}
    }
    Add-Check -Name "docker-log-rotation:$container" -Ok $ok -Details @{
        max_size = $maxSize
        max_file = $maxFile
    }
}

if ($Apply) {
    docker image prune --force --filter "until=$($DockerPruneAfterHours)h" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker image prune failed' }
    docker builder prune --force --filter "until=$($DockerPruneAfterHours)h" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker builder prune failed' }
}

$mapCommand = if ($Apply) {
    "import json; from bankrotai.tasks import cleanup_old_map_datasets_task; print(json.dumps(cleanup_old_map_datasets_task.run(), default=str))"
} else {
    "import json; from bankrotai.db import SessionLocal; from bankrotai.services.map_builder import cleanup_map_datasets; print(json.dumps(cleanup_map_datasets(SessionLocal, retain_previous_ready=1, min_age_hours=24, apply=False), default=str))"
}
$mapJson = docker exec bankrotai-home-map-worker python -c $mapCommand
if ($LASTEXITCODE -ne 0 -or -not $mapJson) {
    Add-Check -Name 'map-retention' -Ok $false -Details @{ apply = [bool]$Apply }
    $mapRetention = $null
} else {
    $mapRetention = ($mapJson | Select-Object -Last 1) | ConvertFrom-Json
    $candidateCount = if ($null -ne $mapRetention.candidate_dataset_count) { [int]$mapRetention.candidate_dataset_count } else { 0 }
    Add-Check -Name 'map-retention' -Ok $true -Details @{
        apply = [bool]$Apply
        candidate_dataset_count = $candidateCount
    }
}

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$cutoff = (Get-Date).ToUniversalTime().AddDays(-[math]::Max(1, $RetainLogDays))
$logRoots = @(
    'C:\BankrotAI\logs\phase3-health',
    $LogDirectory
)
$deletedLogs = 0
if ($Apply) {
    foreach ($root in $logRoots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $expired = @(Get-ChildItem -LiteralPath $root -File -ErrorAction SilentlyContinue |
            Where-Object LastWriteTimeUtc -lt $cutoff)
        foreach ($file in $expired) {
            Remove-Item -LiteralPath $file.FullName -Force
            $deletedLogs++
        }
    }
}

$afterFree = Get-FreePercent
$dockerAfter = (docker system df 2>&1 | Out-String).Trim()
Add-Check -Name 'disk-c-critical' -Ok ($afterFree -ge $CriticalFreePercent) -Details @{
    percent_free = $afterFree
    minimum_percent = $CriticalFreePercent
}
Add-Check -Name 'disk-c-headroom' -Ok ($afterFree -ge $WarningFreePercent) -Severity 'warning' -Details @{
    percent_free = $afterFree
    recommended_percent = $WarningFreePercent
}

$result = [ordered]@{
    checked_at = (Get-Date).ToUniversalTime().ToString('o')
    applied = [bool]$Apply
    healthy = ($criticalFailures -eq 0)
    critical_failure_count = $criticalFailures
    warning_count = $warnings
    disk_percent_free_before = $beforeFree
    disk_percent_free_after = $afterFree
    deleted_old_log_files = $deletedLogs
    map_retention = $mapRetention
    docker_system_df_before = $dockerBefore
    docker_system_df_after = $dockerAfter
    checks = $checks
}

$logPath = Join-Path $LogDirectory ("maintenance-{0}.json" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$result | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath $logPath -Encoding utf8
if ($OutputPath) {
    $directory = Split-Path -Parent $OutputPath
    if ($directory) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
    $result | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath $OutputPath -Encoding utf8
}
$result | ConvertTo-Json -Depth 14
if ($criticalFailures -gt 0) { exit 1 }
