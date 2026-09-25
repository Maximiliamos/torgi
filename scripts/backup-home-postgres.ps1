[CmdletBinding()]
param(
    [string]$Destination = 'C:\BankrotAI\backups\postgres',
    [switch]$VerifyRestore,
    [int]$RetainDays = 14
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
$sourceCounts = docker exec $databaseContainer psql -U $pgUser -d $pgDatabase -Atc $countSql
if ($LASTEXITCODE -ne 0) { throw 'Could not read source row counts before backup' }
$sourceSchema = docker exec $databaseContainer psql -U $pgUser -d $pgDatabase -Atc $schemaSql
if ($LASTEXITCODE -ne 0 -or -not $sourceSchema.Trim()) { throw 'Could not read source schema revision before backup' }
$temporaryDump = "/tmp/bankrotai-$stamp.dump"
try {
    docker exec $databaseContainer pg_dump -U $pgUser -d $pgDatabase -F c -f $temporaryDump
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed' }
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
        if ($exact -eq $verifyContainer) { docker rm -f $verifyContainer | Out-Null }
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
    restored_counts = if ($VerifyRestore) { $restoredCounts.Trim() } else { $null }
    restored_schema_revision = if ($VerifyRestore) { $restoredSchema.Trim() } else { $null }
    backup_file = $backup
    size_bytes = (Get-Item -LiteralPath $backup).Length
    sha256 = $checksum
    restore_verification = $restoreStatus
}
$details | ConvertTo-Json | Set-Content -LiteralPath $metadata -Encoding utf8

if ($RetainDays -gt 0) {
    $cutoff = (Get-Date).AddDays(-$RetainDays)
    Get-ChildItem -LiteralPath $resolvedDestination -File -Filter 'bankrotai-*' |
        Where-Object LastWriteTime -lt $cutoff |
        Remove-Item -Force
}

$details | ConvertTo-Json
