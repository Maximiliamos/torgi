param(
    [int]$StartPage = 172,
    [int]$MaxPages = 10000,
    [double]$DelaySeconds = 0.2
)

$ErrorActionPreference = 'Stop'
$root = 'D:\BankrotAI\etp-evidence\nistp-full-readonly'
New-Item -ItemType Directory -Path $root -Force | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$log = Join-Path $root "nistp-tail-preflight-$stamp.log"
$reportPath = Join-Path $root "nistp-tail-preflight-$stamp.json"
$revision = (& git rev-parse HEAD).Trim()
$previousConsoleEncoding = [Console]::OutputEncoding
$previousPythonUtf8 = $env:PYTHONUTF8
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = '1'

Write-Host "NISTP tail preflight from page $StartPage (one pass only)."
Write-Host "NISTP tail preflight revision: $revision"
Write-Host 'Collects every unknown status before the next full two-pass proof.'
Write-Host 'No PostgreSQL writes, reconciliation, archival, GEO or map publication.'

$previousPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    $output = @(& python.exe -m `
        bankrotai.services.etp_nistp_full_catalogue `
        --single-pass --collect-status-errors `
        --start-page $StartPage --max-pages $MaxPages `
        --delay-seconds $DelaySeconds 2>&1 | Tee-Object -FilePath $log)
    $pythonExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousPreference
    [Console]::OutputEncoding = $previousConsoleEncoding
    if ($null -eq $previousPythonUtf8) {
        Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONUTF8 = $previousPythonUtf8
    }
}
if ($pythonExitCode -ne 0 -or $output.Count -lt 1) {
    throw "NISTP tail preflight failed structurally; log=$log"
}

$report = $output[-1] | ConvertFrom-Json
$report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $reportPath -Encoding utf8
Write-Host "NISTP tail preflight saved: $reportPath"
Write-Host "Unknown source statuses: $($report.unrecognized_source_statuses.Count)"
Write-Host "Full log: $log"
