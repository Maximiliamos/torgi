[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$Resolution = '.artifacts/finish/resolution-result.json',
    [string]$ExpectedSha256,
    [string]$Output = '.artifacts/finish/deleted-source-exclusions.json'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$path = Join-Path $root $Resolution
$sha = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
if ($Apply -and $ExpectedSha256 -ne $sha) { throw 'Explicit deletion manifest SHA is required and must match' }
$manifest = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
if (-not $manifest.committed -or $manifest.remove.deleted_lots -ne 38 -or $manifest.remove.items.Count -ne 38) {
    throw 'Expected the committed original 38-card deletion manifest'
}
$payload = @{ apply = [bool]$Apply; sha256 = $sha; items = @($manifest.remove.items | Select-Object source_system,external_id) }
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 5 -Compress)))
$code = @'
import base64,json
from sqlalchemy import select
from bankrotai.db import SessionLocal,SourceLot,ProcessedLot
from bankrotai.services.source_lot_exclusions import excluded_ids,register_exclusion
p=json.loads(base64.b64decode('PAYLOAD'))
identities={(i['source_system'],i['external_id']) for i in p['items']}
if len(identities)!=38:raise ValueError('Exact 38 unique identities required')
with SessionLocal() as session:
 existing=0
 for source,external in sorted(identities):
  for model in (SourceLot,ProcessedLot):
   if session.scalar(select(model.id).where(model.source_system==source,model.external_id==external).limit(1)):
    raise ValueError('Deleted identity already reappeared; requires separate source-backed review')
  present=bool(excluded_ids(session,source,[external]))
  existing+=int(present)
  if p['apply'] and not present:
   register_exclusion(session,source_system=source,external_id=external,
    reason='Explicit user deletion of original incorrect reviewed card; resolution SHA256 '+p['sha256'])
 if p['apply']:session.commit()
 print(json.dumps({'selected':38,'already_registered':existing,'registered':38-existing if p['apply'] else 0,
  'committed':p['apply'],'deleted_lots':0,'manifest_sha256':p['sha256']}))
'@
$result = $code.Replace('PAYLOAD', $encoded) | docker exec -i bankrotai-home-ingestion-worker python -
if ($LASTEXITCODE -ne 0) { throw 'Exclusion registration failed and rolled back' }
[IO.File]::WriteAllText((Join-Path $root $Output), ($result -join [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
$result
