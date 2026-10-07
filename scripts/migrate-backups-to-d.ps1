[CmdletBinding()]
param(
    [string]$Destination = 'D:\BankrotAI\dr-backups'
)

$ErrorActionPreference = 'Stop'

function Get-FreeGb {
    param([string]$DriveName)
    return [math]::Round((Get-PSDrive -Name $DriveName -ErrorAction Stop).Free / 1GB, 2)
}

if (-not (Get-PSDrive -Name D -ErrorAction SilentlyContinue)) {
    throw 'Migration refused: D: drive is not available'
}

Write-Host 'Phase 1: remove only confirmed legacy backup locations from C:.'
$confirmedLegacyRoots = @(
    'C:\BankrotAI\backups\postgres',
    'C:\ProgramData\BankrotAI\backups'
)
foreach ($path in $confirmedLegacyRoots) {
    if (Test-Path -LiteralPath $path) {
        Write-Host "Removing confirmed legacy backup path: $path"
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}

$cFreeBefore = Get-FreeGb -DriveName 'C'
$dFreeBefore = Get-FreeGb -DriveName 'D'
Write-Host "Free space after legacy cleanup: C=$cFreeBefore GB; D=$dFreeBefore GB"

$requiredGb = 30
if ($cFreeBefore -lt $requiredGb) {
    throw "Migration refused: C: has only $cFreeBefore GB free; current pg_dump implementation needs temporary Docker headroom"
}
if ($dFreeBefore -lt $requiredGb) {
    throw "Migration refused: D: has only $dFreeBefore GB free; need at least $requiredGb GB"
}

Write-Host 'Phase 2: create and verify the new canonical D: backup.'
& "$PSScriptRoot\backup-home-postgres.ps1" -Destination $Destination -RetainCount 3 -VerifyRestore
if ($LASTEXITCODE -ne 0) {
    throw 'Canonical D: backup failed'
}

$latestMetadata = Get-ChildItem -LiteralPath $Destination -File -Filter 'bankrotai-*.json' |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
if (-not $latestMetadata) {
    throw 'Migration refused: canonical D: backup metadata is missing'
}

$metadata = Get-Content -LiteralPath $latestMetadata.FullName -Raw | ConvertFrom-Json
if ($metadata.restore_verification -ne 'passed') {
    throw "Migration refused: D: backup restore verification is $($metadata.restore_verification)"
}
if (-not $metadata.backup_file -or -not (Test-Path -LiteralPath ([string]$metadata.backup_file))) {
    throw 'Migration refused: D: backup file referenced by metadata is missing'
}
$actualHash = (Get-FileHash -LiteralPath ([string]$metadata.backup_file) -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne ([string]$metadata.sha256).ToLowerInvariant()) {
    throw 'Migration refused: D: backup SHA-256 mismatch'
}

Write-Host 'Phase 3: verified D: backup exists; remove superseded C: DR/pre-migration copies.'
$supersededRoots = @(
    'C:\ProgramData\BankrotAI\dr-backups',
    'C:\ProgramData\BankrotAI\db-backups'
)
foreach ($path in $supersededRoots) {
    if (Test-Path -LiteralPath $path) {
        Write-Host "Removing superseded backup path: $path"
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}

$result = [ordered]@{
    migrated_at = (Get-Date).ToUniversalTime().ToString('o')
    canonical_backup_root = $Destination
    canonical_backup_file = $metadata.backup_file
    restore_verification = $metadata.restore_verification
    sha256 = $metadata.sha256
    c_free_gb = Get-FreeGb -DriveName 'C'
    d_free_gb = Get-FreeGb -DriveName 'D'
    removed_legacy_roots = @(
        'C:\BankrotAI\backups\postgres',
        'C:\ProgramData\BankrotAI\backups',
        'C:\ProgramData\BankrotAI\dr-backups',
        'C:\ProgramData\BankrotAI\db-backups'
    )
}
$result | ConvertTo-Json -Depth 5
