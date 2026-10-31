"""Operator-only first production pass across every new ETP profile.

Each provider keeps its evidence-backed pagination contract. The campaign is
strictly non-reconciling: a partial/unavailable provider can never archive an
existing lot. One failed source is reported and the remaining sources continue.
"""
from __future__ import annotations

import argparse
import json
import os
import time

from sqlalchemy import func, or_, select

from bankrotai.connectors.etp_sources import PROFILES, SOURCE_CONNECTOR_TYPES, EtpSearchFilters
from bankrotai.connectors.registry import connector_registry
from bankrotai.db import LotSyncRun, LotSyncSourceRun, ProcessedLot, SessionLocal
from bankrotai.logic import reconcile_cross_source_duplicates
from bankrotai.services.ingestion import (
    NationwideIngestionService, SourceSyncSpec, SyncAlreadyRunningError,
)

ALL_SOURCE_IDS = tuple(profile.source_id for profile in PROFILES)
BLOCKED = {profile.source_id: profile.blocked_reason for profile in PROFILES if profile.blocked_reason}
RUNNABLE = tuple(source for source in ALL_SOURCE_IDS if source not in BLOCKED)


class SequentialCampaignService(NationwideIngestionService):
    """Keep provider traffic serial while preserving the standard run journal."""

    async def _run_active(self, run_id: str, specs: tuple[SourceSyncSpec, ...]) -> dict:
        results = []
        for spec in specs:
            results.append(await self._sync_source(run_id, spec))
        with self.session_factory() as session:
            merged = reconcile_cross_source_duplicates(session)
            session.commit()
        expired = self._expire_elapsed_auctions()
        if results:
            results[-1].duplicates_merged += merged
        status = (
            "success" if all(item.status == "success" for item in results)
            else ("failed" if all(item.status == "failed" for item in results) else "partial")
        )
        return {
            "status": status,
            "sources": [self._result_payload(item) for item in results],
            "expired_after_auction": expired,
            "profile": {},
        }


def _authorized() -> None:
    required = (
        "ETP_ALL_SOURCES_OPERATOR_ENABLED",
        "ETP_CANARY_BACKUP_VERIFIED",
        "ETP_OPERATOR_CANARY_IMPORT_ENABLED",
        "CDTRF_OPERATOR_SYNC_ENABLED",
    )
    missing = [name for name in required if os.getenv(name) != "1"]
    if missing:
        raise PermissionError("All-source campaign authorization missing: " + ",".join(missing))


def _connector(source_id: str):
    # CDTRF has a separately validated API/detail crawler. Every other source
    # uses its own profile (GET, form POST, ASP.NET postback, or JSON API).
    if source_id == "cdtrf.ru":
        return connector_registry.create(source_id)
    return SOURCE_CONNECTOR_TYPES[source_id]()


def _specs() -> tuple[SourceSyncSpec, ...]:
    from bankrotai.connectors.registry.cdtrf_sync import operator_sync_spec

    specs: list[SourceSyncSpec] = []
    for source_id in RUNNABLE:
        if source_id == "cdtrf.ru":
            specs.append(operator_sync_spec())
        else:
            specs.append(SourceSyncSpec(
                source_id,
                EtpSearchFilters(page=1),
                reconcile_missing=False,
                max_batches=500,
            ))
    return tuple(specs)


def snapshot() -> dict:
    with SessionLocal() as session:
        rows = session.execute(
            select(
                ProcessedLot.source_system,
                func.count(ProcessedLot.id),
                func.count(ProcessedLot.id).filter(or_(
                    ProcessedLot.current_price.is_not(None),
                    ProcessedLot.start_price.is_not(None),
                )),
                func.count(ProcessedLot.id).filter(
                    ProcessedLot.current_geo_lat.is_not(None),
                ),
            )
            .where(ProcessedLot.source_system.in_(ALL_SOURCE_IDS))
            .group_by(ProcessedLot.source_system)
        ).all()
    observed = {
        source: {"records": int(records), "with_price": int(priced), "with_geo": int(geo)}
        for source, records, priced, geo in rows
    }
    return {
        "sources": {
            source: observed.get(source, {"records": 0, "with_price": 0, "with_geo": 0})
            for source in ALL_SOURCE_IDS
        },
        "blocked": BLOCKED,
    }


