param([string]$Destination = 'D:\BankrotAI\ETP_AUDIT_NODE.zip')

$ErrorActionPreference = 'Stop'
$stage = Join-Path ([IO.Path]::GetTempPath()) ('ETP_AUDIT_NODE_' + [guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Force -Path (Join-Path $stage 'scripts') | Out-Null
    foreach ($name in @(
        'etp_capture_v6.py', 'etp_capture_profiles.json', 'run-etp-audit-node.ps1'
    )) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) `
            -Destination (Join-Path (Join-Path $stage 'scripts') $name)
    }
    $wheelhouseSource = 'D:\BankrotAI\etp-audit-wheelhouse'
    if (-not (Test-Path -LiteralPath $wheelhouseSource)) {
        throw "Offline wheelhouse is missing: $wheelhouseSource"
    }
    Copy-Item -LiteralPath $wheelhouseSource -Destination (Join-Path $stage 'wheelhouse') -Recurse
    $readme = @'
STERDEZ ETP AUDIT NODE

Read-only collector only. It contains no production DB, server keys or secrets.

First run (installs Playwright from the bundled offline wheelhouse and uses installed Chrome/Edge/Yandex):
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-etp-audit-node.ps1 -Phase pilot -Bootstrap

Later phases:
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-etp-audit-node.ps1 -Phase problem
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-etp-audit-node.ps1 -Phase all

Return C:\ETP_AUDIT\results\TORGI_ETP_V6_*.zip and the matching log.
'@
    Set-Content -LiteralPath (Join-Path $stage 'README.txt') -Value $readme -Encoding UTF8
    Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $Destination -Force
    Write-Host "Package: $Destination"
}
finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
}
