# Operator diagnostics and gated recovery for four guarded standard sources.
# Default behavior: strictly READ-ONLY (DB SELECT + at most one listing page
# per source + at most two NISTP per-lot detail lookups).
# -ApplyVerifiedRecovery: writes ONLY four source health states after
# all four provider parser probes succeed and all cooldowns have elapsed.
[CmdletBinding()]
param(
    [string]$ExpectedSha = 'ce362342c8bb954cd2e55bb1efcbe8efe49c738a',
    [switch]$ApplyVerifiedRecovery
)
$ErrorActionPreference = 'Stop'
$container = 'bankrotai-home-ingestion-worker'
$image = @(& docker.exe inspect --format '{{.Config.Image}}' $container)
if ($LASTEXITCODE -ne 0 -or !$image.Count -or $image[-1].Trim() -cne "bankrotai-home-api:$ExpectedSha") {
    throw 'Home ingestion-worker SHA does not match; no network probe or recovery attempted.'
}
$running = @(& docker.exe inspect --format '{{.State.Running}}' $container)
if ($LASTEXITCODE -ne 0 -or !$running.Count -or $running[-1].Trim() -ne 'true') {
    throw 'Home ingestion-worker unavailable'
}
$python = @'
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime

from sqlalchemy import select

from bankrotai.connectors.registry import connector_registry
from bankrotai.db import SessionLocal, LotSyncRun, SourceHealthState, utc_now
from bankrotai.services.etp_nistp_schedule_guard import (
    catalogue_row_eligible_for_detail, independent_detail_proven,
)
from bankrotai.services.ingestion import default_source_specs
from bankrotai.services.source_resilience import (
    record_source_probe, source_resilience_status,
)
from bankrotai.tasks import (
    _global_source_network_blocked, _source_is_paused,
)

SOURCES = ("torgi.gov.ru", "lot-online.ru", "bidexpert.ru", "nistp.ru")


def serialize_health(session, name: str) -> dict:
    state = session.scalar(
        select(SourceHealthState).where(SourceHealthState.source_system == name)
    )
    health = source_resilience_status(session, name)
    keep = (
        "circuit_state", "circuit_open_until",
        "next_retry_at", "last_error_category",
        "operational_failure", "retryable",
        "consecutive_operational_failures",
        "last_operational_failure_at", "last_probe_at",
        "last_probe_success_at",
    )
    return {
        **{k: health.get(k) for k in keep},
        "db_health": state.status if state else "not_recorded",
        "last_failure_at": state.last_failure_at if state else None,
        "last_success_at": state.last_success_at if state else None,
        "last_error_length": len(state.last_error or "") if state else 0,
    }


async def probe_one(name: str, spec) -> dict:
    result = {"source": name, "listing_verified": False, "detail_verified": None}
    try:
        connector = connector_registry.create(name)
        page = await asyncio.wait_for(
            connector.search(spec.filters), timeout=45,
        )
        lots = page.items
        ids = [
            str(lot.external_id)
            for lot in lots if getattr(lot, "external_id", None)
        ]
        candidates = [
            lot for lot in lots
            if lot.source_system == name and lot.external_id and lot.title
        ]
        result.update(
            listing_count=len(lots),
            distinct_ids=len(set(ids)),
            matching_source_count=len(candidates),
        )
        result["listing_verified"] = bool(
            candidates and len(ids) == len(set(ids))
        )
        if name == "nistp.ru":
            valid_catalogue = [
                lot for lot in candidates
                if catalogue_row_eligible_for_detail(lot)
            ]
            result["eligible_catalogue_rows"] = len(valid_catalogue)
            detail_verified = False
            result["detail_attempts"] = 0
            for lot in valid_catalogue[:2]:
                result["detail_attempts"] += 1
                try:
                    enriched = await asyncio.wait_for(
                        connector.enrich_lot(lot), timeout=40,
                    )
                    if independent_detail_proven(enriched):
                        detail_verified = True
                        break
                except Exception as exc:
                    result["detail_last_failure_type"] = type(exc).__name__
            result["detail_verified"] = detail_verified
            result["listing_verified"] = result["listing_verified"] and bool(
                valid_catalogue
            )
    except Exception as exc:
        result["failure_type"] = type(exc).__name__
        # No raw URLs/HTTP auth headers or cookies are emitted.
        result["failure_category"] = (
            "http_access_denied" if "403" in str(exc) or "401" in str(exc)
            else "http_rate_limited" if "429" in str(exc)
            else "provider_parser_or_network_error"
        )
    result["proof_ok"] = bool(
        result["listing_verified"]
        and (name != "nistp.ru" or result["detail_verified"] is True)
    )
    return result


