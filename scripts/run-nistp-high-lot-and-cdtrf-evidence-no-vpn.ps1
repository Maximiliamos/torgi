param(
    [string]$EvidenceRoot = 'D:\BankrotAI\etp-evidence\two-source-detail-qa',
    [string]$CdtrfPreviousReport = 'D:\BankrotAI\etp-evidence\two-source-detail-qa\cdtrf-ru-20261105T233118Z.json'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repo
$env:PYTHONPATH = Join-Path $repo 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

Write-Host '=== NISTP high-lot source search + CDTRF existing QA evidence ===' -ForegroundColor Cyan
Write-Host ('Checked-out SHA: ' + ((& git.exe rev-parse HEAD).Trim()))
Write-Host 'NISTP: new bounded read-only public searches; no individual details verified'
Write-Host 'CDTRF: previous successful sample only; no new CDTRF network queries'
Write-Host 'No database writes, import, reconciliation, archival, GEO or map publication'

if (-not (Test-Path -LiteralPath $CdtrfPreviousReport)) {
    throw 'Previous successful CDTRF QA JSON not found'
}
$cdtrf = Get-Content -LiteralPath $CdtrfPreviousReport -Raw -Encoding UTF8 | ConvertFrom-Json
if ($cdtrf.source -ne 'cdtrf.ru' -or
    $cdtrf.mode -ne 'bounded_independent_readonly_lot_detail_sample' -or
    $cdtrf.sample_requested -ne 10 -or
    $cdtrf.sample_verified -ne 10 -or
    $cdtrf.detail_sample_pass -ne $true -or
    $cdtrf.source_accepted -ne $false -or
    $cdtrf.production_database_writes -ne 0 -or
    $cdtrf.archival_allowed -ne $false) {
    throw 'Prior CDTRF proof has unexpected scope or status'
}
Write-Host 'CDTRF prior proof: 10/10 verified; title-review flags:' $cdtrf.source_title_disagreement_review_count

Write-Host '=== Offline regression tests ===' -ForegroundColor Cyan
& python.exe -m pytest -q tests/test_etp_nistp_high_lot_source_form.py
if ($LASTEXITCODE -ne 0) {
    throw 'New NISTP high-lot source-form safety tests failed'
}

Write-Host '=== NISTP read-only high numbered lot search ===' -ForegroundColor Cyan
& python.exe -m bankrotai.services.etp_nistp_high_lot_source_form --evidence-root $EvidenceRoot
$code = $LASTEXITCODE
Write-Host "NISTP source-form probe exit code: $code"
Write-Host "Evidence directory: $EvidenceRoot"
exit $code
