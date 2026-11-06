# Exact-SHA, no-VPN operator campaign: existing core sources + M-ETS, CDTRF, NISTP.
# NOT the 18+ source ETP campaign. Respects source pause/circuit and run lease.
# This performs real PostgreSQL writes, GEO work and immutable S3 map publication.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[0-9a-f]{40}$')][string]$ExpectedSha,
    [string]$BackupScript = (Join-Path $PSScriptRoot 'backup-home-postgres.ps1'),
    [ValidateRange(1, 500)][int]$GeoBatchSize = 500,
    [ValidateRange(1, 64)][int]$GeoMaxBatches = 64,
    [switch]$ConfirmProductionWrites
)
$ErrorActionPreference = 'Stop'
if (-not $ConfirmProductionWrites) { throw 'Specify -ConfirmProductionWrites to authorize the full PostgreSQL/GEO/map campaign.' }
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ingest = 'bankrotai-home-ingestion-worker'
$geo = 'bankrotai-home-geocoding-worker'
$map = 'bankrotai-home-map-worker'
$expectedSources = @(
    'torgi.gov.ru', 'lot-online.ru', 'torgi-russia.ru', 'bidexpert.ru',
    'm-ets.ru', 'cdtrf.ru', 'nistp.ru'
)
$logRoot = 'D:\BankrotAI\etp-operator-logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$logFile = Join-Path $logRoot ('manual-standard-geo-map-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
Start-Transcript -Path $logFile -Force | Out-Null
try {
    Write-Host '=== PRE-FLIGHT: exact SHA, no VPN, source state and lease ===' -ForegroundColor Cyan
    if (-not (Get-Command gh.exe -ErrorAction SilentlyContinue)) { throw 'gh.exe is required' }
    $mainSha = (& gh.exe api repos/Maximiliamos/torgi/branches/main --jq '.commit.sha' | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -ne 0 -or $mainSha -ne $ExpectedSha) {
        throw "Main SHA changed. Expected=$ExpectedSha; current=$mainSha"
    }
    foreach ($container in @($ingest, $geo, $map)) {
        $image = (& docker.exe inspect --format '{{.Config.Image}}' $container | Select-Object -Last 1).Trim()
        if ($LASTEXITCODE -ne 0 -or $image -ne "bankrotai-home-api:$ExpectedSha") {
            throw "Wrong image for $container : $image"
        }
        $running = (& docker.exe inspect --format '{{.State.Running}}' $container | Select-Object -Last 1).Trim()
        if ($LASTEXITCODE -ne 0 -or $running -ne 'true') { throw "Container not running: $container" }
    }
    foreach ($domain in @('m-ets.ru','torgi.cdtrf.ru','nistp.ru','nspd.gov.ru')) {
        $ips = @(Resolve-DnsName -Name $domain -Type A -ErrorAction Stop | Where-Object IPAddress | ForEach-Object IPAddress)
        if (-not $ips.Count -or @($ips | Where-Object { $_ -match '^198\.(18|19)\.' }).Count) {
            throw "DNS or VPN Fake-IP problem for $domain : $($ips -join ', ')"
        }
    }

    $preflight = @'
import json
from sqlalchemy import select
from bankrotai.db import LotSyncRun, SessionLocal, utc_now
from bankrotai.services.ingestion import default_source_specs
from bankrotai.tasks import _source_is_paused, _source_is_circuit_blocked, _global_source_network_blocked
want = {"torgi.gov.ru", "lot-online.ru", "torgi-russia.ru", "bidexpert.ru", "m-ets.ru", "cdtrf.ru", "nistp.ru"}
specs = {x.source_id: x for x in default_source_specs()}
excluded = {name: {"paused": _source_is_paused(name), "circuit": _source_is_circuit_blocked(name)} for name in sorted(want)}
missing = sorted(want - set(specs))
unexpected = sorted(set(specs) - want - {"tbankrot.ru"})
tbankrot_paused = _source_is_paused("tbankrot.ru")
blocked = {k: v for k,v in excluded.items() if v["paused"] or v["circuit"]}
active = []
with SessionLocal() as s:
    active = [r.id for r in s.scalars(select(LotSyncRun).where(LotSyncRun.status.in_(("queued","running")), LotSyncRun.lease_expires_at > utc_now())).all()]
invalid = [x.source_id for x in specs.values() if x.source_id in {"m-ets.ru","cdtrf.ru","nistp.ru"} and (x.max_batches != 500 or x.reconcile_missing is not False or x.require_source_end is not True)]
filter_types = {name:type(specs[name].filters).__name__ for name in ("m-ets.ru","cdtrf.ru","nistp.ru") if name in specs}
print(json.dumps({"all_configured": sorted(specs), "required": sorted(want), "missing":missing, "unexpected":unexpected, "tbankrot_paused":tbankrot_paused, "blocked":blocked, "global_block": _global_source_network_blocked(), "active_run_ids":active, "new_source_limit_invalid":invalid, "new_source_filter_types":filter_types, "nistp_detail_cap":getattr(specs.get("nistp.ru"), "max_detail_attempts_per_page",None)}, ensure_ascii=False))
'@
    $raw = @($preflight | & docker.exe exec -i $ingest python -)
    if ($LASTEXITCODE -ne 0 -or -not $raw.Count) { throw 'Cannot inspect live source configuration' }
    $state = $raw[-1] | ConvertFrom-Json
    $state | ConvertTo-Json -Depth 6
    if (@($state.missing).Count -or @($state.unexpected).Count -or -not $state.tbankrot_paused -or
        @($state.blocked.PSObject.Properties).Count -or
        $state.global_block -or @($state.active_run_ids).Count -or
        @($state.new_source_limit_invalid).Count -or $null -ne $state.nistp_detail_cap -or
        $state.new_source_filter_types.'m-ets.ru' -ne 'MetsSyncFilters' -or
        $state.new_source_filter_types.'cdtrf.ru' -ne 'CdtrfSyncFilters' -or
        $state.new_source_filter_types.'nistp.ru' -ne 'EtpSearchFilters') {
        throw 'Preflight BLOCKED: not all seven expected operational sources are eligible. No import has started.'
    }

    if (-not (Test-Path -LiteralPath $BackupScript -PathType Leaf)) {
        throw "Backup script missing: $BackupScript. No import has started."
    }
    Write-Host '=== Verified restore test of fresh PostgreSQL backup ===' -ForegroundColor Cyan
    & $BackupScript -VerifyRestore -RetainCount 0
    if ($LASTEXITCODE -ne 0) { throw 'Backup/restore verification failed. No import has started.' }

    Write-Host '=== Queue normal nationwide full sync (seven eligible sources) ===' -ForegroundColor Cyan
    $start = @'
from bankrotai.tasks import schedule_nationwide_lot_sync
run_id = schedule_nationwide_lot_sync(triggered_by="operator-standard-geo-map", mode="full")
print("RUN_ID=" + run_id, flush=True)
'@
    $out = @($start | & docker.exe exec -i $ingest python -)
    if ($LASTEXITCODE -ne 0 -or -not $out.Count -or $out[-1] -notmatch '^RUN_ID=([a-f0-9-]{36})$') {
        throw 'Failed to queue durable nationwide run'
    }
    $runId = $Matches[1]
    Write-Host "RUN_ID=$runId"

    $probe = @'
import json, os
from bankrotai.db import LotSyncRun, LotSyncSourceRun, SessionLocal
with SessionLocal() as s:
    run = s.get(LotSyncRun,os.environ["TORGI_RUN_ID"])
    rows = s.query(LotSyncSourceRun).filter_by(sync_run_id=os.environ["TORGI_RUN_ID"]).all()
    print(json.dumps({"status":None if run is None else run.status,
       "error":None if run is None else run.error_message,
       "sources":[{"id":r.source_system,"status":r.status,"pages":r.pages_scanned,
          "seen":r.items_seen,"inserted":r.items_inserted,"updated":r.items_updated,
          "archived":r.items_archived,"failed":r.items_failed,"error":r.error_message}
          for r in rows]},ensure_ascii=False,default=str))
'@
    $deadline = (Get-Date).AddHours(6)
    do {
        Start-Sleep -Seconds 20
        $lines = @($probe | & docker.exe exec -i --env "TORGI_RUN_ID=$runId" $ingest python -)
        if ($LASTEXITCODE -ne 0 -or -not $lines.Count) { throw 'Unable to inspect durable sync progress' }
        $status = $lines[-1] | ConvertFrom-Json
        Write-Host ("Nationwide: {0}; source runs={1}/{2}" -f $status.status, @($status.sources).Count, $expectedSources.Count)
        if ($status.status -in @('success','failed','partial')) { break }
    } while ((Get-Date) -lt $deadline)
    if ($status.status -ne 'success') {
        $status | ConvertTo-Json -Depth 7
        throw "Nationwide run not fully successful: $($status.status); no GEO or map rebuild started. RUN_ID=$runId"
    }
    $observed = @($status.sources | ForEach-Object id)
    $missingRuns = @($expectedSources | Where-Object { $_ -notin $observed })
    $failedRuns = @($status.sources | Where-Object { $_.status -ne 'success' })
    if ($missingRuns.Count -or $failedRuns.Count -or $observed.Count -ne $expectedSources.Count) {
        $status | ConvertTo-Json -Depth 7
        throw "Incomplete source coverage; missing=$($missingRuns -join ','); stop before GEO/map"
    }
    foreach ($row in $status.sources) {
        Write-Host ("{0}: pages={1}, seen={2}, inserted={3}, updated={4}, archived={5}, failed={6}" -f $row.id,$row.pages,$row.seen,$row.inserted,$row.updated,$row.archived,$row.failed)
        if ($row.id -in @('nistp.ru','cdtrf.ru','m-ets.ru') -and $row.archived -ne 0) {
            throw "New-source archival unexpectedly nonzero for $($row.id). Stop before GEO/map."
        }
        if ($row.id -in @('nistp.ru','cdtrf.ru','m-ets.ru') -and $row.pages -le 5) {
            throw "Full selected catalogue was not traversed for $($row.id). Stop before GEO/map."
        }
    }

    Write-Host '=== GEO: bounded pending-lot geocoding in dedicated GEO worker ===' -ForegroundColor Cyan
    $geoScript = @'
import json,os,time
from bankrotai.db import SessionLocal
from bankrotai.services.geo_backfill import geocode_pending_lots
limit=int(os.environ["TORGI_GEO_LIMIT"]); batches=int(os.environ["TORGI_GEO_BATCHES"])
summary={"processed":0,"geocoded":0,"failed":0,"batches":0,"backlog_remaining_possible":False}
for i in range(batches):
    r=geocode_pending_lots(SessionLocal,limit=limit,refresh_strategy=False)
    print("GEO_BATCH=" + json.dumps({"i":i+1,"status":r.get("status"),"queued":r.get("queued"),"processed":r.get("processed"),"geocoded":r.get("geocoded"),"failed":r.get("failed")},ensure_ascii=False),flush=True)
    if r.get("status") in {"paused","busy","failed","error"}:
        raise RuntimeError(f"GEO could not run safely: {r.get('status')}")
    summary["batches"] += 1
    for key in ("processed","geocoded","failed"):
        summary[key] += int(r.get(key) or 0)
    if int(r.get("queued") or 0) < limit or int(r.get("processed") or 0) == 0:
        break
    if i == batches-1: summary["backlog_remaining_possible"] = True
    time.sleep(2)
print("GEO_SUMMARY="+json.dumps(summary,ensure_ascii=False),flush=True)
'@
    $geoOut = @($geoScript | & docker.exe exec -i --env "TORGI_GEO_LIMIT=$GeoBatchSize" --env "TORGI_GEO_BATCHES=$GeoMaxBatches" $geo python -)
    $geoOut | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0 -or -not $geoOut.Count -or $geoOut[-1] -notmatch '^GEO_SUMMARY=') {
        throw 'GEO failed or incomplete; map publication not started'
    }
    $geoReport = ($geoOut[-1] -replace '^GEO_SUMMARY=', '') | ConvertFrom-Json

    Write-Host '=== Build immutable MapDataset and verify public REG.RU S3 manifest ===' -ForegroundColor Cyan
    $mapScript = @'
