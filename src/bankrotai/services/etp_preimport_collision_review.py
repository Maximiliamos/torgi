"""Read-only pre-import collision proof for an operator-controlled ETP canary.

A distinct source or a shared cadastral number does not establish the
identity of an auction sale. Reject suspicious overlaps for manual review.
No record IDs, cadastral values, addresses or debtor details are exported.
This audit is never authorization to modify PostgreSQL or publish the map.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select

from bankrotai.db import ProcessedLot, SourceLot


def review_canary_collisions(session, lots: list, *, source: str) -> dict:
    """Inspect max two freshly verified public details; perform SELECT only."""
    if source not in {"m-ets.ru", "cdtrf.ru"} or not 1 <= len(lots) <= 2:
        raise ValueError("Canary collision review requires one or two approved details")
    from bankrotai.connectors.registry.etp_incremental import _property_ready

    seen_ids: set[str] = set()
    seen_cadastres: set[str] = set()
    counts: Counter[str] = Counter()
    for lot in lots:
        external_id = str(getattr(lot, "external_id", "") or "").strip()
        cadastral = str(getattr(lot, "cadastral_number", "") or "").strip()
        if (getattr(lot, "source_system", None) != source
                or not external_id or not cadastral
                or external_id in seen_ids or not _property_ready(lot)):
            raise ValueError("Unverified or repeated ETP identity in collision preflight")
        seen_ids.add(external_id)
        if cadastral in seen_cadastres:
            counts["duplicate_cadastre_within_canary"] += 1
            continue
        seen_cadastres.add(cadastral)

        # Exactly mirror the conservative cross-source guard used at the
        # actual write boundary in etp_canary_persistence.py.
        if session.scalar(
            select(ProcessedLot.id).where(
                ProcessedLot.cadastral_number == cadastral,
                ProcessedLot.source_system != source,
                ProcessedLot.is_archived.is_(False),
            ).limit(1)
        ) is not None:
            counts["cross_source_cadastre_overlap"] += 1
            continue

        # A previously ingested exact ID is not a *new* canary. Keep it out
        # of the first-import acceptance report, even if an update might be
        # idempotent in the general production persister.
        if session.scalar(
            select(ProcessedLot.id).where(
                ProcessedLot.source_system == source,
                ProcessedLot.external_id == external_id,
            ).limit(1)
        ) is not None:
            counts["already_processed_source_id"] += 1
            continue
        if session.scalar(
            select(SourceLot.id).where(
                SourceLot.source_system == source,
                SourceLot.external_id == external_id,
            ).limit(1)
        ) is not None:
            counts["existing_source_provenance"] += 1
            continue
        counts["no_known_identity_collision"] += 1

    available = counts["no_known_identity_collision"]
    return {
        "audit": "etp_preimport_collision_review",
        "source": source,
        "details_reviewed": len(lots),
        "new_unconflicted_candidate_count": available,
        "blocked_candidate_count": len(lots) - available,
        "reason_counts": dict(sorted(counts.items())),
        "ready_for_separate_operator_canary": available == len(lots),
        "read_only": True,
        "database_writes": 0,
        "map_publication_verified": False,
        "race_free_persistence_guaranteed": False,
        "revalidate_in_atomic_import": True,
    }
