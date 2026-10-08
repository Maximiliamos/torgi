"""Read-only P16 production baseline on the *currently running* Home DB.

Only aggregate counts and numeric ProcessedLot IDs are emitted. No raw titles,
addresses, documents, cookies, secrets or arbitrary SourceLot details.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from bankrotai.db import CanonicalLot, ProcessedLot, SessionLocal, SourceLot

ACTIVE = {"active", "open", "scheduled", "published", "applications_submission"}
PROPERTY = {
    "land", "real_estate", "apartment", "house", "commercial", "commercial_room",
    "commercial_building", "commercial_building_with_land", "parking", "unfinished",
    "complex", "office", "retail", "living",
}
MOVABLE = ("moped", "motorcycle", "car", "vehicle", "transport", "movable")
AUTO = {"torgi.gov.ru", "lot-online.ru", "bidexpert.ru", "torgi-russia.ru"}


def build_report(session) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(hours=72)
    active_provenance_ids = set(
        session.scalars(
            select(CanonicalLot.legacy_processed_lot_id)
            .join(SourceLot, SourceLot.canonical_lot_id == CanonicalLot.id)
            .where(
                CanonicalLot.legacy_processed_lot_id.isnot(None),
                SourceLot.source_system.in_(AUTO),
                SourceLot.is_active.is_(True),
                SourceLot.is_archived.is_(False),
                SourceLot.last_seen_at >= cutoff,
            )
        ).all()
    )
    totals = Counter()
    samples = defaultdict(list)
    rows = session.execute(
        select(
            ProcessedLot.id, ProcessedLot.title, ProcessedLot.description,
            ProcessedLot.category, ProcessedLot.auction_status, ProcessedLot.vin,
            ProcessedLot.source_system, ProcessedLot.cadastral_number,
            ProcessedLot.current_geo_source, ProcessedLot.current_geo_confidence,
            ProcessedLot.needs_geo_check,
        ).where(
            ProcessedLot.is_archived.is_(False),
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.current_geo_lat.isnot(None),
            ProcessedLot.current_geo_lon.isnot(None),
        ).order_by(ProcessedLot.id)
    ).yield_per(500)
    for row in rows:
        totals["nonarchived_mapped_primary"] += 1
        title = (row.title or "").casefold()
        desc = (row.description or "").casefold()
        issues = set()
        if ("\u0430\u0440\u0435\u043d\u0434" in title or
            "\u0432\u0438\u0434 \u0442\u043e\u0440\u0433\u043e\u0432 : \u0430\u0440\u0435\u043d\u0434" in desc or
            "\u0432\u0438\u0434 \u0442\u043e\u0440\u0433\u043e\u0432: \u0430\u0440\u0435\u043d\u0434" in desc):
            issues.add("rental")
        if row.vin or row.category in MOVABLE or any(
            term in title for term in ("\u043c\u043e\u043f\u0435\u0434", "\u0430\u0432\u0442\u043e\u043c\u043e\u0431\u0438\u043b", "\u043c\u043e\u0442\u043e\u0446\u0438\u043a\u043b")
        ):
            issues.add("transport_movable")
        if row.auction_status not in ACTIVE:
            issues.add("closed_or_unknown")
        if row.category not in PROPERTY:
            issues.add("invalid_category")
        if row.id not in active_provenance_ids:
            issues.add("no_fresh_source_proof")
        if row.source_system in ("tbankrot", "tbankrot.ru") and row.id not in active_provenance_ids:
            issues.add("stale_tbankrot_only")
        if row.cadastral_number and row.current_geo_source not in ("nspd", "pkk", "ik12_cadastral"):
            issues.add("cadastral_unverified_geo")
        if row.needs_geo_check or row.current_geo_confidence in ("none", "low", "unknown"):
            issues.add("low_geo_quality")
        for name in sorted(issues):
            totals[name] += 1
            if len(samples[name]) < 20:
                samples[name].append(row.id)
        if issues:
            totals["candidate_problem_rows"] += 1
    return {
        "read_only": True,
        "observed_at": now.isoformat() + "Z",
        "source_proof_hours": 72,
        "counts": dict(sorted(totals.items())),
        "sample_ids": dict(sorted(samples.items())),
        "no_mutations": True,
    }


def main() -> None:
    with SessionLocal() as session:
        report = build_report(session)
        session.rollback()
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
