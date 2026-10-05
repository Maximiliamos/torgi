[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$BackupDirectory = 'C:\ProgramData\BankrotAI\dr-backups',
    [int]$MaxBackupAgeHours = 48,
    [double]$FreeSpaceMultiplier = 1.35,
    [int]$ExtraFreeGb = 10,
    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'
$postgres = 'bankrotai-home-postgres'
$mapWorker = 'bankrotai-home-map-worker'
$pgUser = 'bankrotai'
$pgDatabase = 'bankrotai'

if ((docker inspect --format '{{.State.Status}}' $postgres 2>$null) -ne 'running') {
    throw "$postgres is not running"
}

$statsSql = @"
SELECT concat_ws(',',
    count(*),
    count(*) FILTER (WHERE is_current),
    coalesce(sum(tile_count) FILTER (WHERE NOT is_current),0),
    pg_total_relation_size('map_tiles'),
    pg_relation_size('map_tiles'),
    pg_indexes_size('map_tiles'))
FROM map_datasets;
"@
$raw = docker exec $postgres psql -U $pgUser -d $pgDatabase -Atc $statsSql
if ($LASTEXITCODE -ne 0 -or -not $raw) { throw 'Could not inspect map storage before compaction' }
$parts = $raw.Trim().Split(',')
if ($parts.Count -ne 6) { throw "Unexpected map storage result: $raw" }

$datasetCount = [int]$parts[0]
$currentCount = [int]$parts[1]
$nonCurrentTiles = [long]$parts[2]
$totalBytes = [long]$parts[3]
$tableBytes = [long]$parts[4]
$indexBytes = [long]$parts[5]

$drive = Get-PSDrive -Name C
$freeBytes = [long]$drive.Free
$requiredFreeBytes = [long]([math]::Ceiling($totalBytes * $FreeSpaceMultiplier) + ($ExtraFreeGb * 1GB))

$latestBackup = $null
$backupAgeHours = $null
if (Test-Path -LiteralPath $BackupDirectory) {
    $latestBackup = Get-ChildItem -LiteralPath $BackupDirectory -File -Filter 'bankrotai-*.dump' |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if ($latestBackup) {
        $backupAgeHours = ((Get-Date).ToUniversalTime() - $latestBackup.LastWriteTimeUtc).TotalHours
    }
}

$preconditions = [ordered]@{
    single_current_dataset = ($currentCount -eq 1)
    bounded_dataset_count = ($datasetCount -le 5)
    recent_backup = ($null -ne $backupAgeHours -and $backupAgeHours -le $MaxBackupAgeHours)
    enough_free_space = ($freeBytes -ge $requiredFreeBytes)
}
$canApply = -not ($preconditions.Values -contains $false)

$result = [ordered]@{
    checked_at = (Get-Date).ToUniversalTime().ToString('o')
    applied = $false
    can_apply = $canApply
    dataset_count = $datasetCount
    current_dataset_count = $currentCount
    non_current_tile_count = $nonCurrentTiles
    map_tiles_total_gb_before = [math]::Round($totalBytes / 1GB, 2)
    map_tiles_heap_gb_before = [math]::Round($tableBytes / 1GB, 2)
    map_tiles_indexes_gb_before = [math]::Round($indexBytes / 1GB, 2)
    disk_free_gb_before = [math]::Round($freeBytes / 1GB, 2)
    required_free_gb = [math]::Round($requiredFreeBytes / 1GB, 2)
    latest_backup = if ($latestBackup) { $latestBackup.Name } else { $null }
    latest_backup_age_hours = if ($null -ne $backupAgeHours) { [math]::Round($backupAgeHours, 2) } else { $null }
    preconditions = $preconditions
}

if ($Apply) {
    if (-not $canApply) {
        throw "Refusing VACUUM FULL: one or more compaction preconditions failed: $($preconditions | ConvertTo-Json -Compress)"
    }

    $workerWasRunning = (docker inspect --format '{{.State.Status}}' $mapWorker 2>$null) -eq 'running'
    try {
        if ($workerWasRunning) {
            docker stop --time 30 $mapWorker | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Could not stop $mapWorker before compaction" }
        }

        docker exec $postgres psql -v ON_ERROR_STOP=1 -U $pgUser -d $pgDatabase -c "SET statement_timeout = 0; VACUUM (FULL, ANALYZE) map_tiles;"
        if ($LASTEXITCODE -ne 0) { throw 'VACUUM FULL map_tiles failed' }

        $afterRaw = docker exec $postgres psql -U $pgUser -d $pgDatabase -Atc "SELECT concat_ws(',',pg_total_relation_size('map_tiles'),pg_relation_size('map_tiles'),pg_indexes_size('map_tiles'))"
        if ($LASTEXITCODE -ne 0 -or -not $afterRaw) { throw 'Could not inspect map storage after compaction' }
        $after = $afterRaw.Trim().Split(',')
        $result.applied = $true
        $result.map_tiles_total_gb_after = [math]::Round(([long]$after[0]) / 1GB, 2)
        $result.map_tiles_heap_gb_after = [math]::Round(([long]$after[1]) / 1GB, 2)
        $result.map_tiles_indexes_gb_after = [math]::Round(([long]$after[2]) / 1GB, 2)
        $result.disk_free_gb_after = [math]::Round((Get-PSDrive -Name C).Free / 1GB, 2)
    } finally {
        if ($workerWasRunning) {
            docker start $mapWorker | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Could not restart $mapWorker after compaction" }
        }
    }
}

if ($OutputPath) {
    $directory = Split-Path -Parent $OutputPath
    if ($directory) { New-Item -ItemType Directory -Force -Path $directory | Out-Null }
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8
}
$result | ConvertTo-Json -Depth 8
