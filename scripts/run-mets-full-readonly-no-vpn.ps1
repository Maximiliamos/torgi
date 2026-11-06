param(
    [int]$MaxPages = 500,
    [double]$DelaySeconds = 1.0
)

$ErrorActionPreference = 'Stop'
$root = 'D:\BankrotAI\etp-evidence\mets-full-readonly'
New-Item -ItemType Directory -Path $root -Force | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$log = Join-Path $root "mets-two-pass-$stamp.log"
$reportPath = Join-Path $root "mets-two-pass-$stamp.json"
$previousConsoleEncoding = [Console]::OutputEncoding
$previousPythonUtf8 = $env:PYTHONUTF8
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = '1'

Write-Host 'M-ETS selected-catalogue two-pass read-only scan from the checked-out revision.'
Write-Host 'Scope: seven selected property categories; announced + application-intake states.'
Write-Host 'No PostgreSQL writes, reconciliation, archival, GEO or map publication.'
$revision = (& git.exe rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $revision) {
    throw 'Could not record the exact scanner revision'
}
Write-Host "M-ETS read-only scanner revision: $revision; max_pages=$MaxPages"

$savedErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $output = @(& python.exe -m `
        bankrotai.services.etp_mets_full_catalogue `
        --max-pages $MaxPages --delay-seconds $DelaySeconds 2>&1 |
        Tee-Object -FilePath $log | ForEach-Object {
            Write-Host $_
            $_
        })
    $pythonExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $savedErrorActionPreference
    [Console]::OutputEncoding = $previousConsoleEncoding
    if ($null -eq $previousPythonUtf8) {
        Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONUTF8 = $previousPythonUtf8
    }
}
if ($pythonExitCode -ne 0 -or $output.Count -lt 1) {
    throw "M-ETS read-only scan failed; log=$log"
}

$report = $output[-1] | ConvertFrom-Json
if ($report.source -ne 'm-ets.ru' -or
    $report.passes_match -ne $true -or
    $report.last_page_proven_twice -ne $true -or
    $report.filters_proven_twice -ne $true -or
    $report.catalogue_prices_and_statuses_proven_twice -ne $true -or
    $report.complete_selected_filter_inventory_twice -ne $true -or
    $report.complete_source_inventory -ne $false -or
    $report.category_scope_complete -ne $false -or
    $report.detail_validation_proven -ne $false -or
    $report.production_database_writes -ne 0 -or
    $report.archival_allowed -ne $false -or
    $report.reconcile_missing -ne $false -or
    $report.geocoding -ne $false -or
    $report.map_publication -ne $false) {
    throw "M-ETS report did not satisfy the read-only two-pass contract; log=$log"
}
$report | Add-Member -NotePropertyName scanner_revision -NotePropertyValue $revision

$report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $reportPath -Encoding utf8
Write-Host "M-ETS two-pass proof saved: $reportPath"
Write-Host "Full log: $log"
