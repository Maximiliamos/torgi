[CmdletBinding()]
param([Parameter(Mandatory)][string]$Selection,
      [Parameter(Mandatory)][string]$Output,
      [Parameter(Mandatory)][string]$ExpectedDeploymentSha,
      [ValidateSet('duplicate_links','geo_query_policy')][string]$Operation = 'duplicate_links',
      [switch]$Apply, [switch]$ValidateTransaction,
      [string]$Plan, [string]$BackupMetadata, [string]$MaintenanceRunId)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$image = (docker inspect --format '{{.Config.Image}}' bankrotai-home-ingestion-worker).Trim()
if ($image -ne "bankrotai-home-api:$ExpectedDeploymentSha") { throw 'Unexpected deployed repair revision' }
if ($Apply -and -not $ValidateTransaction) {
    if (-not $BackupMetadata) { throw 'Verified restorable backup required' }
    $backup = Get-Content -LiteralPath $BackupMetadata -Raw | ConvertFrom-Json
    if ($backup.restore_verification -ne 'passed' -or
        (Get-FileHash -LiteralPath $backup.backup_file -Algorithm SHA256).Hash -ne $backup.sha256) {
        throw 'Backup content or restore verification mismatch'
    }
}
$selectionPath = Join-Path $root $Selection
$ids = @(Get-Content -LiteralPath $selectionPath -Raw | ConvertFrom-Json)
$fileSha = (Get-FileHash -LiteralPath $selectionPath -Algorithm SHA256).Hash.ToLowerInvariant()
$payload = @{lot_ids=$ids;operation=$Operation;apply=[bool]$Apply;rollback_only=[bool]$ValidateTransaction;maintenance_run_id=$MaintenanceRunId}
if ($Apply) {
    if (-not $Plan -or -not $MaintenanceRunId) { throw 'Preview and maintenance identity required' }
    $expected = Get-Content -LiteralPath (Join-Path $root $Plan) -Raw | ConvertFrom-Json
    if ($Operation -ne 'duplicate_links' -and $expected.operation -ne $Operation) { throw 'Preview operation mismatch' }
    if ($expected.selection_file_sha256 -ne $fileSha) { throw 'Exact selection changed after preview' }
    $payload.expected = $expected
}
$payload64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 40 -Compress)))
$script64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root 'scripts/apply_duplicate_link_repair.py')))
$code = @'
import base64,io,sys
sys.stdin=io.StringIO(base64.b64decode('PAYLOAD').decode())
exec(compile(base64.b64decode('SCRIPT'),'apply_duplicate_link_repair.py','exec'),{'__name__':'__main__'})
'@
$raw = $code.Replace('PAYLOAD',$payload64).Replace('SCRIPT',$script64) | docker exec -i bankrotai-home-ingestion-worker python -
if ($LASTEXITCODE -ne 0) { throw 'Publication-link repair failed; transaction rolled back' }
$result = ($raw -join [Environment]::NewLine) | ConvertFrom-Json
$result | Add-Member -NotePropertyName selection_file_sha256 -NotePropertyValue $fileSha
[IO.File]::WriteAllText((Join-Path $root $Output),($result | ConvertTo-Json -Depth 30),[Text.UTF8Encoding]::new($false))
$result | Select-Object selected,repairable,committed,reviews_and_notes_preserved,deleted_lots,source_fact_changes