def main():
    recover = os.getenv("TORGI_RECOVER", "0") == "1"
    configured = {s.source_id: s for s in default_source_specs()}
    if any(s not in configured for s in SOURCES):
        raise RuntimeError("A required provider is missing from main specs")
    with SessionLocal() as session:
        active = session.scalars(
            select(LotSyncRun).where(
                LotSyncRun.status.in_(("queued", "running")),
                LotSyncRun.lease_expires_at > utc_now(),
            )
        ).all()
        before = {
            name: serialize_health(session, name) for name in SOURCES
        }
    report = {
        "mode": "verified_recovery" if recover else "read_only",
        "production_lot_writes": 0,
        "global_network_block": _global_source_network_blocked(),
        "source_pause": {name: _source_is_paused(name) for name in SOURCES},
        "active_sync_runs": [r.id for r in active],
        "health_before": before,
        "probes": [],
    }
    print("SOURCE_HEALTH_BEFORE="+json.dumps(
        report, ensure_ascii=False, default=str
    ), flush=True)
    if (
        report["global_network_block"] or active
        or any(report["source_pause"].values())
    ):
        print("PROBE_ABORTED_PRECONDITION", flush=True)
        raise SystemExit(2)
    for name in SOURCES:
        print(f"PROBING={name}", flush=True)
        probe = asyncio.run(probe_one(name, configured[name]))
        report["probes"].append(probe)
        print("SOURCE_PROBE="+json.dumps(probe, ensure_ascii=False), flush=True)

    all_proven = all(p["proof_ok"] for p in report["probes"])
    cooldowns_done = all(
        status["circuit_state"] in {"half_open", "closed"}
        for status in before.values()
    )
    report["all_proven"] = all_proven
    report["cooldowns_done"] = cooldowns_done
    report["recovery_applied"] = False
    if recover:
        if not (all_proven and cooldowns_done):
            print("RECOVERY_REFUSED="+json.dumps(
                {"all_proven": all_proven, "cooldowns_done": cooldowns_done},
                ensure_ascii=False,
            ), flush=True)
            raise SystemExit(2)
        with SessionLocal() as session:
            statuses = {
                name: serialize_health(session, name)
                for name in SOURCES
            }
            if any(
                value["circuit_state"] not in {"half_open", "closed"}
                for value in statuses.values()
            ):
                raise RuntimeError("Circuit state changed during probes")
            current_active = session.scalars(
                select(LotSyncRun).where(
                    LotSyncRun.status.in_(("queued", "running")),
                    LotSyncRun.lease_expires_at > utc_now(),
                )
            ).first()
            if current_active:
                raise RuntimeError("Nationwide sync began during probing")
            for name in SOURCES:
                record_source_probe(session, name, success=True)
            session.commit()
            after = {
                name: serialize_health(session, name)
                for name in SOURCES
            }
        report["recovery_applied"] = True
        report["health_after"] = after
        print("RECOVERED_ALL_4", flush=True)
    print("DIAGNOSTIC_RESULT="+json.dumps(
        report, ensure_ascii=False, default=str,
    ), flush=True)
    if not all_proven:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
'@
$mode = if ($ApplyVerifiedRecovery) { '1' } else { '0' }
Write-Host "=== Source health and bounded provider probes (recovery=$mode) ===" -ForegroundColor Cyan
$python | & docker.exe exec -i --env "TORGI_RECOVER=$mode" $container python -
$code = $LASTEXITCODE
if ($code -ne 0) {
    throw "Source diagnostic returned $code. Do NOT run the seven-source import."
}
Write-Host '=== Source diagnostic completed ===' -ForegroundColor Green