def progress() -> dict:
    with SessionLocal() as session:
        run = session.scalar(
            select(LotSyncRun)
            .where(LotSyncRun.triggered_by == "all_new_etp_operator_campaign")
            .order_by(LotSyncRun.id.desc()).limit(1)
        )
        if run is None:
            return {"status": "waiting", "percent": 0, "completed": 0, "total": len(RUNNABLE)}
        rows = session.scalars(
            select(LotSyncSourceRun)
            .where(LotSyncSourceRun.sync_run_id == run.id)
            .order_by(LotSyncSourceRun.id)
        ).all()
    completed = sum(row.status in {"success", "failed"} for row in rows)
    current = next((row for row in rows if row.status == "running"), None)
    source_percent = 0
    if current is not None:
        checkpoint = current.checkpoint_json if isinstance(current.checkpoint_json, dict) else {}
        position = int(checkpoint.get("progress_current") or current.pages_scanned or 0)
        total_pages = int(checkpoint.get("progress_total") or checkpoint.get("total_pages") or 0)
        source_percent = min(99, int(position * 100 / total_pages)) if total_pages else 0
    overall = min(99, int((completed + source_percent / 100) * 100 / len(RUNNABLE)))
    if run.status in {"success", "partial", "failed"}:
        overall = 100
    return {
        "run_id": str(run.id), "status": run.status, "percent": overall,
        "completed": completed, "total": len(RUNNABLE),
        "current_source": current.source_system if current is not None else None,
        "current_source_percent": source_percent,
        "sources": [{
            "source": row.source_system, "status": row.status,
            "pages": row.pages_scanned, "seen": row.items_seen,
            "inserted": row.items_inserted, "updated": row.items_updated,
            "failed": row.items_failed, "error": row.error_message,
        } for row in rows],
    }


def run_import() -> dict:
    _authorized()
    specs = _specs()
    service = SequentialCampaignService(SessionLocal, connector_factory=_connector)
    run_id = None
    for attempt in range(120):
        try:
            run_id = service.create_run(
                triggered_by="all_new_etp_operator_campaign",
                trigger_type="manual_all_new_etp_nonreconciling",
                total_sources=len(specs),
            )
            break
        except SyncAlreadyRunningError:
            if attempt == 119:
                raise
            time.sleep(30)
    if run_id is None:  # pragma: no cover - defensive type narrowing
        raise RuntimeError("Could not acquire the ingestion campaign slot")
    result = __import__("asyncio").run(service.run(run_id, specs))
    return {"run_id": run_id, "result": result, "blocked": BLOCKED, "snapshot": snapshot()}


def geocode_all(*, batch_size: int = 250) -> dict:
    if not 1 <= batch_size <= 500:
        raise ValueError("Invalid GEO batch size")
    from bankrotai.services.geo_backfill import geocode_pending_lots

    with SessionLocal() as session:
        ids = list(session.scalars(
            select(ProcessedLot.id).where(
                ProcessedLot.source_system.in_(ALL_SOURCE_IDS),
                ProcessedLot.is_archived.is_(False),
                ProcessedLot.duplicate_of_id.is_(None),
                or_(ProcessedLot.current_geo_lat.is_(None), ProcessedLot.current_geo_lon.is_(None)),
            ).order_by(ProcessedLot.id)
        ).all())
    batches = []
    for offset in range(0, len(ids), batch_size):
        chunk = ids[offset:offset + batch_size]
        response = geocode_pending_lots(
            SessionLocal, limit=len(chunk), lot_ids=chunk, refresh_strategy=False,
        )
        if response.get("status") in {"busy", "paused"}:
            raise RuntimeError("All-source GEO campaign blocked by existing GEO work")
        batches.append({"requested": len(chunk), **response})
    return {"requested": len(ids), "batches": batches, "snapshot": snapshot()}


def publish_map() -> dict:
    from bankrotai.services.map_builder import build_map_dataset

    publication = build_map_dataset(SessionLocal)
    if publication.get("build_status") != "success":
        raise RuntimeError("All-source map publication failed")
    return {"map": publication, "snapshot": snapshot()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("import", "progress", "geocode", "map", "audit"))
    parser.add_argument("--batch-size", type=int, default=250)
    args = parser.parse_args()
    if args.stage == "import":
        result = run_import()
    elif args.stage == "progress":
        result = progress()
    elif args.stage == "geocode":
        result = geocode_all(batch_size=args.batch_size)
    elif args.stage == "map":
        result = publish_map()
    else:
        result = snapshot()
    print(json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":")))


if __name__ == "__main__":
    main()
