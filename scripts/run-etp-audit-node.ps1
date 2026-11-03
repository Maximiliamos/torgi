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
    Write-Host ("Python: {0}" -f (& $python --version 2>&1))
    $browserCandidates = @(
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:LOCALAPPDATA 'Yandex\YandexBrowser\Application\browser.exe')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    $browserExecutable = $browserCandidates | Select-Object -First 1
    if (-not $browserExecutable) {
        throw 'Chrome, Edge or Yandex Browser executable was not found.'
    }
    Write-Host "Browser: $browserExecutable"
    if ($Bootstrap) {
        Write-Host '[0%] Installing the read-only collector from the offline wheelhouse...'
        $wheelhouse = Join-Path $root 'wheelhouse'
        if (-not (Test-Path -LiteralPath $wheelhouse)) {
            throw "Offline wheelhouse is missing: $wheelhouse"
        }
        & $python -m pip install --user --no-index --find-links $wheelhouse playwright
        if ($LASTEXITCODE -ne 0) { throw 'Playwright package installation failed.' }
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
    $arguments += '--browser-executable'; $arguments += $browserExecutable
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "Collector failed with exit code $LASTEXITCODE" }
    Write-Host '[100%] Capture complete.'
    Write-Host "Results: $OutputRoot"
    Write-Host "Log: $log"
}
finally {
    Stop-Transcript
}
