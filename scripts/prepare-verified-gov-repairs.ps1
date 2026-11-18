[CmdletBinding()]
param([string]$Journal = '.artifacts/finish/torgi-gov-primary-verification.jsonl',
      [ValidateRange(1,5000)][int]$Maximum = 1000,
      [Parameter(Mandatory)][string]$Prefix)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$applied = [Collections.Generic.HashSet[int]]::new()
foreach ($file in Get-ChildItem -LiteralPath (Join-Path $root '.artifacts/finish') -Filter 'gov-safe-*-result.json') {
    $result = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json
    if ($result.committed) { foreach ($item in $result.items) { [void]$applied.Add([int]$item.lot_id) } }
}
$rows = @(Get-Content -LiteralPath (Join-Path $root $Journal) | Where-Object { $_.EndsWith('}') } |
    ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.identity_verified -and -not $applied.Contains([int]$_.lot_id) } |
    Group-Object external_id | ForEach-Object { $_.Group[-1] } | Select-Object -First $Maximum)
$script64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root 'scripts/prepare_verified_gov_repair.py')))
for ($offset = 0; $offset -lt $rows.Count; $offset += 100) {
    $batch = [math]::Floor($offset / 100) + 1
    $snapshots = @($rows | Select-Object -Skip $offset -First 100 | ForEach-Object {
        $native = $_.external_id.Split(':')[1]
        $file = Join-Path $root ".artifacts/finish/gov-primary-json/$native-$($_.sha256).json"
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $_.sha256) { throw 'Primary snapshot SHA mismatch' }
        @{ external_id = $_.external_id; lot_id = $_.lot_id; sha256 = $_.sha256; checked_at_utc = $_.checked_at_utc;
           snapshot_base64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($file)) }
    })
    $payload64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $snapshots -Depth 5 -Compress)))
    $code = "import base64,io,sys;sys.stdin=io.StringIO(base64.b64decode('$payload64').decode());exec(compile(base64.b64decode('$script64'),'prepare_verified_gov_repair.py','exec'))"
    $result = $code | docker exec -i bankrotai-home-ingestion-worker python -
    if ($LASTEXITCODE -ne 0) { throw "Read-only primary preparation failed for batch $batch" }
    $output = Join-Path $root "$Prefix-$batch-evidence.json"
    [IO.File]::WriteAllText($output, ($result -join [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
    [pscustomobject]@{ Batch = $batch; Count = $snapshots.Count; Evidence = $output; DatabaseWrites = 0 }
}
