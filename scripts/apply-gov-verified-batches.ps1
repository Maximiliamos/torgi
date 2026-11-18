[CmdletBinding()]
param([string]$Folder = '.artifacts/finish/gov-entire-verified',
      [Parameter(Mandatory)][string]$BackupMetadata,
      [Parameter(Mandatory)][string]$ExpectedDeploymentSha,
      [int]$MaximumGroups = 0)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$image = (docker inspect --format '{{.Config.Image}}' bankrotai-home-ingestion-worker).Trim()
if ($image -ne "bankrotai-home-api:$ExpectedDeploymentSha") { throw 'Worker is not the expected accepted deployment' }
$manifest = Get-Content -LiteralPath (Join-Path $root "$Folder/manifest.json") -Raw | ConvertFrom-Json
if ($manifest.selected -ne 61872 -or $manifest.database_writes -ne 0) { throw 'Expected the complete exact-ID GIS evidence manifest' }
$backup = Get-Content -LiteralPath $BackupMetadata -Raw | ConvertFrom-Json
if ($backup.restore_verification -ne 'passed' -or
    (Get-FileHash -LiteralPath $backup.backup_file -Algorithm SHA256).Hash -ne $backup.sha256) {
    throw 'Verified restorable backup is required before maintenance'
}
$begin = @'
import hashlib,json
from sqlalchemy import select
from bankrotai.db import SessionLocal,LotSyncRun,ProcessedLot,LotNote,utc_now
from bankrotai.services.ingestion import NationwideIngestionService
def digest(s):
 reviews=[tuple(r) for r in s.execute(select(ProcessedLot.id,ProcessedLot.review_status,ProcessedLot.rating).where(ProcessedLot.review_status.isnot(None)).order_by(ProcessedLot.id))]
 notes=[tuple(r) for r in s.execute(select(LotNote.id,LotNote.lot_id,LotNote.user_id,LotNote.content).order_by(LotNote.id))]
 return hashlib.sha256(json.dumps([reviews,notes],default=str).encode()).hexdigest()
service=NationwideIngestionService(SessionLocal)
run_id=service.create_run(triggered_by='verified_official_GIS_evidence',trigger_type='source_repair',total_sources=1)
with SessionLocal() as s:
 run=s.get(LotSyncRun,run_id);run.status='running';run.started_at=utc_now()
 run.result_json={'operation':'verified_source_repair','missing_lot_reconciliation':False,'deleted_lots':0,'repaired_records':0,'reviews_and_notes_digest':digest(s)}
 s.commit()
 print(json.dumps({'run_id':run_id,'kind':'source_repair','map_publication_deferred':True,'deleted_lots':0}))
'@
$raw = $begin | docker exec -i bankrotai-home-ingestion-worker python -
if ($LASTEXITCODE -ne 0) { throw 'Could not acquire source-repair maintenance lease' }
$run = ($raw -join [Environment]::NewLine) | ConvertFrom-Json
[IO.File]::WriteAllText((Join-Path $root "$Folder/maintenance-run.json"), ($raw -join [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
$completed = $false
try {
    $groups = @(Get-ChildItem -LiteralPath (Join-Path $root $Folder) -Directory -Filter 'group-*' | Sort-Object Name)
    if ($MaximumGroups -gt 0) { $groups = @($groups | Select-Object -First $MaximumGroups) }
    foreach ($group in $groups) {
        $relative = "$Folder/$($group.Name)"
        $inputs = @(Get-ChildItem -LiteralPath $group.FullName -Filter 'input-*-evidence.json' | ForEach-Object { "$relative/$($_.Name)" })
        if (-not @(Get-ChildItem -LiteralPath $group.FullName -Filter 'safe-v2-*-evidence.json').Count) {
            & (Join-Path $root 'scripts/partition-verified-repair-evidence.ps1') -EvidenceFiles $inputs -Prefix "$relative/safe-v2" | Out-Null
        }
        foreach ($file in Get-ChildItem -LiteralPath $group.FullName -Filter 'safe-v2-*-evidence.json' | Sort-Object Name) {
            $prefix = "$relative/" + ($file.Name -replace '-evidence.json$', '')
            $resultPath = Join-Path $root "$prefix-result.json"
            if (Test-Path -LiteralPath $resultPath) {
                $previous = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
                $currentSha = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
                if ($previous.committed -and $previous.evidence_file_sha256 -eq $currentSha) { continue }
                throw 'Existing batch result does not match its immutable evidence file'
            }
            & (Join-Path $root 'scripts/apply-verified-source-repair.ps1') -Evidence "$prefix-evidence.json" -Output "$prefix-preview.json" | Out-Null
            & (Join-Path $root 'scripts/apply-verified-source-repair.ps1') -Apply -ValidateTransaction -Evidence "$prefix-evidence.json" -Plan "$prefix-preview.json" -Output "$prefix-trial.json" -MaintenanceRunId $run.run_id | Out-Null
            & (Join-Path $root 'scripts/apply-verified-source-repair.ps1') -Apply -Evidence "$prefix-evidence.json" -Plan "$prefix-preview.json" -Output "$prefix-result.json" -BackupMetadata $BackupMetadata -MaintenanceRunId $run.run_id | Out-Null
            $result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
            @{group=$group.Name; batch=$file.Name; selected=$result.selected; committed=$result.committed; deleted_lots=0} | ConvertTo-Json -Compress
        }
    }
    $completed = $true
} finally {
    $finish = @'
import hashlib,json
from sqlalchemy import select
from bankrotai.db import SessionLocal,LotSyncRun,ProcessedLot,LotNote,utc_now
from bankrotai.tasks import _schedule_map_dataset_build
with SessionLocal() as s:
 run=s.get(LotSyncRun,'RUN_ID')
 if run is None or run.trigger_type!='source_repair':raise ValueError('Maintenance identity mismatch')
 reviews=[tuple(r) for r in s.execute(select(ProcessedLot.id,ProcessedLot.review_status,ProcessedLot.rating).where(ProcessedLot.review_status.isnot(None)).order_by(ProcessedLot.id))]
 notes=[tuple(r) for r in s.execute(select(LotNote.id,LotNote.lot_id,LotNote.user_id,LotNote.content).order_by(LotNote.id))]
 actual=hashlib.sha256(json.dumps([reviews,notes],default=str).encode()).hexdigest()
 preserved=actual==run.result_json['reviews_and_notes_digest']
 run.status='success' if COMPLETED and preserved else 'failed'
 run.finished_at=run.heartbeat_at=utc_now();run.lease_expires_at=None
 run.result_json={**run.result_json,'status':run.status,'reviews_and_notes_preserved':preserved,'after_digest':actual}
 if not COMPLETED:run.error_message='Bounded source repair stopped; inspect exact batch logs, no reconciliation performed'
 s.commit();print(json.dumps({'run_id':run.id,**run.result_json},default=str))
print(json.dumps({'map_build':_schedule_map_dataset_build()},default=str))
if not preserved:raise SystemExit('Reviews/notes changed; inspect concurrent user actions before proceeding')
'@
    $finish = $finish.Replace('RUN_ID', $run.run_id).Replace('COMPLETED', $(if ($completed) { 'True' } else { 'False' }))
    $final = $finish | docker exec -i bankrotai-home-ingestion-worker python -
    [IO.File]::WriteAllText((Join-Path $root "$Folder/maintenance-result.jsonl"), ($final -join [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
    if ($LASTEXITCODE -ne 0) { throw 'Maintenance final verification failed' }
}
