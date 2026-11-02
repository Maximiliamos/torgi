param(
    [string]$OutputRoot = 'D:\BankrotAI\etp-evidence',
    [switch]$Headless
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$logRoot = 'D:\BankrotAI\etp-operator-logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $logRoot "seven-etp-no-vpn-$stamp.log"

Start-Transcript -Path $log
try {
    Set-Location $repo
    Write-Host '[0%] Checking DNS outside Happ Fake-IP range...'
    $hosts = @(
        'etp.alfalot.ru', 'www.roseltorg.ru', 'it2.rts-tender.ru',
        'etp-profit.ru', 'ausib.ru', 'utender.ru', 'bankrotstvo.electro-torgi.ru'
    )
    foreach ($hostName in $hosts) {
        $addresses = @(Resolve-DnsName -Name $hostName -Type A -ErrorAction Stop |
            Where-Object IPAddress | Select-Object -ExpandProperty IPAddress)
        if (-not $addresses) { throw "DNS returned no A records for $hostName" }
        if ($addresses | Where-Object { $_ -match '^198\.(1[89]|2[0-9]|3[01])\.' }) {
            throw "Happ Fake-IP detected for $hostName. Disable VPN/proxy and retry."
        }
        Write-Host "  DNS $hostName -> $($addresses -join ', ')"
    }

    $python = Join-Path $repo '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Python venv not found: $python"
    }
    $env:PYTHONPATH = Join-Path $repo 'src'
    $arguments = @(
        'scripts\etp_capture_v6.py',
        '--sites', 'alfalot.ru', 'roseltorg.ru', 'rts-tender.ru',
        'etp-profit.ru', 'ausib.ru', 'utender.ru', 'electro-torgi.ru',
        '--out', $OutputRoot, '--max-details', '3', '--challenge-timeout', '90'
    )
    if ($Headless) { $arguments += '--headless' }

    Write-Host '[1%] Starting sequential read-only browser capture. No DB writes.'
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "Capture failed with exit code $LASTEXITCODE" }
    Write-Host '[100%] Capture complete.'
    Write-Host "Evidence root: $OutputRoot"
    Write-Host "Full log: $log"
}
finally {
    Stop-Transcript
}
