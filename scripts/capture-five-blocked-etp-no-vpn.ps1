param(
    [string]$OutputRoot = 'D:\BankrotAI\etp-evidence'
)

$ErrorActionPreference = 'Stop'

function Test-PlaywrightImport([string]$PythonExe) {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        & $PythonExe -c 'import playwright' 2>$null
        return ($LASTEXITCODE -eq 0)
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

$repo = Split-Path -Parent $PSScriptRoot
$logRoot = 'D:\BankrotAI\etp-operator-logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $logRoot "five-blocked-etp-no-vpn-$stamp.log"

Start-Transcript -Path $log
try {
    Set-Location $repo
    $hosts = @(
        'it2.rts-tender.ru', 'etp-profit.ru', 'ausib.ru',
        'utender.ru', 'bankrotstvo.electro-torgi.ru'
    )
    Write-Host '[0%] Checking no-VPN DNS...'
    foreach ($hostName in $hosts) {
        $addresses = @(Resolve-DnsName -Name $hostName -Type A -ErrorAction Stop |
            Where-Object IPAddress | Select-Object -ExpandProperty IPAddress)
        if (-not $addresses) { throw "DNS returned no A records for $hostName" }
        if ($addresses | Where-Object { $_ -match '^198\.(1[89]|2[0-9]|3[01])\.' }) {
            throw "Happ Fake-IP detected for $hostName. Disable VPN/proxy and retry."
        }
    }

    $python = Join-Path $repo '.venv\Scripts\python.exe'
    $venvReady = (Test-Path -LiteralPath $python)
    if ($venvReady) {
        $venvReady = Test-PlaywrightImport $python
    }
    if (-not $venvReady) {
        $systemPython = Get-Command python -ErrorAction SilentlyContinue
        if (-not $systemPython) { throw 'System Python is not available.' }
        $python = $systemPython.Source
        if (-not (Test-PlaywrightImport $python)) { throw 'Playwright is not installed.' }
    }

    $env:PYTHONPATH = Join-Path $repo 'src'
    Write-Host '[1%] Capturing five remaining providers sequentially...'
    & $python scripts\etp_capture_v6.py `
        --sites rts-tender.ru etp-profit.ru ausib.ru utender.ru electro-torgi.ru `
        --out $OutputRoot --max-details 3 --challenge-timeout 90
    if ($LASTEXITCODE -ne 0) { throw "Capture failed with exit code $LASTEXITCODE" }
    Write-Host '[100%] Capture complete.'
    Write-Host "Evidence root: $OutputRoot"
    Write-Host "Full log: $log"
}
finally {
    Stop-Transcript
}
