# Resume GEO + MapDataset after the specifically verified partial seven-source run.
# DO NOT re-run ingestion or bypass Torgi Russia's fail-closed region guard.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9-]{36}
    [ValidateRange(1, 500)][int]$GeoBatchSize = 250,
    [ValidateRange(1, 64)][int]$GeoMaxBatches = 16,
    [switch]$AcceptSixSourcePartial,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
# This legacy continuation intentionally accepts only six successful sources.
# It cannot prove the seventh source's region filter and must not publish
# a MapDataset. Its diagnostic preflight remains available as a dry-run.
if (-not $DryRun) {
    throw 'Six-source partial GEO/Map publication is disabled fail-closed. Retry the failed source via a separately verified targeted source-run, then require seven-source coverage.'
}
if (-not $AcceptSixSourcePartial) {
    throw 'Specify -AcceptSixSourcePartial: Torgi Russia is excluded because its region filter was not proven.'
}
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ingest = 'bankrotai-home-ingestion-worker'
$geo = 'bankrotai-home-geocoding-worker'
$map = 'bankrotai-home-map-worker'
$expectedGood = @(
    'torgi.gov.ru', 'lot-online.ru', 'bidexpert.ru',
    'nistp.ru', 'cdtrf.ru', 'm-ets.ru'
)
$logRoot = 'D:\BankrotAI\etp-operator-logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$logFile = Join-Path $logRoot ('resume-partial-geo-map-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
Start-Transcript -Path $logFile -Force | Out-Null
try {
    Write-Host '=== SHA and container checks ===' -ForegroundColor Cyan
    $main = @(& gh.exe api repos/Maximiliamos/torgi/branches/main --jq '.commit.sha')
    if ($LASTEXITCODE -ne 0 -or !$main.Count -or $main[-1].Trim() -ne $ExpectedSha) {
        throw 'Main moved since the pinned source run; stop before publication.'
    }
    foreach ($name in @($ingest, $geo, $map)) {
        $image = @(& docker.exe inspect --format '{{.Config.Image}}' $name)
        if ($LASTEXITCODE -ne 0 -or !$image.Count -or $image[-1].Trim() -ne "bankrotai-home-api:$ExpectedSha") {
            throw "Incorrect worker image for $name"
        }
        $running = @(& docker.exe inspect --format '{{.State.Running}}' $name)
        if ($LASTEXITCODE -ne 0 -or !$running.Count -or $running[-1].Trim() -ne 'true') {
            throw "Worker is not running: $name"
        }
    }

    Write-Host '=== Confirm six successes and one explicit Torgi Russia region-guard failure ===' -ForegroundColor Cyan
    $preflight = @'
import json, os
from sqlalchemy import select
from bankrotai.db import LotSyncRun, LotSyncSourceRun, MapDataset, SessionLocal, utc_now
from bankrotai.services.geo_backfill import is_geocoding_paused
wanted = {"torgi.gov.ru", "lot-online.ru", "bidexpert.ru", "nistp.ru", "cdtrf.ru", "m-ets.ru"}
with SessionLocal() as s:
    run = s.get(LotSyncRun, os.environ["TORGI_RUN_ID"])
    if run is None or run.status != "partial" or run.trigger_type != "manual_full" or run.finished_at is None:
        raise RuntimeError("Expected finished partial manual nationwide run not present")
    source_runs = s.scalars(select(LotSyncSourceRun).where(LotSyncSourceRun.sync_run_id == run.id)).all()
    by_name = {r.source_system:r for r in source_runs}
    if set(by_name) != wanted | {"torgi-russia.ru"}:
        raise RuntimeError("Unexpected number or identity of source runs")
    if any(
        by_name[name].status != "success" or by_name[name].items_failed != 0
        for name in wanted
    ):
        raise RuntimeError("One of six required sources did not pass")
    if any(by_name[name].items_archived for name in ("nistp.ru","cdtrf.ru","m-ets.ru")):
        raise RuntimeError("Unexpected archival on new ETP")
    bad = by_name["torgi-russia.ru"]
    if bad.status != "failed" or not str(bad.error_message or "").startswith(
        "Torgi Russia public region filter was not applied:"
    ) or bad.items_inserted or bad.items_updated or bad.items_archived:
        raise RuntimeError("Torgi Russia failure differs from the known no-write region-filter guard")
    active = s.scalars(select(LotSyncRun).where(
        LotSyncRun.status.in_(("queued","running")),
        LotSyncRun.lease_expires_at > utc_now(),
    )).all()
    if active:
        raise RuntimeError("Nationwide run is currently active; cannot publish safely")
    later = s.scalars(select(LotSyncRun).where(
        LotSyncRun.created_at > run.created_at, LotSyncRun.id != run.id,
    )).all()
    if later:
        raise RuntimeError("Later nationwide sync rows exist; re-audit before resuming this specific run")
    newer_map = s.scalar(select(MapDataset).where(
        MapDataset.created_at > run.finished_at,
        MapDataset.status == "ready",
    ).limit(1))
    if newer_map is not None:
        raise RuntimeError("A ready MapDataset newer than this run already exists; do not repeat partial continuation")
    if is_geocoding_paused(s):
        raise RuntimeError("GEO is paused in production")
    print("PREFLIGHT_PASS="+json.dumps({
        "run_id":run.id, "status":run.status,
        "six_successes":sorted(wanted),
        "missing_lot_archival_on_new_etps":0,
        "failed_source":"torgi-russia.ru",
        "failed_source_write_counts": [bad.items_inserted,bad.items_updated,bad.items_archived],
        "geo_paused":False,
        "active_nationwide_runs":0,
    },ensure_ascii=False),flush=True)
'@
    $rows = @($preflight | & docker.exe exec -i --env "TORGI_RUN_ID=$RunId" $ingest python -)
    if ($LASTEXITCODE -ne 0 -or !$rows.Count -or $rows[-1] -notmatch '^PREFLIGHT_PASS=') {
        $rows | ForEach-Object { Write-Host $_ }
        throw 'Partial-run GEO/map preflight failed; no GEO or map publication started'
    }
    $rows | ForEach-Object { Write-Host $_ }
    Write-Host 'DRY_RUN_ONLY: six-source status diagnosed; seventh source missing; no GEO or MapDataset publication is allowed.'
    return

} finally {
    Write-Host "Operator transcript: $logFile"
    Stop-Transcript | Out-Null
}
