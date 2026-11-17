param(
  [string]$Evidence = '.artifacts/finish/reports-fresh-details.json',
  [string]$Output = '.artifacts/finish/repair-preview.json'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$module64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root 'src/bankrotai/services/reported_lot_repair.py')))
$filter64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root 'src/bankrotai/services/real_estate_filter.py')))
$evidence64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root $Evidence)))
$code = @'
import types,sys,base64,json
from sqlalchemy import text
from bankrotai.db import SessionLocal
for name,payload in [('bankrotai.services.real_estate_filter','FILTER64'),('bankrotai.services.reported_lot_repair','MODULE64')]:
 module=types.ModuleType(name)
 sys.modules[name]=module
 exec(compile(base64.b64decode(payload),name,'exec'),module.__dict__)
evidence=json.loads(base64.b64decode('EVIDENCE64'))['items']
with SessionLocal() as session:
 session.execute(text('SET TRANSACTION READ ONLY'))
 plan=module.repair_reported_lots(session,evidence=evidence)
 print(json.dumps(plan,ensure_ascii=True,default=str))
'@
$result = $code.Replace('FILTER64', $filter64).Replace('MODULE64', $module64).Replace('EVIDENCE64', $evidence64) | docker exec -i bankrotai-home-ingestion-worker python -
if ($LASTEXITCODE -ne 0) { throw 'Read-only exact report preview failed' }
$result | Set-Content -LiteralPath (Join-Path $root $Output) -Encoding utf8
Write-Output "Read-only repair preview saved: $Output"
