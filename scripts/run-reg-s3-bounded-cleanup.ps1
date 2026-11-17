param(
  [switch]$Apply,
  [string]$Plan = '.artifacts/finish/s3-bounded-cleanup-plan.json',
  [string]$ExpectedPlanSha
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if ($Apply -and $ExpectedPlanSha -notmatch '^[a-fA-F0-9]{64}$') { throw 'Exact approved plan SHA is required' }
$modules = @{}
foreach ($name in @('plan_reg_s3_orphans','s3_capacity_probe','apply_reg_s3_orphans')) {
  $modules[$name] = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root "scripts/$name.py")))
}
$payload = @{ modules = $modules; plan = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root $Plan))); apply = [bool]$Apply; expected_sha = $ExpectedPlanSha }
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 10 -Compress)))
$code = @'
import json,base64,sys,types
p=json.loads(base64.b64decode('PAYLOAD'))
for name in ['plan_reg_s3_orphans','s3_capacity_probe','apply_reg_s3_orphans']:
 m=types.ModuleType(name);sys.modules[name]=m
 exec(compile(base64.b64decode(p['modules'][name]),name,'exec'),m.__dict__)
print(json.dumps(m.execute(base64.b64decode(p['plan']),apply=p['apply'],expected_sha=p['expected_sha'])))
'@
$result = $code.Replace('PAYLOAD',$encoded) | docker exec -i bankrotai-home-map-worker python -
if ($LASTEXITCODE -ne 0) { throw 'S3 cleanup validation/execution failed; inspect the receipt before any retry' }
$name = $(if ($Apply) { 's3-cleanup-result.json' } else { 's3-cleanup-validation.json' })
$result | Set-Content -LiteralPath (Join-Path $root ".artifacts/finish/$name") -Encoding utf8
Write-Output $result
