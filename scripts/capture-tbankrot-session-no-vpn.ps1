param([string]$Destination = 'C:\ProgramData\BankrotAI\tbankrot-cookies.json')
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$node = (Get-Command node -ErrorAction Stop).Source
if (-not (Test-Path (Join-Path $repo 'WEB\node_modules\playwright'))) {
    Push-Location (Join-Path $repo 'WEB')
    try { npm ci } finally { Pop-Location }
}
New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
& $node (Join-Path $PSScriptRoot 'capture-tbankrot-session.mjs') $Destination
if ($LASTEXITCODE -ne 0) { throw 'Could not save the TBankrot session' }
& icacls.exe $Destination /inheritance:r /grant:r 'SYSTEM:F' '*S-1-5-32-544:F' | Out-Null
Write-Host 'Done. Cookie values were not printed. Enable VPN and reply: ready.'
