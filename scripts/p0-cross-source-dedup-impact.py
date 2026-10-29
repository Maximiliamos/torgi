"""P0 read-only survey of potentially unsafe historic cross-source dedup.

Rows may represent legitimate multi-source republications. A shared cadastral
number never proves the same sale. Report aggregate evidence only; do not split
or change records without an independently reviewed migration and backup.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from bankrotai.db import ProcessedLot, SessionLocal, SourceLot


def assess_pair(
    *, primary_cadastre: str | None, duplicate_cadastre: str | None,
    primary_region: str | None, duplicate_region: str | None,
    primary_status: str | None, duplicate_status: str | None,
) -> tuple[str, ...]:
    issues = []
    first, second = (primary_cadastre or "").strip(), (duplicate_cadastre or "").strip()
    if first and second:
        issues.append("same_cadastre_not_sale_identity" if first == second
                      else "conflicting_cadastres")
    else:
        issues.append("missing_cadastral_evidence")
    if primary_region and duplicate_region and primary_region != duplicate_region:
        issues.append("region_conflict")
    if primary_status and duplicate_status and primary_status != duplicate_status:
        issues.append("source_status_divergence")
    return tuple(issues)


def build_report(session) -> dict:
    primary = aliased(ProcessedLot)
    duplicate = aliased(ProcessedLot)
    query = select(
        primary.id, duplicate.id,
        primary.cadastral_number, duplicate.cadastral_number,
        primary.region_code, duplicate.region_code,
        primary.auction_status, duplicate.auction_status,
        primary.source_system, duplicate.source_system,
    ).select_from(duplicate).join(
        primary, primary.id == duplicate.duplicate_of_id
    ).where(
        primary.source_system != duplicate.source_system
    ).order_by(duplicate.id)

    counts: Counter[str] = Counter()
    pair_sources: Counter[str] = Counter()
    samples: dict[str, list[dict[str, int]]] = defaultdict(list)
    for row in session.execute(query).yield_per(500):
        counts["cross_source_merged_pairs"] += 1
        src = f"{row[8]} -> {row[9]}"
        pair_sources[src] += 1
        reasons = assess_pair(
            primary_cadastre=row[2], duplicate_cadastre=row[3],
            primary_region=row[4], duplicate_region=row[5],
            primary_status=row[6], duplicate_status=row[7],
        )
        for reason in reasons:
            counts[reason] += 1
            if len(samples[reason]) < 20:
                samples[reason].append({"primary_id": int(row[0]), "duplicate_id": int(row[1])})
    # Additional P0 path: a canonical asset may contain multiple SourceLot
    # publications even when no ProcessedLot.duplicate_of_id was set.
    # This is a REVIEW CANDIDATE, not a proven false merge.
    group_query = (
        select(SourceLot.canonical_lot_id)
        .group_by(SourceLot.canonical_lot_id)
        .having(func.count(func.distinct(SourceLot.source_system)) > 1)
        .order_by(SourceLot.canonical_lot_id)
    )
    for (canonical_id,) in session.execute(group_query).yield_per(500):
        counts["canonical_groups_multiple_sources"] += 1
        if len(samples["canonical_groups_multiple_sources"]) < 20:
            samples["canonical_groups_multiple_sources"].append({
                "canonical_id": int(canonical_id),
            })
    return {
        "report": "p0_historical_cross_source_dedup_impact",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "read_only": True, "no_mutations": True,
        "interpretation": "potential_review_candidates_not_proven_false_merges",
        "counts": dict(sorted(counts.items())),
        "source_pairs": dict(sorted(pair_sources.items())),
        "sample_ids": dict(sorted(samples.items())),
    }


if __name__ == "__main__":
    with SessionLocal() as session:
        report = build_report(session)
        session.rollback()
    print(json.dumps(report, ensure_ascii=True, separators=(",", ":")))
