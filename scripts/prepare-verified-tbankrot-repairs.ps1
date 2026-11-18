[CmdletBinding()]
param([string]$Journal = '.artifacts/finish/tbankrot-global-candidate-verification.jsonl')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$rows = @(Get-Content -LiteralPath (Join-Path $root $Journal) | ForEach-Object { $_ | ConvertFrom-Json } |
    Where-Object { $_.identity_verified } | Group-Object external_id | ForEach-Object { $_.Group[-1] })
$script64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root 'scripts/prepare_verified_tbankrot_repair.py')))
for ($offset = 0; $offset -lt $rows.Count; $offset += 100) {
    $batch = [math]::Floor($offset / 100) + 1
    $snapshots = @($rows | Select-Object -Skip $offset -First 100 | ForEach-Object {
        $native = $_.external_id.Split(':')[1]
        $file = Join-Path $root ".artifacts/finish/tbankrot-public-candidate-html/$native-$($_.sha256).html"
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $_.sha256) { throw 'Snapshot SHA mismatch' }
        @{ external_id = $_.external_id; lot_id = $_.lot_id; source_url = $_.source_url;
           sha256 = $_.sha256; checked_at_utc = $_.checked_at_utc;
           snapshot_base64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($file)) }
    })
    $payload64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $snapshots -Depth 5 -Compress)))
    $code = "import base64,io,sys;sys.stdin=io.StringIO(base64.b64decode('$payload64').decode());exec(compile(base64.b64decode('$script64'),'prepare_verified_tbankrot_repair.py','exec'))"
    $result = $code | docker exec -i bankrotai-home-ingestion-worker python -
    if ($LASTEXITCODE -ne 0) { throw "Read-only preparation failed for batch $batch" }
    $output = Join-Path $root ".artifacts/finish/tbankrot-verified-batch-$batch-evidence.json"
    [IO.File]::WriteAllText($output, ($result -join [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
    [pscustomobject]@{ Batch = $batch; Count = $snapshots.Count; Evidence = $output; DatabaseWrites = 0 }
}
