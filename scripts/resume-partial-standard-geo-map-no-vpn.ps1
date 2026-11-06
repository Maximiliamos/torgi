# Resume GEO + MapDataset after the specifically verified partial seven-source run.
# DO NOT re-run ingestion or bypass Torgi Russia's fail-closed region guard.
[CmdletBinding()]
param(
    [string]$ExpectedSha = 'ce362342c8bb954cd2e55bb1efcbe8efe49c738a',
    [string]$RunId = '26ca1925-068f-4d86-ad7b-53d6482f5c21',
    [ValidateRange(1, 500)][int]$GeoBatchSize = 250,
    [ValidateRange(1, 64)][int]$GeoMaxBatches = 16,
    [switch]$AcceptSixSourcePartial
)
$ErrorActionPreference = 'Stop'
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
from bankrotai.db import LotSyncRun, LotSyncSourceRun, SessionLocal, utc_now
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

    Write-Host '=== GEO: bounded pending-lot geocoding; no source ingestion ===' -ForegroundColor Cyan
    $geoScript = @'
import json,os,time
from bankrotai.db import SessionLocal
from bankrotai.services.geo_backfill import geocode_pending_lots
limit=int(os.environ["TORGI_GEO_LIMIT"])
batches=int(os.environ["TORGI_GEO_BATCHES"])
summary={"processed":0,"geocoded":0,"failed":0,"batches":0,"backlog_remaining_possible":False}
for i in range(batches):
    r=geocode_pending_lots(SessionLocal,limit=limit,refresh_strategy=False)
    row={"number":i+1,"status":r.get("status"),"queued":r.get("queued"),
         "processed":r.get("processed"),"geocoded":r.get("geocoded"),"failed":r.get("failed")}
    print("GEO_BATCH="+json.dumps(row,ensure_ascii=False),flush=True)
    if r.get("status") in {"paused","busy","failed","error"}:
        raise RuntimeError("Geocoding aborted by guarded backfill: "+str(r.get("status")))
    summary["batches"] += 1
    for key in ("processed","geocoded","failed"):
        summary[key] += int(r.get(key) or 0)
    if int(r.get("queued") or 0) < limit or int(r.get("processed") or 0) == 0:
        break
    if i == batches-1:
        summary["backlog_remaining_possible"] = True
    time.sleep(2)
print("GEO_SUMMARY="+json.dumps(summary,ensure_ascii=False),flush=True)
'@
    $geoOut = @($geoScript | & docker.exe exec -i --env "TORGI_GEO_LIMIT=$GeoBatchSize" --env "TORGI_GEO_BATCHES=$GeoMaxBatches" $geo python -)
    $geoOut | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0 -or !$geoOut.Count -or $geoOut[-1] -notmatch '^GEO_SUMMARY=') {
        throw 'GEO continuation did not finish. Map publication was not started.'
    }

    Write-Host '=== Publish immutable map and verify public REG.RU S3 manifest ===' -ForegroundColor Cyan
    $mapScript = @'
import json
from sqlalchemy import select,func
from bankrotai.core import get_settings
from bankrotai.db import MapDataset,ProcessedLot,SessionLocal
from bankrotai.services.map_object_store import _verify_public_manifest
from bankrotai.tasks import build_map_dataset_task
result=build_map_dataset_task.run()
print("MAP_BUILD="+json.dumps(result,ensure_ascii=False,default=str),flush=True)
status=result.get("status")
if status not in {"success","published","skipped"} or (status=="skipped" and result.get("reason")!="map-source-unchanged"):
    raise RuntimeError("MapDataset was not published or already proven current")
with SessionLocal() as session:
    dataset=session.scalar(select(MapDataset).where(
        MapDataset.is_current.is_(True),
        MapDataset.status=="ready",
    ))
    if dataset is None: raise RuntimeError("No current ready MapDataset")
    count=session.scalar(select(func.count()).select_from(ProcessedLot).where(
        ProcessedLot.is_archived.is_(False),
        ProcessedLot.duplicate_of_id.is_(None),
        ProcessedLot.current_geo_lat.is_not(None),
        ProcessedLot.current_geo_lon.is_not(None),
    ))
    manifest=_verify_public_manifest(get_settings(),dataset.version)
    if not isinstance(manifest,dict):
        raise RuntimeError("Public REG.RU S3 manifest was not independently verified")
    print("MAP_CURRENT="+json.dumps({
        "version":dataset.version,"published_at":dataset.published_at,
        "point_count":dataset.point_count,
        "active_canonical_geocoded_lots":count,
        "public_manifest_verified":True,
        "torgi_russia_incomplete":True
    },ensure_ascii=False,default=str),flush=True)
'@
    $mapOut = @($mapScript | & docker.exe exec -i $map python -)
    $mapOut | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0 -or -not @($mapOut | Where-Object { $_ -match '^MAP_CURRENT=' }).Count) {
        throw 'Map build/verification failed'
    }
    Write-Host '=== GEO/MAP COMPLETE for 6 verified sources; Torgi Russia remains blocked ===' -ForegroundColor Green
    Write-Host "Run ID: $RunId"
    Write-Host 'Public map: https://sterdez.online/map'
} finally {
    Write-Host "Operator transcript: $logFile"
    Stop-Transcript | Out-Null
}