param(
    [ValidateRange(1, 50)][int]$SampleSize = 30,
    [ValidateRange(1, 10000)][int]$NistpLastPage = 142,
    [ValidateRange(1, 10000)][int]$CdtrfLastPage = 64,
    [ValidateRange(0.5, 10)][double]$DelaySeconds = 1.0,
    [string]$EvidenceRoot = 'D:\BankrotAI\etp-evidence\two-source-detail-qa'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repo
$revision = (& git.exe rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $revision) {
    throw 'Unable to record exact source revision'
}
$env:PYTHONPATH = Join-Path $repo 'src'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

New-Item -ItemType Directory -Path $EvidenceRoot -Force | Out-Null
Write-Host '====== NISTP + CDTRF: READ-ONLY DETAIL SAMPLE ======'
Write-Host "Checked-out SHA: $revision"
Write-Host "Sample per source: $SampleSize; NISTP last page: $NistpLastPage; CDTRF last page: $CdtrfLastPage"
Write-Host 'No PostgreSQL, reconcile, archive, GEO or map publication.'

& python.exe -m bankrotai.services.etp_two_source_detail_qa `
    --source both `
    --nistp-last-page $NistpLastPage `
    --cdtrf-last-page $CdtrfLastPage `
    --sample-size $SampleSize `
    --delay-seconds $DelaySeconds `
    --evidence-root $EvidenceRoot
$exitCode = $LASTEXITCODE
Write-Host "Evidence directory: $EvidenceRoot"
Write-Host "Diagnostic exit code: $exitCode"
exit $exitCode
