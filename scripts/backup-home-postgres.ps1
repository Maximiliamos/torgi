[CmdletBinding()]
param(
    [string]$Destination = 'D:\BankrotAI\dr-backups',
    [switch]$VerifyRestore,
    [ValidateRange(0, 10)]
    [int]$RetainCount = 1,
    [ValidateRange(0, 9)]
    [int]$CompressionLevel = 1,
    [int]$MaxMapDatasetCountWarning = 5,
    [long]$MaxNonCurrentMapTilesWarning = 500000
)

$ErrorActionPreference = 'Stop'
$databaseContainer = 'bankrotai-home-postgres'
$verifyContainer = 'bankrotai-backup-verify'
$resolvedDestination = [System.IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $resolvedDestination | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $resolvedDestination "bankrotai-$stamp.dump"
$metadata = Join-Path $resolvedDestination "bankrotai-$stamp.json"

if ((docker inspect --format '{{.State.Status}}' $databaseContainer) -ne 'running') {
    throw "$databaseContainer is not running"
}

Write-Host "Creating PostgreSQL custom-format backup outside the repository..."
$pgUser = (docker exec $databaseContainer printenv POSTGRES_USER).Trim()
$pgDatabase = (docker exec $databaseContainer printenv POSTGRES_DB).Trim()
$countSql = "SELECT concat_ws(',',(SELECT count(*) FROM processed_lots),(SELECT count(*) FROM lot_geo_snapshots),(SELECT count(*) FROM app_users),(SELECT count(*) FROM lot_sync_runs),(SELECT count(*) FROM map_datasets))"
$schemaSql = "SELECT version_num FROM alembic_version LIMIT 1"
$mapStorageSql = "SELECT concat_ws(',',count(*),count(*) FILTER (WHERE is_current),coalesce(sum(tile_count) FILTER (WHERE NOT is_current),0),coalesce(sum(tile_count),0)) FROM map_datasets"
$mapStorageRaw = docker exec $databaseContainer psql -U $pgUser -d $pgDatabase -Atc $mapStorageSql
if ($LASTEXITCODE -ne 0 -or -not $mapStorageRaw) { throw 'Could not read map storage guard before backup' }
$mapStorageParts = $mapStorageRaw.Trim().Split(',')
if ($mapStorageParts.Count -ne 4) { throw "Unexpected map storage guard result: $mapStorageRaw" }
$mapStorage = [ordered]@{
    dataset_count = [int]$mapStorageParts[0]
    current_dataset_count = [int]$mapStorageParts[1]
    non_current_tile_count = [long]$mapStorageParts[2]
    declared_tile_count = [long]$mapStorageParts[3]
}
$mapStorageAnomaly = (
    $mapStorage.current_dataset_count -ne 1 -or
    $mapStorage.dataset_count -gt $MaxMapDatasetCountWarning -or
    $mapStorage.non_current_tile_count -gt $MaxNonCurrentMapTilesWarning
)
if ($mapStorageAnomaly) {
    Write-Warning ("Map storage anomaly before backup: datasets={0}, current={1}, non_current_tiles={2}. Backup will continue but metadata will flag the condition." -f $mapStorage.dataset_count, $mapStorage.current_dataset_count, $mapStorage.non_current_tile_count)
}

$sourceCounts = docker exec $databaseContainer psql -U $pgUser -d $pgDatabase -Atc $countSql
if ($LASTEXITCODE -ne 0) { throw 'Could not read source row counts before backup' }
$sourceSchema = docker exec $databaseContainer psql -U $pgUser -d $pgDatabase -Atc $schemaSql
if ($LASTEXITCODE -ne 0 -or -not $sourceSchema.Trim()) { throw 'Could not read source schema revision before backup' }
$temporaryDump = "/tmp/bankrotai-$stamp.dump"
$backupStartedAt = Get-Date
$backupDurationSeconds = $null
try {
    Write-Host "Starting pg_dump with compression level $CompressionLevel..."
    docker exec $databaseContainer pg_dump -U $pgUser -d $pgDatabase -F c -Z $CompressionLevel -f $temporaryDump
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed' }
    $backupDurationSeconds = [math]::Round(((Get-Date) - $backupStartedAt).TotalSeconds, 2)
    $containerDumpBytes = (docker exec $databaseContainer stat -c '%s' $temporaryDump).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect completed pg_dump size' }
    Write-Host "pg_dump completed in $backupDurationSeconds seconds; container dump bytes=$containerDumpBytes"
    docker cp "${databaseContainer}:$temporaryDump" $backup | Out-Null
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $backup) -or (Get-Item -LiteralPath $backup).Length -le 0) {
        throw 'pg_dump did not create a non-empty backup'
    }
    docker exec $databaseContainer pg_restore --list $temporaryDump | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'pg_restore --list failed' }
} finally {
    docker exec $databaseContainer rm -f $temporaryDump | Out-Null
}

