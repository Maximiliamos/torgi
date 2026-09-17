[CmdletBinding()]
param([string]$LogDirectory = 'C:\BankrotAI\logs\health')

$ErrorActionPreference = 'Stop'
$required = @(
    'bankrotai-home-postgres', 'bankrotai-home-redis', 'bankrotai-photon',
    'bankrotai-home-secondary', 'bankrotai-home-ingestion-worker'
)
$result = [ordered]@{ checked_at = (Get-Date).ToUniversalTime().ToString('o'); checks = @(); healthy = $true }

foreach ($name in $required) {
    $status = docker inspect --format '{{.State.Status}}' $name 2>$null
    $health = docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}not-configured{{end}}' $name 2>$null
    $ok = $LASTEXITCODE -eq 0 -and $status -eq 'running' -and $health -notin @('unhealthy', 'starting')
    $result.checks += [ordered]@{ name = $name; status = $status; health = $health; ok = $ok }
    if (!$ok) { $result.healthy = $false }
}

$disk = Get-PSDrive -Name C
$diskPercentFree = [math]::Round(($disk.Free / ($disk.Used + $disk.Free)) * 100, 1)
$diskOk = $diskPercentFree -ge 10
$result.checks += [ordered]@{ name = 'disk-c'; percent_free = $diskPercentFree; ok = $diskOk }
if (!$diskOk) { $result.healthy = $false }

$pgUser = docker exec bankrotai-home-postgres printenv POSTGRES_USER
$pgDatabase = docker exec bankrotai-home-postgres printenv POSTGRES_DB
$db = docker exec bankrotai-home-postgres psql -U $pgUser.Trim() -d $pgDatabase.Trim() -Atc `
    'SELECT count(*) FILTER (WHERE is_current),count(*) FROM map_datasets'
$dbExitCode = $LASTEXITCODE
$dbParts = if ($null -eq $db) { @() } else { ([string]$db).Trim().Split('|') }
$dbOk = $dbExitCode -eq 0 -and $dbParts.Count -eq 2 -and $dbParts[0] -eq '1'
$result.checks += [ordered]@{ name = 'map-current-invariant'; current = $dbParts[0]; datasets = $dbParts[1]; ok = $dbOk }
if (!$dbOk) { $result.healthy = $false }

foreach ($url in @('http://127.0.0.1:18000/health/live', 'http://127.0.0.1:18000/health/ready')) {
    try { $statusCode = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 -Uri $url).StatusCode } catch { $statusCode = 0 }
    $ok = $statusCode -eq 200
    $result.checks += [ordered]@{ name = $url; status = $statusCode; ok = $ok }
    if (!$ok) { $result.healthy = $false }
}

New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$path = Join-Path $LogDirectory ("health-{0}.json" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $path -Encoding utf8
$result | ConvertTo-Json -Depth 5
if (!$result.healthy) { exit 1 }
