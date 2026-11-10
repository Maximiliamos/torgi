# Windows PowerShell 5.1 compatible; this file intentionally contains ASCII only.
# Reads the home PostgreSQL through the existing GEO worker, never modifying it.
param(
    [string]$Container = 'bankrotai-home-geocoding-worker',
    [string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = $utf8
[Console]::OutputEncoding = $utf8

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI not found. Start Docker Desktop and check installation.'
}
$server = docker info --format '{{.ServerVersion}}' 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Docker Engine is unavailable: $server"
}
$running = docker ps --filter "name=^/$Container$" --format '{{.Names}}'
if ($LASTEXITCODE -ne 0 -or @($running) -notcontains $Container) {
    throw "GEO worker '$Container' is not running. Do not run the audit against an unrelated container."
}

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path (Get-Location).Path 'geo-p0-evidence'
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

function Invoke-BoundedReadOnlyPython {
    param(
        [Parameter(Mandatory=$true)][string]$FileName,
        [Parameter(Mandatory=$true)][string]$OutputName
    )
    $path = Join-Path $PSScriptRoot $FileName
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Missing $path. Keep the PowerShell and both Python files in one directory."
    }
    $result = Get-Content -LiteralPath $path -Encoding UTF8 -Raw |
        docker exec -e PYTHONIOENCODING=utf-8 -i $Container python - 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "$FileName failed in the GEO worker: $($result | Select-Object -Last 5 | Out-String)"
    }
    $line = @($result | Where-Object { $_ -and "$_".Trim() }) | Select-Object -Last 1
    try {
        $parsed = $line | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "$FileName did not return one valid JSON report."
    }
    if (-not $parsed.report) { throw "Invalid report from $FileName" }
    $dest = Join-Path $OutputDirectory $OutputName
    $parsed | ConvertTo-Json -Depth 30 | Out-File -LiteralPath $dest -Encoding utf8
    Write-Host "OK: $($parsed.report): $dest"
    return $parsed
}

$diskInfo = @(Get-PSDrive -Name C,D -ErrorAction SilentlyContinue |
    Select-Object Name,
        @{Name='FreeGiB';Expression={[math]::Round($_.Free/1GB,2)}},
        @{Name='UsedGiB';Expression={[math]::Round($_.Used/1GB,2)}})
$diskInfo | ConvertTo-Json -Depth 5 |
    Out-File -LiteralPath (Join-Path $OutputDirectory 'geo-p0-host-disks.json') -Encoding utf8
Write-Host "Docker Engine version: $server"
Write-Host 'Starting a read-only full-population census (no provider queries)...'
$census = Invoke-BoundedReadOnlyPython 'geo_p0_readonly.py' 'geo-p0-census.json'
Write-Host "Active/scheduled unmapped: $($census.active_or_scheduled_unmapped)"
Write-Host "All non-archived, nonduplicate unmapped: $($census.nonarchived_unduplicated_unmapped)"
Write-Host 'Checking the configured live providers (one Photon and one NSPD GET)...'
$probe = Invoke-BoundedReadOnlyPython 'geo_p0_provider_probe.py' 'geo-p0-providers.json'
Write-Host "Photon: $($probe.checks.photon.state)"
Write-Host "NSPD: $($probe.checks.nspd.state)"
Write-Host "Evidence saved in: $OutputDirectory"
Write-Host 'No database rows, GEO queues, backup files or production map were changed.'
