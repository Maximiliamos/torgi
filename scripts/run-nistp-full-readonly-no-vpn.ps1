param(
    [int]$MaxPages = 10000,
    [double]$DelaySeconds = 1.0
)

$ErrorActionPreference = 'Stop'
$root = 'D:\BankrotAI\etp-evidence\nistp-full-readonly'
New-Item -ItemType Directory -Path $root -Force | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$log = Join-Path $root "nistp-two-pass-$stamp.log"
$reportPath = Join-Path $root "nistp-two-pass-$stamp.json"

Write-Host 'NISTP full read-only scan from the checked-out source revision.'
Write-Host 'No PostgreSQL writes, reconciliation, archival, GEO or map publication.'
$revision = (& git.exe rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $revision) {
    throw 'Could not record the exact scanner revision'
}
Write-Host "NISTP read-only scanner revision: $revision"

$savedErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $output = @(& python.exe -m `
        bankrotai.services.etp_nistp_full_catalogue `
        --max-pages $MaxPages --delay-seconds $DelaySeconds 2>&1 |
        Tee-Object -FilePath $log)
    $pythonExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $savedErrorActionPreference
}
if ($pythonExitCode -ne 0 -or $output.Count -lt 1) {
    throw "NISTP read-only scan failed; log=$log"
}

$report = $output[-1] | ConvertFrom-Json
if ($report.source -ne 'nistp.ru' -or $report.passes_match -ne $true -or
    $report.last_page_proven_twice -ne $true -or
    $report.production_database_writes -ne 0 -or
    $report.map_publication -ne $false -or
    $report.archival_allowed -ne $false -or
    $report.reconcile_missing -ne $false) {
    throw "NISTP report did not satisfy the read-only two-pass contract; log=$log"
}
$report | Add-Member -NotePropertyName scanner_revision -NotePropertyValue $revision

$report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $reportPath -Encoding utf8
Write-Host "NISTP two-pass proof saved: $reportPath"
Write-Host "Full log: $log"
