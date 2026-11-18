[CmdletBinding()]
param([Parameter(Mandatory)][string]$Evidence,
      [Parameter(Mandatory)][string]$Output,
      [Parameter(Mandatory)][string]$ExpectedDeploymentSha,
      [ValidateSet('stale_no_match','current_missing_geo','current_address_precision')][string]$Mode = 'stale_no_match',
      [switch]$Apply, [switch]$ValidateTransaction,
      [string]$Plan, [string]$BackupMetadata)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$image = (docker inspect --format '{{.Config.Image}}' bankrotai-home-geocoding-worker).Trim()
if ($image -ne "bankrotai-home-api:$ExpectedDeploymentSha") { throw 'Unexpected deployed GEO revision' }
if ($Apply -and -not $ValidateTransaction) {
    if (-not $BackupMetadata) { throw 'Verified restorable backup required' }
    $backup = Get-Content -LiteralPath $BackupMetadata -Raw | ConvertFrom-Json
    if ($backup.restore_verification -ne 'passed' -or
        (Get-FileHash -LiteralPath $backup.backup_file -Algorithm SHA256).Hash -ne $backup.sha256) {
        throw 'Backup content or restore verification mismatch'
    }
}
$receipts = @(Get-Content -LiteralPath (Join-Path $root $Evidence) -Raw | ConvertFrom-Json)
$payload = @{ receipts=$receipts; mode=$Mode; apply=[bool]$Apply; rollback_only=[bool]$ValidateTransaction }
if ($Apply) {
    if (-not $Plan) { throw 'Preview plan required' }
    $preview = Get-Content -LiteralPath (Join-Path $root $Plan) -Raw | ConvertFrom-Json
    if (($preview.mode -and $preview.mode -ne $Mode) -or (-not $preview.mode -and $Mode -ne 'stale_no_match')) { throw 'Preview mode mismatch' }
    if ($Mode -in @('current_missing_geo','current_address_precision')) { $payload.expected_current_plan = $preview }
    $fileSha = (Get-FileHash -LiteralPath (Join-Path $root $Evidence) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($preview.evidence_file_sha256 -ne $fileSha) { throw 'Evidence file changed after preview' }
    $payload.expected_proof_sha256 = $preview.evidence_sha256
}
$payload64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 100 -Compress)))
$script64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root 'scripts/apply_verified_nspd.py')))
$code = @'
import base64,hashlib,io,json,sys
payload_bytes=base64.b64decode('PAYLOAD64')
sys.stdin=io.StringIO(payload_bytes.decode('utf-8'))
exec(compile(base64.b64decode('SCRIPT64'),'apply_verified_nspd.py','exec'),{'__name__':'__main__'})
'@
$code = $code.Replace('PAYLOAD64', $payload64).Replace('SCRIPT64', $script64)
$raw = @($code | docker exec -i bankrotai-home-geocoding-worker python -)
if ($LASTEXITCODE -ne 0) { throw 'Bounded NSPD replay failed; transaction rolled back' }
$result = ($raw -join [Environment]::NewLine) | ConvertFrom-Json
$fileSha = (Get-FileHash -LiteralPath (Join-Path $root $Evidence) -Algorithm SHA256).Hash.ToLowerInvariant()
$result | Add-Member -NotePropertyName evidence_file_sha256 -NotePropertyValue $fileSha
[IO.File]::WriteAllText((Join-Path $root $Output), ($result | ConvertTo-Json -Depth 30), [Text.UTF8Encoding]::new($false))
$result | Select-Object selected,accepted,updated,committed,reviews_and_notes_preserved,deleted_lots
