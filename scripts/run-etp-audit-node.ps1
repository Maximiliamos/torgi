param(
    [ValidateSet('pilot','problem','all')][string]$Phase = 'pilot',
    [string]$OutputRoot = 'C:\ETP_AUDIT\results',
    [switch]$Bootstrap
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$logRoot = 'C:\ETP_AUDIT\logs'
New-Item -ItemType Directory -Force -Path $OutputRoot, $logRoot | Out-Null
$log = Join-Path $logRoot ("etp-audit-{0}-{1}.log" -f $Phase, (Get-Date -Format 'yyyyMMdd-HHmmss'))

Start-Transcript -Path $log
try {
    $python = (Get-Command python -ErrorAction Stop).Source
    if ($Bootstrap) {
        Write-Host '[0%] Installing the read-only browser collector dependencies...'
        & $python -m pip install --user playwright
        if ($LASTEXITCODE -ne 0) { throw 'Playwright package installation failed.' }
        & $python -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw 'Chromium installation failed.' }
    }
    & $python -c 'import playwright'
    if ($LASTEXITCODE -ne 0) {
        throw 'Playwright is missing. Re-run this script once with -Bootstrap.'
    }

    $sites = switch ($Phase) {
        'pilot' { @('m-ets.ru','cdtrf.ru','rts-tender.ru') }
        'problem' { @(
            'alfalot.ru','rts-tender.ru','roseltorg.ru','sberbank-ast.ru',
            'fabrikant.ru','aukcioncenter.ru','ausib.ru','utender.ru','electro-torgi.ru'
        ) }
        'all' { @() }
    }
    Write-Host "[1%] Starting $Phase read-only ETP capture. No DB/GEO/map access."
    $arguments = @(
        (Join-Path $PSScriptRoot 'etp_capture_v6.py'), '--out', $OutputRoot,
        '--max-details', '3', '--challenge-timeout', '90'
    )
    if ($sites.Count) { $arguments += '--sites'; $arguments += $sites }
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "Collector failed with exit code $LASTEXITCODE" }
    Write-Host '[100%] Capture complete.'
    Write-Host "Results: $OutputRoot"
    Write-Host "Log: $log"
}
finally {
    Stop-Transcript
}
