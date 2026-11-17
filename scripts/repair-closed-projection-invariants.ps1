[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$ValidateTransaction,
    [string]$Plan = '.artifacts/finish/archive-invariants-preview.json',
    [string]$BackupMetadata,
    [string]$Output = '.artifacts/finish/archive-invariants-result.json'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if ($Apply -and -not $ValidateTransaction) {
    if (-not $BackupMetadata) { throw 'Verified restorable backup metadata is required' }
    $proof = Get-Content -LiteralPath $BackupMetadata -Raw | ConvertFrom-Json
    if ($proof.restore_verification -ne 'passed' -or -not (Test-Path -LiteralPath $proof.backup_file)) {
        throw 'Backup has not passed isolated restore verification'
    }
    if ((Get-FileHash -LiteralPath $proof.backup_file -Algorithm SHA256).Hash -ne $proof.sha256) {
        throw 'Backup file does not match its verified hash'
    }
}
$arguments = @('exec', '-i', 'bankrotai-home-ingestion-worker', 'python', '-')
if ($Apply) {
    $preview = Get-Content -LiteralPath (Join-Path $root $Plan) -Raw | ConvertFrom-Json
    if ($preview.preview_sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Invalid preview digest' }
    $arguments += @('--apply', '--expected-sha', $preview.preview_sha256)
    if ($ValidateTransaction) { $arguments += '--rollback-only' }
}
$result = @(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'repair_closed_projection_invariants.py') -Raw | docker @arguments)
if ($LASTEXITCODE -ne 0) { throw 'Archive invariant repair failed; transaction rolled back' }
$report = ($result -join [Environment]::NewLine) | ConvertFrom-Json
$report | ConvertTo-Json -Depth 10 -Compress | Set-Content -LiteralPath (Join-Path $root $Output) -Encoding utf8
$report | Select-Object selected,updated,committed,deleted_lots,preview_sha256
