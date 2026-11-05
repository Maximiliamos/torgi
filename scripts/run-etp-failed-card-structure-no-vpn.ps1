param(
    [string]$NistpPreviousReport = 'D:\BankrotAI\etp-evidence\two-source-detail-qa\nistp-ru-20261105T222934Z.json',
    [string]$CdtrfPreviousReport = 'D:\BankrotAI\etp-evidence\two-source-detail-qa\cdtrf-ru-20261105T222934Z.json',
    [string]$EvidenceRoot = 'D:\BankrotAI\etp-evidence\two-source-detail-qa'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repo
$env:PYTHONPATH = Join-Path $repo 'src'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

if (-not (Test-Path -LiteralPath $NistpPreviousReport) -or
    -not (Test-Path -LiteralPath $CdtrfPreviousReport)) {
    throw 'The original NISTP/CDTRF failure-only JSON reports are missing'
}

Write-Host '=== READ-ONLY EXACT CARD STRUCTURAL EVIDENCE: NISTP + CDTRF ==='
Write-Host ('SHA: ' + ((& git.exe rev-parse HEAD).Trim()))
Write-Host "NISTP evidence: $NistpPreviousReport"
Write-Host "CDTRF evidence: $CdtrfPreviousReport"
Write-Host 'No production DB, reconciliation, archival, GEO or map publication.'

& python.exe -m pytest -q tests/test_etp_failed_card_structure.py
if ($LASTEXITCODE -ne 0) {
    throw 'Structural evidence unit tests failed: no source requests were made'
}
& python.exe -m bankrotai.services.etp_failed_card_structure `
    --nistp-report $NistpPreviousReport `
    --cdtrf-report $CdtrfPreviousReport `
    --evidence-root $EvidenceRoot
$code = $LASTEXITCODE
Write-Host "Read-only probe exit code: $code"
Write-Host "Evidence: $EvidenceRoot"
exit $code
