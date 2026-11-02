param([string]$OutputRoot = 'D:\BankrotAI\etp-evidence')

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$logRoot = 'D:\BankrotAI\etp-operator-logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$log = Join-Path $logRoot ("profit-ausib-no-vpn-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))

Start-Transcript -Path $log
try {
    Set-Location $repo
    $env:PYTHONPATH = Join-Path $repo 'src'
    $python = (Get-Command python -ErrorAction Stop).Source
    Write-Host '[0%] Capturing Profit and AUSIB catalogue, page 2 and three details...'
    & $python scripts\etp_capture_v6.py `
        --sites etp-profit.ru ausib.ru `
        --out $OutputRoot --max-details 3 --challenge-timeout 30
    if ($LASTEXITCODE -ne 0) { throw "Capture failed with exit code $LASTEXITCODE" }
    Write-Host '[100%] Profit/AUSIB evidence capture complete.'
    Write-Host "Full log: $log"
}
finally {
    Stop-Transcript
}
