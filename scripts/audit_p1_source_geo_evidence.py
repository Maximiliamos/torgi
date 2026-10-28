"""Read-only evidence snapshot of auction source completeness and map GEO rejects.

Run against a configured copy of TORGI with DATABASE_URL set; does not
scrape, publish, archive, correct coordinates, or mutate database records.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from bankrotai.db import ProcessedLot, SessionLocal
from bankrotai.region_sanity import coordinate_region_sanity_rejection_reason
from bankrotai.regions import normalize_region_code
from bankrotai.services.map_bundle_store import normalize_map_region_code
from bankrotai.services.map_builder import MAX_WEB_MERCATOR_LAT
from bankrotai.services.quality import (
    data_quality_snapshot,
    list_source_health,
    operational_quality_report,
)


def region_rejection_reason(
    lat: float, lon: float, region: str | None, cadastre: str | None
) -> str | None:
    """Follow the exact rejection precedence of map_delivery_reconciliation_report."""
    initial = coordinate_region_sanity_rejection_reason(lat, lon, region)
    if initial == "unsupported_region_code":
        return initial
    bundle_region = normalize_map_region_code(region, cadastre)
    check_bundle = coordinate_region_sanity_rejection_reason(lat, lon, bundle_region)
    if check_bundle:
        return (
            "unsupported_bundle_region_code"
            if check_bundle == "unsupported_region_code"
            else f"bundle_{check_bundle}"
        )
    resolved = normalize_region_code(bundle_region) or normalize_region_code(region) or region
    return coordinate_region_sanity_rejection_reason(lat, lon, resolved)


def collect_evidence(session, *, max_samples: int = 2000) -> dict:
    if not 1 <= max_samples <= 10_000:
        raise ValueError("max_samples must be between 1 and 10000")
    report = operational_quality_report(session, problem_limit=min(max_samples, 1000))
    counters: Counter[str] = Counter()
    rejected: list[dict] = []
    candidates = session.execute(
        select(
            ProcessedLot.id,
            ProcessedLot.source_system,
            ProcessedLot.external_id,
            ProcessedLot.region_code,
            ProcessedLot.cadastral_number,
            ProcessedLot.current_geo_lat,
            ProcessedLot.current_geo_lon,
        ).where(
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.is_archived.is_(False),
            ProcessedLot.current_geo_lat.between(-MAX_WEB_MERCATOR_LAT, MAX_WEB_MERCATOR_LAT),
            ProcessedLot.current_geo_lon.between(-180.0, 180.0),
        )
    ).yield_per(1000)
    for row in candidates:
        reason = region_rejection_reason(
            float(row.current_geo_lat),
            float(row.current_geo_lon),
            row.region_code,
            row.cadastral_number,
        )
        if reason:
            counters[reason] += 1
            if len(rejected) < max_samples:
                rejected.append({
                    "lot_id": row.id,
                    "source": row.source_system,
                    "external_id": row.external_id,
                    "region": row.region_code,
                    "cadastre": row.cadastral_number,
                    "latitude": row.current_geo_lat,
                    "longitude": row.current_geo_lon,
                    "reason": reason,
                })
    states = list_source_health(session)
    sources = [
        {
            # Do not expose raw source errors: they can contain URL query secrets.
            "source": state.source_system,
            "status": state.status,
            "freshness": state.freshness_status,
            "coverage": state.coverage_status,
            "last_complete_success_at": state.last_complete_success_at,
            "last_success_at": state.last_success_at,
            "circuit_state": state.circuit_state,
            "last_pages_scanned": state.last_pages_scanned,
            "last_items_failed": state.last_items_failed,
            "last_complete_source_run": state.last_complete_source_run,
        }
        for state in states
    ]
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "data_quality": data_quality_snapshot(session).model_dump(mode="json"),
        "source_health": sources,
        "lot_data_quality": report["lot_data_quality"],
        "source_date_quality": report["source_date_quality"],
        "map_delivery": report["map_delivery"],
        "geo_rejections_total": sum(counters.values()),
        "geo_rejections_by_reason": dict(sorted(counters.items())),
        "geo_rejections_sample": rejected,
        "geo_sample_truncated": sum(counters.values()) > len(rejected),
        "note": "Freshness/coverage reflect DB evidence; independent live source completeness is NOT proven.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-samples", type=int, default=2000)
    args = parser.parse_args()
    with SessionLocal() as session:
        evidence = collect_evidence(session, max_samples=args.max_samples)
        # End a read-only transaction; no persistent writes.
        session.rollback()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"Evidence saved: {args.output}; rejected_geo={evidence['geo_rejections_total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
