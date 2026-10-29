"""Bounded read-only evidence for historical missing ProcessedLot.region_code.

This is a triage preview, not a backfill. Only direct, fresh, active official
SourceLot evidence may corroborate the exact cadastral region prefix. Do not
infer an administrative region from address fallback or an unrelated canonical
sibling. No write, status change, geo assignment, or map publication.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from bankrotai.db import ProcessedLot, SessionLocal, SourceLot

ACTIVE = ("active", "scheduled", "published", "open", "applications_submission")
PROPERTY = (
    "land", "real_estate", "apartment", "house", "commercial",
    "commercial_room", "commercial_building", "commercial_building_with_land",
    "parking", "unfinished", "complex", "office", "retail", "living",
)
OFFICIAL = ("torgi.gov.ru", "lot-online.ru", "torgi-russia.ru", "bidexpert.ru")
CAD_GEO = ("nspd", "ik12_cadastral", "pkk")
CAD_NUMBER = re.compile(r"^([0-9]{2}):[0-9]{2}:[0-9]{6,7}:[0-9]+$")


def strict_cadastral_region(number: str | None) -> str | None:
    """Accept a single exact cadastral identifier, not a list or address."""
    match = CAD_NUMBER.fullmatch(str(number or "").strip())
    if not match:
        return None
    region = match.group(1)
    return region if 1 <= int(region) <= 99 else None


def classify_region_evidence(
    cadastral_number: str | None, official_regions: list[str | None],
) -> tuple[str, str | None]:
    """Any apparent code is a review hint, never authorization to backfill."""
    cadastral = strict_cadastral_region(cadastral_number)
    if cadastral is None:
        return "invalid_or_multiple_cadastres", None
    supplied = [str(value or "").strip() for value in official_regions]
    if not supplied or all(not value for value in supplied):
        return "no_direct_official_region_evidence", None
    # An unexpected three-digit/unknown source code requires individual
    # interpretation, not automatic coercion into the cadastral prefix.
    if any(not re.fullmatch(r"[0-9]{2}", value) or int(value) == 0
           for value in supplied):
        return "unsupported_source_region_code", None
    if set(supplied) != {cadastral}:
        return "source_cadastral_region_conflict", None
    return "consistent_requires_manual_approval", cadastral


def build_report(session: Session, *, limit: int = 50) -> dict:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Bounded region review limit must be 1..100")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = session.execute(
        select(ProcessedLot.id, ProcessedLot.cadastral_number)
        .where(
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.is_archived.is_(False),
            or_(ProcessedLot.region_code.is_(None), ProcessedLot.region_code == ""),
            ProcessedLot.auction_status.in_(ACTIVE),
            ProcessedLot.category.in_(PROPERTY),
            ProcessedLot.vin.is_(None),
            ProcessedLot.needs_geo_check.is_(False),
            ProcessedLot.current_geo_source.in_(CAD_GEO),
            ProcessedLot.cadastral_number.is_not(None),
            ProcessedLot.current_geo_lat.between(-85.05112878, 85.05112878),
            ProcessedLot.current_geo_lon.between(-180.0, 180.0),
        )
        .order_by(ProcessedLot.id)
        .limit(limit)
    ).all()
    counts: Counter[str] = Counter()
    reviews = []
    for processed_id, cadastre in rows:
        # The explicit processed_lot_id is necessary: a legacy-only canonical
        # group can contain different sales of the same physical asset.
        direct_regions = session.scalars(
            select(SourceLot.region_code).where(
                SourceLot.processed_lot_id == processed_id,
                SourceLot.source_system.in_(OFFICIAL),
                SourceLot.is_active.is_(True),
                SourceLot.is_archived.is_(False),
                SourceLot.source_status.in_(ACTIVE),
                SourceLot.last_seen_at >= now - timedelta(hours=72),
            )
        ).all()
        reason, suggestion = classify_region_evidence(cadastre, direct_regions)
        counts[reason] += 1
        reviews.append({
            "processed_lot_id": int(processed_id),
            "classification": reason,
            "review_region_hint": suggestion,
            "direct_fresh_source_region_count": len(direct_regions),
            "operation": "manual_review_only",
        })
    return {
        "report": "p13_historical_region_evidence_readonly",
        "observed_at_utc": now.isoformat() + "Z",
        "read_only": True,
        "no_mutations": True,
        "sample_limit": limit,
        "sample_size": len(rows),
        "sample_exhaustive": False,
        "same_as_full_p13_eligibility_population": False,
        "historical_region_backfill_completed": False,
        "restore_verified_backup_required_before_write": True,
        "classification_counts_in_sample": dict(sorted(counts.items())),
        "bounded_review_items": reviews,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    with SessionLocal() as session:
        report = build_report(session, limit=args.limit)
        session.rollback()
    print(json.dumps(report, ensure_ascii=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
