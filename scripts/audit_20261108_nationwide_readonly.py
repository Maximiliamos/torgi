"""Read-only evidence report for the 2026-11-08 seven-source nationwide run.

Run inside the Home API/ingestion-worker Docker image. This program performs
SELECT only; a public S3 manifest HEAD/GET is optional and also read-only.
It never schedules ingestion, reopens circuits, geocodes, publishes, or archives.

Important limitation: current PostgreSQL state is NOT a historical snapshot.
Timestamp-window attribution and existing GEO coordinates are best-effort;
without an event-level before/after audit one cannot prove the precise prior
value of updated lots or that all newly geocoded lots entered public map tiles.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from bankrotai.db import (
    LotSyncRun,
    LotSyncSourceRun,
    MapDataset,
    ProcessedLot,
    SessionLocal,
    SourceLot,
)

DEFAULT_RUN_ID = "f572bdff-3db1-41bf-94a4-1f75ab690232"
KNOWN_SOURCES = (
    "torgi.gov.ru", "lot-online.ru", "torgi-russia.ru", "bidexpert.ru",
    "m-ets.ru", "cdtrf.ru", "nistp.ru",
)
CHECKPOINT_KEYS = (
    "catalogue_candidates", "catalogue_total", "catalogue_discovery_ms",
    "detail_attempts", "detail_skipped_unchanged", "detail_cache_hit_rate",
    "items_rejected", "quarantined_records", "quarantine_reasons",
    "rejection_codes", "retry_count", "sql_statements_per_100_lots",
    "pages_per_second", "rows_per_second", "complete_source_inventory",
    "catalogue_total_changes", "detail_rejections",
)


def dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def safe_count(value: object) -> int:
    return int(value or 0)


def checkpoint_metrics(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {key: raw[key] for key in CHECKPOINT_KEYS if key in raw}


def run_report(run_id: str, *, public_s3: bool = False) -> dict[str, Any]:
    with SessionLocal() as db:
        run = db.get(LotSyncRun, run_id)
        if run is None or run.started_at is None:
            raise ValueError(f"No started nationwide run with ID: {run_id}")
        run_finished = run.finished_at
        if run_finished is None:
            raise ValueError("Run has not finished; report would not be stable")
        source_runs = db.scalars(
            select(LotSyncSourceRun)
            .where(LotSyncSourceRun.sync_run_id == run_id)
            .order_by(LotSyncSourceRun.source_system)
        ).all()
        totals = {
            key: sum(safe_count(getattr(row, key)) for row in source_runs)
            for key in ("items_inserted", "items_updated", "items_archived")
        }
        source_rows: list[dict[str, Any]] = []
        archive_windows: list[dict[str, Any]] = []
        geo_windows: list[dict[str, Any]] = []
        for row in source_runs:
            previous = db.scalars(
                select(LotSyncSourceRun)
                .where(
                    LotSyncSourceRun.source_system == row.source_system,
                    LotSyncSourceRun.sync_run_id != run_id,
                    LotSyncSourceRun.status == "success",
                    LotSyncSourceRun.complete_source_run.is_(True),
                    LotSyncSourceRun.finished_at < run.started_at,
                )
                .order_by(LotSyncSourceRun.finished_at.desc())
                .limit(2)
            ).all()
            source_rows.append({
                "source": row.source_system,
                "status": row.status,
                "complete_source_run": row.complete_source_run,
                "pages": row.pages_scanned,
                "seen": row.items_seen,
                "inserted": row.items_inserted,
                "updated": row.items_updated,
                "unchanged": row.items_unchanged,
                "archived": row.items_archived,
                "failed": row.items_failed,
                "error": row.error_message,
                "checkpoint_metrics": checkpoint_metrics(row.checkpoint_json),
                "previous_complete_run_ids": [p.sync_run_id for p in previous],
                "previous_complete_run_seen": [p.items_seen for p in previous],
            })

            # Only a time-window approximation: scheduled expiry and another
            # source action may have run concurrently, and historical rows may
            # have been modified again since. Never equate with exact run causes.
            archive_reasons = db.execute(
                select(
                    SourceLot.archive_reason,
                    func.count(SourceLot.id),
                )
                .where(
                    SourceLot.source_system == row.source_system,
                    SourceLot.is_archived.is_(True),
                    SourceLot.archived_at >= run.started_at,
                    SourceLot.archived_at <= run_finished,
                )
                .group_by(SourceLot.archive_reason)
            ).all()
            archive_count = sum(safe_count(x[1]) for x in archive_reasons)
            archive_windows.append({
                "source": row.source_system,
                "run_reported_archived": row.items_archived,
                "currently_archived_in_run_time_window": archive_count,
                "reason_counts": {str(reason or "missing_reason"): int(n)
                                  for reason, n in archive_reasons},
                "window_matches_run_counter": archive_count == safe_count(row.items_archived),
                "attribution": "time-window only; cannot prove source event without archival event log",
            })

            first_seen = db.execute(
                select(SourceLot.id, SourceLot.external_id, SourceLot.raw_data,
                       ProcessedLot.current_geo_lat,
                       ProcessedLot.current_geo_lon,
                       ProcessedLot.is_archived,
                       ProcessedLot.duplicate_of_id)
                .outerjoin(ProcessedLot, SourceLot.processed_lot_id == ProcessedLot.id)
                .where(
                    SourceLot.source_system == row.source_system,
                    SourceLot.first_seen_at >= run.started_at,
                    SourceLot.first_seen_at <= run_finished,
                )
            ).all()
            with_geo = sum(
                1 for x in first_seen if x.current_geo_lat is not None
                and x.current_geo_lon is not None
                and x.is_archived is False and x.duplicate_of_id is None
            )
            no_geo = len(first_seen) - with_geo
            evidence: dict[str, Any] = {}
            if row.source_system == "nistp.ru":
                evidence = {
                    "catalogue_status": dict(Counter(
                        str((x.raw_data or {}).get("catalogue_source_status_text") or "missing")
                        if isinstance(x.raw_data, dict) else "not_dict"
                        for x in first_seen
                    )),
                    "verified_lot_identity": sum(
                        bool(isinstance(x.raw_data, dict) and
                             x.raw_data.get("individual_lot_identity_verified") is True)
                        for x in first_seen
                    ),
                    "verified_price": sum(
                        bool(isinstance(x.raw_data, dict) and
                             x.raw_data.get("detail_start_price_verified") is True)
                        for x in first_seen
                    ),
                }
            geo_windows.append({
                "source": row.source_system,
                "new_source_rows_by_first_seen_window": len(first_seen),
                "active_canonical_new_rows_with_current_coordinates": with_geo,
                "other_new_rows_including_no_geo_or_archive_or_duplicate": no_geo,
                "equals_reported_inserted": len(first_seen) == safe_count(row.items_inserted),
                "attribution": "currently visible source first_seen_at, not immutable historical map membership",
                "nistp_detail_evidence": evidence if evidence else None,
            })

        dataset = db.scalar(
            select(MapDataset).where(MapDataset.is_current.is_(True))
        )
        recent_map = {
            "version": dataset.version,
            "status": dataset.status,
            "is_current": dataset.is_current,
            "point_count": dataset.point_count,
            "tile_count": dataset.tile_count,
            "published_at": dt(dataset.published_at),
            "published_after_run": (
                dataset.published_at >= run_finished
                if dataset.published_at else False
            ),
        } if dataset is not None else None
        report: dict[str, Any] = {
            "run": {
                "id": run.id, "status": run.status,
                "trigger_type": run.trigger_type,
                "started_at": dt(run.started_at),
                "finished_at": dt(run.finished_at),
                "error": run.error_message,
            },
            "source_count": len(source_runs),
            "expected_seven_present": {r.source_system for r in source_runs} == set(KNOWN_SOURCES),
            "source_runs": source_rows,
            "totals": totals,
            "archive_time_window": archive_windows,
            "new_lot_geo_current_snapshot": geo_windows,
            "map_dataset_current": recent_map,
            "public_s3_manifest": {
                "status": "not_checked",
                "explanation": "Enable --check-public-s3 for independent read-only verification",
            },
            "limitations": [
                "Run counters are aggregated durable source-run rows, not per-lot history",
                "Archive reasons grouped by timestamp are not guaranteed to belong to only this run",
                "New-row GEO counts reflect current ProcessedLot state, not exact historic publication",
                "MapDataset point_count does not prove every new lot has a public map marker",
                "Missing raw catalogue rejection counters cannot be reconstructed from DB",
                "A semantic DB UPDATE proof requires before/after or SQL-statement instrumentation",
            ],
        }
    # Network verification outside the DB session, no write transactions.
    if public_s3 and recent_map is not None:
        try:
            from bankrotai.core import get_settings
            from bankrotai.services.map_object_store import _verify_public_manifest
            manifest = _verify_public_manifest(get_settings(), recent_map["version"])
            report["public_s3_manifest"] = {
                "status": "verified" if isinstance(manifest, dict) else "unexpected_response",
                "version": recent_map["version"],
            }
        except Exception as exc:
            report["public_s3_manifest"] = {
                "status": "verification_failed",
                "error_class": type(exc).__name__,
            }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--check-public-s3", action="store_true")
    args = parser.parse_args()
    report = run_report(args.run_id, public_s3=args.check_public_s3)
    print(json.dumps(report, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
