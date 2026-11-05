param(
    [ValidateRange(1, 50)][int]$SampleSize = 30,
    [ValidateRange(1, 10000)][int]$NistpLastPage = 142,
    [ValidateRange(1, 10000)][int]$CdtrfLastPage = 64,
    [ValidateRange(0.5, 10)][double]$DelaySeconds = 1.0,
    [switch]$RecheckFailedOnly,
    [string]$NistpPreviousReport = '',
    [string]$CdtrfPreviousReport = '',
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

# Cheap syntax gate before running any live provider request.
& python.exe -m py_compile `
    (Join-Path $repo 'src/bankrotai/services/etp_two_source_detail_qa.py') `
    (Join-Path $repo 'tests/test_etp_two_source_detail_qa.py')
if ($LASTEXITCODE -ne 0) {
    throw 'Read-only detail QA failed Python syntax preflight'
}
New-Item -ItemType Directory -Path $EvidenceRoot -Force | Out-Null
Write-Host '====== NISTP + CDTRF: READ-ONLY DETAIL SAMPLE ======'
Write-Host "Checked-out SHA: $revision"
if ($RecheckFailedOnly) {
    Write-Host "Mode: original-ID-pinned failed-lot recheck; last pages NISTP=$NistpLastPage CDTRF=$CdtrfLastPage"
} else {
    Write-Host "Mode: fresh stratified bounded sample; target per source: $SampleSize; last pages NISTP=$NistpLastPage CDTRF=$CdtrfLastPage"
}
Write-Host 'No PostgreSQL, reconcile, archive, GEO or map publication.'

$arguments = @(
    '-m', 'bankrotai.services.etp_two_source_detail_qa',
    '--source', 'both',
    '--nistp-last-page', "$NistpLastPage",
    '--cdtrf-last-page', "$CdtrfLastPage",
    '--sample-size', "$SampleSize",
    '--delay-seconds', "$DelaySeconds",
    '--evidence-root', "$EvidenceRoot"
)
if ($RecheckFailedOnly) {
    if (-not $NistpPreviousReport) {
        $previous = Get-ChildItem -LiteralPath $EvidenceRoot -Filter 'nistp-ru-*.json' -File |
            Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
        if (-not $previous) { throw 'Previous NISTP detail QA JSON not found' }
        $NistpPreviousReport = $previous.FullName
    }
    if (-not $CdtrfPreviousReport) {
        $previous = Get-ChildItem -LiteralPath $EvidenceRoot -Filter 'cdtrf-ru-*.json' -File |
            Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
        if (-not $previous) { throw 'Previous CDTRF detail QA JSON not found' }
        $CdtrfPreviousReport = $previous.FullName
    }
    Write-Host "Rechecking NISTP failure pages from: $NistpPreviousReport"
    Write-Host "Rechecking CDTRF failure pages from: $CdtrfPreviousReport"
    $arguments += @(
        '--recheck-nistp-report', "$NistpPreviousReport",
        '--recheck-cdtrf-report', "$CdtrfPreviousReport"
    )
}
& python.exe @arguments
$exitCode = $LASTEXITCODE
Write-Host "Evidence directory: $EvidenceRoot"
Write-Host "Diagnostic exit code: $exitCode"
exit $exitCode
