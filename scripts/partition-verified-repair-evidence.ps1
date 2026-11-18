[CmdletBinding()]
param([Parameter(Mandatory)][string[]]$EvidenceFiles,
      [Parameter(Mandatory)][string]$Prefix)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$items = @($EvidenceFiles | ForEach-Object {
    (Get-Content -LiteralPath (Join-Path $root $_) -Raw | ConvertFrom-Json).items
})
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $items -Depth 40 -Compress)))
$code = @'
import base64,json
from sqlalchemy import select,func,text,and_
from bankrotai.db import SessionLocal,ProcessedLot,SourceLot,CanonicalLot
items=json.loads(base64.b64decode('PAYLOAD'))
ids=[i['lot_id'] for i in items]
if not items or len(ids)!=len(set(ids)):raise ValueError('Evidence must have unique exact lot IDs')
with SessionLocal() as s:
 s.execute(text('SET TRANSACTION READ ONLY'))
 rows=s.execute(select(ProcessedLot.id,SourceLot.canonical_lot_id,CanonicalLot.id).select_from(ProcessedLot)
  .outerjoin(SourceLot,and_(SourceLot.source_system==ProcessedLot.source_system,SourceLot.external_id==ProcessedLot.external_id))
  .outerjoin(CanonicalLot,CanonicalLot.legacy_processed_lot_id==ProcessedLot.id).where(ProcessedLot.id.in_(ids))).all()
 keys={r[0]:{key for key in (r[1],r[2]) if key is not None} or {('unlinked',r[0])} for r in rows}
 if len(keys)!=len(ids):raise ValueError('Missing exact projection')
 cohorts=[];seen=[]
 for item in sorted(items,key=lambda i:i['lot_id']):
  key=keys[item['lot_id']]
  for group,used in zip(cohorts,seen):
   if len(group)<100 and not key.intersection(used):break
  else:
   group=[];used=set();cohorts.append(group);seen.append(used)
  group.append(item);used.update(key)
 print(json.dumps({'cohorts':cohorts,'database_writes':0},ensure_ascii=True))
'@
$raw = $code.Replace('PAYLOAD', $encoded) | docker exec -i bankrotai-home-ingestion-worker python -
if ($LASTEXITCODE -ne 0) { throw 'Read-only canonical grouping failed' }
$groups = ($raw -join [Environment]::NewLine) | ConvertFrom-Json
for ($index = 0; $index -lt $groups.cohorts.Count; $index++) {
    $path = Join-Path $root "$Prefix-$($index+1)-evidence.json"
    $json = ConvertTo-Json -InputObject @{ items = @($groups.cohorts[$index]); database_writes = 0 } -Depth 40
    [IO.File]::WriteAllText($path, $json, [Text.UTF8Encoding]::new($false))
    [pscustomobject]@{ Batch = $index+1; Count = $groups.cohorts[$index].Count; Evidence = $path }
}
