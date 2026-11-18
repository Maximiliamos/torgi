param(
  [switch]$Apply,
  [switch]$ValidateTransaction,
  [switch]$AllowReviewedSourceFields,
  [string]$Evidence = '.artifacts/finish/verified-source-evidence.json',
  [string]$Plan = '.artifacts/finish/verified-source-repair-preview.json',
  [string]$Output = '.artifacts/finish/verified-source-repair-result.json',
  [string]$BackupMetadata
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

if ($Apply) {
  if (-not $BackupMetadata) {
    throw 'A verified database backup is required before verified source repair'
  }
  $backup = Get-Content -LiteralPath $BackupMetadata -Raw | ConvertFrom-Json
  if ($backup.restore_verification -ne 'passed' -or -not (Test-Path -LiteralPath $backup.backup_file)) {
    throw 'Database backup has not passed isolated restore verification'
  }
  if ((Get-FileHash -LiteralPath $backup.backup_file -Algorithm SHA256).Hash -ne $backup.sha256) {
    throw 'Database backup hash differs from the verified manifest'
  }
}

$modulePath = Join-Path $root 'src/bankrotai/services/verified_source_repair.py'
$evidencePath = Join-Path $root $Evidence
if (-not (Test-Path -LiteralPath $evidencePath)) {
  throw "Evidence file does not exist: $evidencePath"
}

$module64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($modulePath))
$evidence64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($evidencePath))
$expected64 = ''
if ($Apply) {
  $planPath = Join-Path $root $Plan
  if (-not (Test-Path -LiteralPath $planPath)) {
    throw "Preview plan does not exist: $planPath"
  }
  $expected64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($planPath))
}

$payload = @{
  apply = [bool]$Apply
  rollback_only = [bool]$ValidateTransaction
  allow_reviewed = [bool]$AllowReviewedSourceFields
  module = $module64
  evidence = $evidence64
  expected = $expected64
}
$payload64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Compress))
)

$code = @'
import base64,json,sys,types
from sqlalchemy import select,text
from bankrotai.db import LotSyncRun,SessionLocal

p=json.loads(base64.b64decode("PAYLOAD64"))
name="bankrotai.services.verified_source_repair"
module=types.ModuleType(name)
sys.modules[name]=module
exec(compile(base64.b64decode(p["module"]),name,"exec"),module.__dict__)

evidence=json.loads(base64.b64decode(p["evidence"]).decode("utf-8-sig"))
items=evidence["items"] if isinstance(evidence,dict) else evidence
if not isinstance(items,list):
    raise ValueError("Evidence must contain an items list")

with SessionLocal() as session:
    if not p["apply"]:
        session.execute(text("SET TRANSACTION READ ONLY"))
        result=module.repair_verified_source_lots(
            session,
            evidence=items,
            allow_reviewed_source_fields=p["allow_reviewed"],
        )
        session.rollback()
    else:
        session.execute(text("SET LOCAL lock_timeout = '10s'"))
        if session.scalar(
            select(LotSyncRun.id)
            .where(LotSyncRun.status.in_(["queued","running"]))
            .limit(1)
        ):
            raise RuntimeError("Active ingestion exists; refuse concurrent verified repair")
        expected=json.loads(base64.b64decode(p["expected"]).decode("utf-8-sig"))
        states={
            int(item["lot_id"]):item["expected_state"]
            for item in expected["items"]
            if item.get("lot_id") is not None and item.get("expected_state")
        }
        result=module.repair_verified_source_lots(
            session,
            evidence=items,
            apply=True,
            expected_states=states,
            allow_reviewed_source_fields=p["allow_reviewed"],
        )
        if p["rollback_only"]:
            session.rollback()
            result["committed"]=False
        else:
            session.commit()
            result["committed"]=True

print(json.dumps(result,ensure_ascii=True,default=str))
'@

$result = $code.Replace('PAYLOAD64', $payload64) |
  docker exec -i bankrotai-home-ingestion-worker python -

if ($LASTEXITCODE -ne 0) {
  throw 'Verified source repair failed; transaction was not committed'
}

$target = if ($Apply) { $Output } else { $Plan }
$result | Set-Content -LiteralPath (Join-Path $root $target) -Encoding utf8
Write-Output "Verified source repair saved: $target"
