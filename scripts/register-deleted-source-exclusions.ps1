[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$Resolution = '.artifacts/finish/resolution-result.json',
    [string]$ExpectedSha256,
    [string]$Plan = '.artifacts/finish/deleted-source-exclusions-preview.json',
    [string]$BackupMetadata,
    [string]$Output = '.artifacts/finish/deleted-source-exclusions.json'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$path = Join-Path $root $Resolution
$sha = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
if ($Apply -and $ExpectedSha256 -ne $sha) { throw 'Explicit deletion manifest SHA is required and must match' }
if ($Apply) {
    $backup = Get-Content -LiteralPath $BackupMetadata -Raw | ConvertFrom-Json
    if ($backup.restore_verification -ne 'passed' -or
        (Get-FileHash -LiteralPath $backup.backup_file -Algorithm SHA256).Hash -ne $backup.sha256) {
        throw 'Verified restorable backup is required'
    }
}
$manifest = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
if (-not $manifest.committed -or $manifest.remove.deleted_lots -ne 38 -or $manifest.remove.items.Count -ne 38) {
    throw 'Expected the committed original 38-card deletion manifest'
}
$payload = @{ apply = [bool]$Apply; sha256 = $sha; items = @($manifest.remove.items | Select-Object source_system,external_id)
    expected = $(if ($Apply) { Get-Content -LiteralPath (Join-Path $root $Plan) -Raw | ConvertFrom-Json } else { $null }) }
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 5 -Compress)))
$code = @'
import base64,json
from sqlalchemy import select
from bankrotai.db import SessionLocal,SourceLot,ProcessedLot,LotSyncRun,utc_now
from bankrotai.services.source_lot_exclusions import excluded_ids,register_exclusion
from bankrotai.services.reported_lot_repair import _state
p=json.loads(base64.b64decode('PAYLOAD'))
identities={(i['source_system'],i['external_id']) for i in p['items']}
if len(identities)!=38:raise ValueError('Exact 38 unique identities required')
with SessionLocal() as session:
 if p['apply'] and session.scalar(select(LotSyncRun.id).where(LotSyncRun.status.in_(['running','queued'])).limit(1)):
  raise ValueError('Active ingestion; defer exclusion registration')
 states={}
 reappeared=[]
 existing=0
 for source,external in sorted(identities):
  records=[]
  for model in (ProcessedLot,SourceLot):
   query=select(model).where(model.source_system==source,model.external_id==external)
   records.append(session.scalar(query.with_for_update() if p['apply'] else query))
  processed,origin=records
  if origin is not None and (processed is None or origin.processed_lot_id not in (None,processed.id)):
   raise ValueError('Unexpected recreated source/projection relationship')
  key=source+'|'+external
  states[key]=_state(processed,origin)
  if p['apply'] and p['expected']['states'].get(key)!=states[key]:
   raise ValueError('Exact exclusion preview is stale')
  if processed is not None:
   reappeared.append({'lot_id':processed.id,'source_system':source,'external_id':external,
                     'review_status':processed.review_status})
   if p['apply']:
    # Preserve source facts and user data. This is an explicit user exclusion,
    # not a claim that the auction has ended or that the new card was verified.
    processed.is_archived=True
    processed.archived_at=processed.archived_at or utc_now()
    processed.last_update=utc_now()
    if origin is not None:
     origin.is_active=False;origin.is_archived=True
     origin.archived_at=origin.archived_at or utc_now()
     origin.archive_reason='explicit_user_deleted_source_identity'
  present=bool(excluded_ids(session,source,[external]))
  existing+=int(present)
  if p['apply'] and not present:
   register_exclusion(session,source_system=source,external_id=external,
    reason='Explicit user deletion of original incorrect reviewed card; resolution SHA256 '+p['sha256'])
 if p['apply']:session.commit()
 print(json.dumps({'selected':38,'already_registered':existing,'registered':38-existing if p['apply'] else 0,
  'committed':p['apply'],'deleted_lots':0,'manifest_sha256':p['sha256'],
  'reappeared':reappeared,'states':states,'quarantined':len(reappeared) if p['apply'] else 0}))
'@
$result = $code.Replace('PAYLOAD', $encoded) | docker exec -i bankrotai-home-ingestion-worker python -
if ($LASTEXITCODE -ne 0) { throw 'Exclusion registration failed and rolled back' }
[IO.File]::WriteAllText((Join-Path $root $Output), ($result -join [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
$result
