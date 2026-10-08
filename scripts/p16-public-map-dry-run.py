#!/usr/bin/env python3
"""P16 read-only audit of existing public map candidates.

Never edits/deletes data, triggers GEO, rebuilds a map, or touches production
credentials. Run only after a verified backup when inspecting a live host.
Output deliberately contains counts and numeric row IDs, not PII/addresses.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.db import ProcessedLot, SourceLot, SessionLocal
from bankrotai.services.public_map_policy import (
    PUBLIC_ACTIVE_STATUSES,
    PUBLIC_EXCLUDED_TITLE_TERMS,
    public_map_predicates,
)
from bankrotai.services.real_estate_filter import REAL_ESTATE_CATEGORIES

TRUSTED_CADASTRAL_GEO = frozenset({"nspd", "ik12_cadastral", "pkk"})
ACTIVE_PUBLIC_SOURCES = frozenset({
    "torgi.gov.ru", "lot-online.ru", "bidexpert.ru", "torgi-russia.ru",
})
CLOSED_MARKERS = ("closed", "completed", "finished", "cancelled", "expired", "заверш", "отмен", "закрыт")


def audit_public_map_candidates(session: Session, *, limit_examples: int = 25) -> dict[str, Any]:
    """Check public candidate defects and stale source-only claims without writes."""
    limit_examples = max(0, min(100, limit_examples))
    as_of = datetime.now(timezone.utc).replace(tzinfo=None)
    source_fresh_cutoff = as_of - timedelta(hours=72)
    projection: dict[int, list[SourceLot]] = defaultdict(list)
    for row in session.scalars(select(SourceLot).where(SourceLot.processed_lot_id.isnot(None))).yield_per(500):
        projection[int(row.processed_lot_id)].append(row)

    counts: Counter[str] = Counter()
    samples: dict[str, list[int]] = defaultdict(list)
    candidates = session.scalars(
        select(ProcessedLot)
        .where(
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.is_archived.is_(False),
            ProcessedLot.current_geo_lat.isnot(None),
            ProcessedLot.current_geo_lon.isnot(None),
        )
        .order_by(ProcessedLot.id)
    ).yield_per(500)
    for lot in candidates:
        counts["mapped_nonarchived_rows"] += 1
        title = (lot.title or "").casefold().replace("ё", "е")
        desc = (lot.description or "").casefold().replace("ё", "е")
        reasons: set[str] = set()
        if any(token in title for token in ("аренд", "субаренд", "договор найма", "право пользования")) or "право заключения договора аренды" in desc:
            reasons.add("rental")
        if lot.vin or any(token in title for token in ("мопед", "мотоцикл", "автомобил", "прицеп", "грузовик", "транспортн")):
            reasons.add("transport_or_movable")
        if lot.auction_status not in PUBLIC_ACTIVE_STATUSES:
            reasons.add("closed_or_unknown_status")
        if lot.category not in REAL_ESTATE_CATEGORIES:
            reasons.add("non_real_estate_category")
        if lot.needs_geo_check:
            reasons.add("needs_geo_check")
        if lot.cadastral_number and (lot.current_geo_source or "") not in TRUSTED_CADASTRAL_GEO:
            reasons.add("cadastral_address_fallback_unverified")
        if not (-85.05112878 <= (lot.current_geo_lat or 0) <= 85.05112878 and -180 <= (lot.current_geo_lon or 0) <= 180):
            reasons.add("out_of_range_coordinates")

        # Direct source rows are a conservative signal; canonical siblings
        # require an explicit follow-up join, so absence is marked unverified.
        sources = projection.get(lot.id, [])
        fresh = [
            p for p in sources
            if p.source_system in ACTIVE_PUBLIC_SOURCES
            and p.is_active and not p.is_archived
            and p.last_seen_at and p.last_seen_at >= source_fresh_cutoff
            and not any(term in (p.source_status or "").casefold() for term in CLOSED_MARKERS)
        ]
        if not fresh:
            reasons.add("no_verified_fresh_direct_source_projection")
        if lot.source_system == "tbankrot.ru" and not fresh:
            reasons.add("legacy_tbankrot_without_fresh_source")
        if any(term in title for term in PUBLIC_EXCLUDED_TITLE_TERMS):
            reasons.add("title_policy_rejection")
        for reason in sorted(reasons):
            counts[reason] += 1
            if len(samples[reason]) < limit_examples:
                samples[reason].append(int(lot.id))
        if reasons:
            counts["rows_with_findings"] += 1

    # Deterministic, stratified reservoir sample for the requested P17
    # production acceptance (50 land, 30 premises, 20 houses/flats).
    quotas = {"land": 50, "premises": 30, "homes": 20}
    groups = {
        "land": {"land"},
        "premises": {"commercial_room", "commercial", "office", "retail", "real_estate"},
        "homes": {"apartment", "house", "living"},
    }
    randomizer = random.Random(20261008)
    seen: Counter[str] = Counter()
    stratified: dict[str, list[int]] = {name: [] for name in quotas}
    eligible_rows = session.execute(
        select(ProcessedLot.id, ProcessedLot.category)
        .where(*public_map_predicates())
        .order_by(ProcessedLot.id)
    ).yield_per(500)
    for lot_id, category in eligible_rows:
        group = next((name for name, categories in groups.items() if category in categories), None)
        if group is None:
            continue
        seen[group] += 1
        subset = stratified[group]
        if len(subset) < quotas[group]:
            subset.append(int(lot_id))
        else:
            replacement = randomizer.randrange(seen[group])
            if replacement < quotas[group]:
                subset[replacement] = int(lot_id)

    return {
        "dry_run": True,
        "mutation_count": 0,
        "generated_at_utc": as_of.isoformat() + "Z",
        "source_freshness_window_hours": 72,
        "counts": dict(sorted(counts.items())),
        "sample_processed_lot_ids": dict(sorted(samples.items())),
        "p17_sample_random_seed": 20261008,
        "p17_representative_sample_ids": stratified,
        "p17_eligible_group_counts": dict(seen),
        "p17_requested_sample_sizes": quotas,
        "caveat": "Direct source rows only; canonical sibling and paused-circuit state require separate verification",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-limit", type=int, default=25)
    args = parser.parse_args()
    with SessionLocal() as session:
        report = audit_public_map_candidates(session, limit_examples=args.sample_limit)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