import json
from sqlalchemy import select,func
from bankrotai.core import get_settings
from bankrotai.db import MapDataset,ProcessedLot,SessionLocal
from bankrotai.services.map_object_store import _verify_public_manifest
from bankrotai.tasks import build_map_dataset_task
out=build_map_dataset_task.run()
print("MAP_BUILD="+json.dumps(out,ensure_ascii=False,default=str),flush=True)
status=out.get("status")
if status not in {"success","published","skipped"} or (status=="skipped" and out.get("reason")!="map-source-unchanged"):
    raise RuntimeError("Map publication did not complete cleanly")
with SessionLocal() as s:
    d=s.scalar(select(MapDataset).where(MapDataset.is_current.is_(True),MapDataset.status=="ready"))
    if d is None: raise RuntimeError("No current ready MapDataset")
    active_coords=s.scalar(select(func.count()).select_from(ProcessedLot).where(ProcessedLot.is_archived.is_(False),ProcessedLot.duplicate_of_id.is_(None),ProcessedLot.current_geo_lat.is_not(None),ProcessedLot.current_geo_lon.is_not(None)))
    manifest=_verify_public_manifest(get_settings(),d.version)
    if not isinstance(manifest,dict): raise RuntimeError("Public S3 manifest not verified")
    print("MAP_CURRENT="+json.dumps({"version":d.version,"published_at":d.published_at,"active_canonical_geocoded_lots":active_coords,"public_manifest_verified":True},ensure_ascii=False,default=str),flush=True)
'@
    $mapOut = @($mapScript | & docker.exe exec -i $map python -)
    $mapOut | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0 -or -not @($mapOut | Where-Object { $_ -match '^MAP_CURRENT=' }).Count) {
        throw 'Map publishing or public S3 verification failed'
    }
    Write-Host '=== COMPLETED: all 7 eligible sources; GEO; published MapDataset ===' -ForegroundColor Green
    if ($geoReport.backlog_remaining_possible) {
        Write-Warning 'GEO was bounded and may have remaining pending lots; map includes only geocoded/eligible lots.'
    }
    Write-Host 'Public map: https://sterdez.online/map'
    Write-Host "Sync run ID: $runId"
} finally {
    Write-Host "Operator transcript: $logFile"
    Stop-Transcript | Out-Null
}