$sourceCountsAfter = docker exec $databaseContainer psql -U $pgUser -d $pgDatabase -Atc $countSql
if ($LASTEXITCODE -ne 0) { throw 'Could not read source row counts after backup' }
$sourceSchemaAfter = docker exec $databaseContainer psql -U $pgUser -d $pgDatabase -Atc $schemaSql
if ($LASTEXITCODE -ne 0 -or -not $sourceSchemaAfter.Trim()) { throw 'Could not read source schema revision after backup' }
if ($sourceSchema.Trim() -ne $sourceSchemaAfter.Trim()) { throw "Schema changed during backup: before=$sourceSchema after=$sourceSchemaAfter" }
$sourceChangedDuringBackup = $sourceCounts.Trim() -ne $sourceCountsAfter.Trim()

$restoreStatus = 'not-requested'
if ($VerifyRestore) {
    $existing = docker ps -a --filter "name=^/${verifyContainer}$" --format '{{.Names}}'
    if ($existing) { throw "Safety stop: $verifyContainer already exists" }
    $verifyPassword = [guid]::NewGuid().ToString('N')
    try {
        docker run -d --name $verifyContainer --network none `
            -e "POSTGRES_PASSWORD=$verifyPassword" -e POSTGRES_DB=bankrotai_restore postgres:17 | Out-Null
        for ($attempt = 1; $attempt -le 30; $attempt++) {
            docker exec $verifyContainer pg_isready -U postgres -d bankrotai_restore *> $null
            if ($LASTEXITCODE -eq 0) { break }
            if ($attempt -eq 30) { throw 'Isolated PostgreSQL restore target did not become ready' }
            Start-Sleep -Seconds 2
        }
        docker cp $backup "${verifyContainer}:/tmp/restore.dump" | Out-Null
        docker exec $verifyContainer pg_restore -U postgres -d bankrotai_restore --no-owner --no-privileges --jobs=4 /tmp/restore.dump
        if ($LASTEXITCODE -ne 0) { throw 'Isolated pg_restore failed' }
        $restoredCounts = docker exec $verifyContainer psql -U postgres -d bankrotai_restore -Atc `
            $countSql
        if ($LASTEXITCODE -ne 0) { throw 'Could not read restored row counts' }
        $restoredSchema = docker exec $verifyContainer psql -U postgres -d bankrotai_restore -Atc $schemaSql
        if ($LASTEXITCODE -ne 0) { throw 'Could not read restored schema revision' }
        if ($restoredSchema.Trim() -ne $sourceSchema.Trim()) { throw "Restored schema revision mismatch: source=$sourceSchema restored=$restoredSchema" }
        $before = @($sourceCounts.Trim().Split(',') | ForEach-Object { [long]$_ })
        $after = @($sourceCountsAfter.Trim().Split(',') | ForEach-Object { [long]$_ })
        $restored = @($restoredCounts.Trim().Split(',') | ForEach-Object { [long]$_ })
        $countsPlausible = $restored.Count -eq 5
        for ($index = 0; $countsPlausible -and $index -lt 5; $index++) {
            $minimum = [math]::Min($before[$index], $after[$index])
            $maximum = [math]::Max($before[$index], $after[$index])
            $countsPlausible = $restored[$index] -ge $minimum -and $restored[$index] -le $maximum
        }
        if (!$countsPlausible) {
            throw "Restore counts are outside the source range: before=$sourceCounts after=$sourceCountsAfter restored=$restoredCounts"
        }
        $restoreStatus = 'passed'
    } finally {
        $exact = docker ps -a --filter "name=^/${verifyContainer}$" --format '{{.Names}}'
        if ($exact -eq $verifyContainer) { docker rm -fv $verifyContainer | Out-Null }
    }
}

