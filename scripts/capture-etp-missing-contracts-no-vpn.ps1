param([string]$OutputRoot = 'D:\BankrotAI\etp-evidence')

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$logRoot = 'D:\BankrotAI\etp-operator-logs'
New-Item -ItemType Directory -Force -Path $logRoot, $OutputRoot | Out-Null
$log = Join-Path $logRoot ("etp-missing-contracts-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))

Start-Transcript -Path $log
try {
    Set-Location $repo
    $sources = @(
        'alfalot.ru', 'rts-tender.ru', 'roseltorg.ru', 'sberbank-ast.ru',
        'fabrikant.ru', 'aukcioncenter.ru', 'ausib.ru', 'utender.ru',
        'electro-torgi.ru'
    )
    $hosts = @(
        'etp.alfalot.ru', 'it2.rts-tender.ru', 'www.roseltorg.ru',
        'www.sberbank-ast.ru', 'www.fabrikant.ru', 'aukcioncenter.ru',
        'ausib.ru', 'utender.ru', 'bankrotstvo.electro-torgi.ru'
    )
    Write-Host '[0%] Проверка DNS без Happ Fake-IP...'
    foreach ($hostName in $hosts) {
        $addresses = @(Resolve-DnsName -Name $hostName -Type A -ErrorAction Stop |
            Where-Object IPAddress | Select-Object -ExpandProperty IPAddress)
        if (-not $addresses) { throw "DNS returned no A records for $hostName" }
        if ($addresses | Where-Object { $_ -match '^198\.(1[89]|2[0-9]|3[01])\.' }) {
            throw "Happ Fake-IP detected for $hostName. Disable VPN/proxy and retry."
        }
    }

    $python = (Get-Command python -ErrorAction Stop).Source
    $env:PYTHONPATH = Join-Path $repo 'src'
    & $python -c 'import playwright'
    if ($LASTEXITCODE -ne 0) { throw 'Playwright is not installed for this Python.' }

    Write-Host '[1%] Read-only capture: page 1, page 2, last page and up to 3 details.'
    Write-Host 'The Python collector prints a percentage after every source.'
    & $python scripts\etp_capture_v6.py --sites $sources --out $OutputRoot `
        --max-details 3 --challenge-timeout 90
    if ($LASTEXITCODE -ne 0) { throw "Capture failed with exit code $LASTEXITCODE" }
    Write-Host '[100%] Evidence capture complete. No database writes were performed.'
    Write-Host "Full log: $log"
}
finally {
    Stop-Transcript
}
