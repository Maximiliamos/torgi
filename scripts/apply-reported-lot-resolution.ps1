param(
  [switch]$Apply,
  [string]$Evidence = '.artifacts/finish/reports-fresh-details.json',
  [string]$Plan = '.artifacts/finish/resolution-preview.json',
  [string]$Output = '.artifacts/finish/resolution-result.json',
  [string]$BackupMetadata
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if ($Apply) {
  if (-not $BackupMetadata) { throw 'A verified database backup is required before physical removal' }
  $backup = Get-Content -LiteralPath $BackupMetadata -Raw | ConvertFrom-Json
  if ($backup.restore_verification -ne 'passed' -or -not (Test-Path -LiteralPath $backup.backup_file)) {
    throw 'Database backup has not passed isolated restore verification'
  }
  if ((Get-FileHash -LiteralPath $backup.backup_file -Algorithm SHA256).Hash -ne $backup.sha256) {
    throw 'Database backup hash differs from the verified manifest'
  }
}
$modules = @{}
foreach ($leaf in @('real_estate_filter', 'reported_lot_repair', 'reported_lot_removal')) {
  $modules["bankrotai.services.$leaf"] = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root "src/bankrotai/services/$leaf.py")))
}
$payload = @{
  apply = [bool]$Apply
  modules = $modules
  evidence = (Get-Content -LiteralPath (Join-Path $root $Evidence) -Raw | ConvertFrom-Json).items
  expected = $(if ($Apply) { Get-Content -LiteralPath (Join-Path $root $Plan) -Raw | ConvertFrom-Json } else { $null })
}
$payload64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 100 -Compress)))
$code = @'
import base64,json,sys,types
from sqlalchemy import select,text
from bankrotai.db import SessionLocal,ProcessedLot,LotSyncRun
p=json.loads(base64.b64decode('PAYLOAD64'))
for name in ['real_estate_filter','reported_lot_repair','reported_lot_removal']:
 fullname='bankrotai.services.'+name
 module=types.ModuleType(fullname)
 sys.modules[fullname]=module
 exec(compile(base64.b64decode(p['modules'][fullname]),fullname,'exec'),module.__dict__)
repair=sys.modules['bankrotai.services.reported_lot_repair'].repair_reported_lots
remove=module.remove_reviewed_reported_lots
with SessionLocal() as session:
 if not p['apply']:session.execute(text('SET TRANSACTION READ ONLY'))
 else:
  session.execute(text("SET LOCAL lock_timeout = '10s'"))
  if session.scalar(select(LotSyncRun.id).where(LotSyncRun.status.in_(['queued','running'])).limit(1)):
   raise RuntimeError('Active ingestion exists; refuse concurrent physical removal')
 if p['apply']:
  selected=p['expected']['remove']['items']
  delete_ids=[item['lot_id'] for item in selected]
  states={item['lot_id']:item['expected_state'] for item in selected}
  repair_items=p['expected']['repair']['items']
  repair_states={item['lot_id']:item['expected_state'] for item in repair_items}
  repair_ids=set(repair_states)
 else:
  ids=[item['lot_id'] for item in p['evidence']]
  rows=list(session.scalars(select(ProcessedLot).where(ProcessedLot.id.in_(ids))))
  delete_ids=[row.id for row in rows if row.review_status is not None]
  repair_ids={row.id for row in rows if row.review_status is None}
  states=repair_states=None
 result={'apply':p['apply'],'remove':remove(session,lot_ids=delete_ids,apply=False),
         'repair':repair(session,evidence=[item for item in p['evidence'] if item['lot_id'] in repair_ids])}
 if p['apply']:
  # Validate both cohorts before committing a single atomic transaction.
  result['repair']=repair(session,evidence=[item for item in p['evidence'] if item['lot_id'] in repair_ids],
                          apply=True,expected_states=repair_states)
  result['remove']=remove(session,lot_ids=delete_ids,apply=True,expected_states=states)
  session.commit()
 print(json.dumps(result,ensure_ascii=True,default=str))
'@
$result = $code.Replace('PAYLOAD64', $payload64) | docker exec -i bankrotai-home-ingestion-worker python -
if ($LASTEXITCODE -ne 0) { throw 'Exact report resolution failed; transaction was not committed' }
$target = $(if ($Apply) { $Output } else { $Plan })
$result | Set-Content -LiteralPath (Join-Path $root $target) -Encoding utf8
Write-Output "Exact report resolution saved: $target"