$checksum = (Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash.ToLowerInvariant()
$details = [ordered]@{
    created_at = (Get-Date).ToUniversalTime().ToString('o')
    source_container = $databaseContainer
    postgres_version = (docker exec $databaseContainer postgres --version)
    source_schema_revision = $sourceSchema.Trim()
    source_counts_before = $sourceCounts.Trim()
    source_counts_after = $sourceCountsAfter.Trim()
    source_changed_during_backup = $sourceChangedDuringBackup
    map_storage = $mapStorage
    map_storage_anomaly = $mapStorageAnomaly
    compression_level = $CompressionLevel
    backup_duration_seconds = $backupDurationSeconds
    restored_counts = if ($VerifyRestore) { $restoredCounts.Trim() } else { $null }
    restored_schema_revision = if ($VerifyRestore) { $restoredSchema.Trim() } else { $null }
    backup_file = $backup
    size_bytes = (Get-Item -LiteralPath $backup).Length
    sha256 = $checksum
    restore_verification = $restoreStatus
}
$details | ConvertTo-Json | Set-Content -LiteralPath $metadata -Encoding utf8

$snapshotJson = $details | ConvertTo-Json -Depth 8 -Compress
$persistCommand = "import json,sys; from bankrotai.db import SessionLocal; from bankrotai.services.operations_status import record_operations_snapshot; p=json.loads(sys.stdin.buffer.read().decode('utf-8-sig')); s=SessionLocal(); record_operations_snapshot(s,'backup',p); s.commit(); s.close()"
$persistExitCode = 0
$persistOutput = @()
$previousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    $persistOutput = @($snapshotJson | docker exec -i bankrotai-home-map-worker python -c $persistCommand 2>&1)
    $persistExitCode = $LASTEXITCODE
} catch {
    $persistExitCode = 1
    $persistOutput = @($_.Exception.Message)
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($persistExitCode -ne 0) {
    $persistDetails = ($persistOutput | Out-String).Trim()
    Write-Warning ("Could not persist backup snapshot for Operations UX; backup remains valid. {0}" -f $persistDetails)
}
$global:LASTEXITCODE = 0

if ($RetainCount -gt 0) {
    if ($restoreStatus -ne 'passed') {
        Write-Warning "Retention skipped because the new backup has not passed isolated restore verification."
    } else {
        $dumpFiles = @(
            Get-ChildItem -LiteralPath $resolvedDestination -File -Filter 'bankrotai-*.dump' |
                Sort-Object LastWriteTimeUtc -Descending
        )
        $protected = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
        foreach ($dump in ($dumpFiles | Select-Object -First $RetainCount)) {
            [void]$protected.Add($dump.FullName)
            $stem = [System.IO.Path]::GetFileNameWithoutExtension($dump.Name)
            [void]$protected.Add((Join-Path $resolvedDestination "$stem.json"))
        }
        Get-ChildItem -LiteralPath $resolvedDestination -File -Filter 'bankrotai-*' |
            Where-Object { -not $protected.Contains($_.FullName) } |
            Remove-Item -Force
    }
}

$details | ConvertTo-Json
