param(
    [ValidateSet(20, 50, 100)]
    [int]$PageSize = 100,
    [string]$EvidenceRoot = 'D:\BankrotAI\etp-evidence\cdtrf-full-readonly'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
$revision = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $revision) {
    throw 'Cannot determine the checked-out source revision'
}
$python = Get-Command python.exe -ErrorAction Stop
$env:PYTHONPATH = (Resolve-Path (Join-Path $repoRoot 'src')).Path
New-Item -ItemType Directory -Force -Path $EvidenceRoot | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$reports = @()

Write-Output 'CDTRF two-pass full read-only scan from the checked-out revision.'
Write-Output 'No PostgreSQL writes, reconciliation, archival, GEO or map publication.'
Write-Output ("CDTRF scanner revision: {0}; page_size={1}" -f $revision, $PageSize)

foreach ($pass in @(1, 2)) {
    $log = Join-Path $EvidenceRoot ("cdtrf-pass-{0}-{1}.log" -f $pass, $stamp)
    $jsonPath = Join-Path $EvidenceRoot ("cdtrf-pass-{0}-{1}.json" -f $pass, $stamp)
    Write-Output ("CDTRF independent pass {0}/2 started; progress follows page by page." -f $pass)
    $ErrorActionPreference = 'Continue'
    $output = @(& $python.Source -m bankrotai.services.etp_cdtrf_full_catalogue `
        --page-size $PageSize --max-pages 10000 --delay-seconds 1 --progress 2>&1 `
        | Tee-Object -FilePath $log)
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($exitCode -ne 0) {
        throw ("CDTRF read-only pass {0} failed; log={1}" -f $pass, $log)
    }
    $jsonLine = @($output | ForEach-Object { $_.ToString() } |
        Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1)
    if ($jsonLine.Count -ne 1) {
        throw ("CDTRF pass {0} returned no JSON report; log={1}" -f $pass, $log)
    }
    $report = $jsonLine[0] | ConvertFrom-Json
    if ($report.pages_scanned -ne $report.pages_expected -or
        $report.unverified_coverage_gap -ne 0 -or
        $report.cross_page_overlap_count -ne 0 -or
        $report.production_database_writes -ne 0 -or
        $report.archival_allowed -ne $false -or
        $report.reconcile_missing -ne $false -or
        $report.map_publication -ne $false) {
        throw ("CDTRF pass {0} failed structural acceptance; log={1}" -f $pass, $log)
    }
    $report | Add-Member -NotePropertyName source_revision -NotePropertyValue $revision
    $report | Add-Member -NotePropertyName independent_pass -NotePropertyValue $pass
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $jsonPath -Encoding UTF8
    $reports += $report
    Write-Output ("CDTRF pass {0}/2 complete: pages={1}, IDs={2}, digest={3}" -f `
        $pass, $report.pages_scanned, $report.unique_catalogue_lot_ids, $report.lot_ids_sha256)
}

$matches = ($reports.Count -eq 2 -and
    $reports[0].unique_catalogue_lot_ids -eq $reports[1].unique_catalogue_lot_ids -and
    $reports[0].lot_ids_sha256 -eq $reports[1].lot_ids_sha256)
$comparisonPath = Join-Path $EvidenceRoot ("cdtrf-two-pass-{0}.json" -f $stamp)
[ordered]@{
    source_revision = $revision
    pass_1_ids = $reports[0].unique_catalogue_lot_ids
    pass_2_ids = $reports[1].unique_catalogue_lot_ids
    pass_1_sha256 = $reports[0].lot_ids_sha256
    pass_2_sha256 = $reports[1].lot_ids_sha256
    two_pass_catalogue_match = $matches
    production_database_writes = 0
    archival_allowed = $false
} | ConvertTo-Json | Set-Content -LiteralPath $comparisonPath -Encoding UTF8

if (-not $matches) {
    throw ("CDTRF passes completed but differ; evidence={0}" -f $comparisonPath)
}
Write-Output ("CDTRF TWO-PASS READ-ONLY PASS; evidence={0}" -f $comparisonPath)
