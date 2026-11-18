[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$ValidateTransaction,
    [string]$Evidence = '.artifacts/finish/tbankrot-systemic-verified-evidence.json',
    [string]$Plan = '.artifacts/finish/tbankrot-systemic-repair-preview.json',
    [string]$Output = '.artifacts/finish/tbankrot-systemic-repair-result.json',
    [string]$BackupMetadata,
    [string]$MaintenanceRunId
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if ($Apply -and -not $ValidateTransaction) {
    if (-not $BackupMetadata) { throw 'Verified restorable database backup is required' }
    $backup = Get-Content -LiteralPath $BackupMetadata -Raw | ConvertFrom-Json
    if ($backup.restore_verification -ne 'passed' -or -not (Test-Path -LiteralPath $backup.backup_file)) {
        throw 'Backup has not passed isolated restore verification'
    }
    if ((Get-FileHash -LiteralPath $backup.backup_file -Algorithm SHA256).Hash -ne $backup.sha256) {
        throw 'Verified backup content changed'
    }
}
$modules = @{}
foreach ($name in @('reported_lot_repair', 'verified_source_repair')) {
    $modules[$name] = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root "src/bankrotai/services/$name.py")))
}
$payload = @{
    apply = [bool]$Apply
    rollback_only = [bool]$ValidateTransaction
    maintenance_run_id = $MaintenanceRunId
    modules = $modules
    evidence = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root $Evidence)))
    expected = $(if ($Apply) { [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root $Plan))) } else { $null })
}
$payload64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 5 -Compress)))
$code = @'
import base64,hashlib,json,sys,types
from datetime import timedelta
from sqlalchemy import select,text
from bankrotai.db import SessionLocal,LotSyncRun,utc_now
p=json.loads(base64.b64decode('PAYLOAD64'))
for leaf in ['reported_lot_repair','verified_source_repair']:
 name='bankrotai.services.'+leaf
 module=types.ModuleType(name);sys.modules[name]=module
 exec(compile(base64.b64decode(p['modules'][leaf]),name,'exec'),module.__dict__)
evidence_bytes=base64.b64decode(p['evidence'])
evidence=json.loads(evidence_bytes.decode('utf-8-sig'))['items']
file_sha=hashlib.sha256(evidence_bytes).hexdigest()
expected=json.loads(base64.b64decode(p['expected']).decode('utf-8-sig')) if p['apply'] else None
if expected is not None and file_sha!=expected['evidence_file_sha256']:
 raise ValueError('Evidence file changed after preview')
scope={item['lot_id'] for item in (expected['items'] if expected else evidence)}
states={item['lot_id']:item['expected_state'] for item in expected['items']} if expected else None
with SessionLocal() as session:
 if not p['apply']:session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
 else:
  session.execute(text("SET LOCAL lock_timeout = '10s'"))
  active=session.scalars(select(LotSyncRun).where(LotSyncRun.status.in_(['queued','running']))).all()
  if active:
   if len(active)!=1 or active[0].id!=p['maintenance_run_id'] or active[0].trigger_type!='source_repair':
    raise RuntimeError('Active ingestion; defer exact source repair')
   if not active[0].lease_expires_at or active[0].lease_expires_at<utc_now().replace(tzinfo=None):
    raise RuntimeError('Source repair maintenance lease expired')
  elif p['maintenance_run_id']:
   raise RuntimeError('Source repair maintenance lease is missing')
 result=module.repair_verified_source_lots(session,evidence=evidence,allowed_lot_ids=scope,
  apply=p['apply'],expected_states=states,
  expected_evidence_sha256=expected['evidence_sha256'] if expected else None)
 result['evidence_file_sha256']=file_sha
 if p['apply']:
  if p['maintenance_run_id']:
   run=session.get(LotSyncRun,p['maintenance_run_id'])
   run.heartbeat_at=utc_now()
   run.lease_expires_at=utc_now()+timedelta(minutes=10)
   run.result_json={**(run.result_json or {}),'last_batch_selected':result['selected'],
    'repaired_records':int((run.result_json or {}).get('repaired_records',0))+result['selected']}
  session.flush()
  if p['rollback_only']:session.rollback()
  else:session.commit()
 result['committed']=bool(p['apply'] and not p['rollback_only'])
 result['deleted_lots']=0
 print(json.dumps(result,default=str,ensure_ascii=True))
'@
$result = $code.Replace('PAYLOAD64', $payload64) | docker exec -i bankrotai-home-ingestion-worker python -
if ($LASTEXITCODE -ne 0) { throw 'Verified repair failed; transaction rolled back' }
[IO.File]::WriteAllText((Join-Path $root $Output), ($result -join [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
$report = ($result -join [Environment]::NewLine) | ConvertFrom-Json
$report | Select-Object selected,blocked,protected,committed,deleted_lots,evidence_sha256
