[CmdletBinding()]
param(
    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'
$mapWorker = 'bankrotai-home-map-worker'
$geoWorker = 'bankrotai-home-geocoding-worker'

foreach ($container in @($mapWorker, $geoWorker)) {
    $status = docker inspect --format '{{.State.Status}}' $container 2>$null
    if ($LASTEXITCODE -ne 0 -or $status -ne 'running') {
        throw "$container is not running"
    }
}

$json = docker exec $mapWorker python -c @"
import json
from bankrotai.db import SessionLocal
from bankrotai.services.geo_backfill import geocoding_diagnostic_report
from bankrotai.services.quality import map_delivery_reconciliation_report, operational_quality_report
s = SessionLocal()
try:
    result = {
        'map_delivery': map_delivery_reconciliation_report(s, verify_public_manifest=True),
        'geocoding': geocoding_diagnostic_report(s),
        'operational': operational_quality_report(s),
    }
    print(json.dumps(result, ensure_ascii=False, default=str))
finally:
    s.close()
"@
if ($LASTEXITCODE -ne 0 -or -not $json) {
    throw 'P1 production data-quality audit could not query the production database'
}

$report = ($json | Select-Object -Last 1) | ConvertFrom-Json
$quality = $report.geocoding.quality
$backlog = $report.geocoding.backlog
$lotQuality = $report.operational.lot_data_quality
$sourceDateQuality = $report.operational.source_date_quality
$warningCount = 0
if ([int]$quality.invalid_coordinate_count -gt 0) { $warningCount++ }
if ([int]$quality.locality_mismatch_count -gt 0) { $warningCount++ }
if ([int]$backlog.retry_state.terminal -gt 0) { $warningCount++ }
foreach ($name in @(
    'missing_title',
    'missing_region',
    'missing_url',
    'missing_price',
    'non_positive_price',
    'unknown_status'
)) {
    if ([int]$lotQuality.$name -gt 0) { $warningCount++ }
}
foreach ($name in @(
    'first_seen_after_last_seen',
    'application_start_after_deadline',
    'archived_before_first_seen'
)) {
    if ([int]$sourceDateQuality.$name -gt 0) { $warningCount++ }
}

$result = [ordered]@{
    checked_at = (Get-Date).ToUniversalTime().ToString('o')
    healthy = [bool]$report.map_delivery.ok
    warning_count = $warningCount
    map_delivery = $report.map_delivery
    geocoding = $report.geocoding
    operational = $report.operational
}

if ($OutputPath) {
    $directory = Split-Path -Parent $OutputPath
    if ($directory) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
    $result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $OutputPath -Encoding utf8
}
$result | ConvertTo-Json -Depth 20
if (-not $result.healthy) { exit 1 }
