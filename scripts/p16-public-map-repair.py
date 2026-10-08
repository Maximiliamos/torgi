#!/usr/bin/env python3
"""P16 bounded, provenance-preserving cleanup of objectively invalid map lots.

Dry-run by default. Live application requires a locally verified checksum of a
restore-tested PostgreSQL dump, explicit --apply, and --approved-batch. No
physical DELETE, no implicit GEO task scheduling and no source unpausing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.db import LotGeoSnapshot, LotStatusEvent, LotStatusHistory, ProcessedLot, SessionLocal, SourceLot


RENT_HEADS = ("аренда ", "право заключения договора аренды ", "субаренда ")
MOVABLE_WORDS = ("мопед", "мотоцикл", "автомобиль", "автомобил", "прицеп", "грузовик")
CLOSED_STATUSES = {"closed", "completed", "finished", "cancelled", "expired", "failed"}
MOVABLE_CATEGORIES = {"car", "vehicle", "transport", "movable"}


def proposed_archive_reason(lot: ProcessedLot) -> str | None:
    title = (lot.title or "").casefold().replace("ё", "е").strip()
    if lot.vin or lot.category in MOVABLE_CATEGORIES or any(word in title for word in MOVABLE_WORDS):
        return "movable_property"
    desc = (lot.description or "").casefold().replace("ё", "е")
    if any(title.startswith(prefix) for prefix in RENT_HEADS) or any(
        phrase in desc for phrase in (
            "право заключения договора аренды",
            "вид торгов : аренда", "вид торгов: аренда",
        )
    ):
        return "rental"
    if (lot.auction_status or "") in CLOSED_STATUSES:
        return "source_closed"
    return None


def verified_backup(metadata_path: Path) -> None:
    """Refuse applying changes without both a restored backup and matching bytes."""
    details = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    created_text = str(details.get("created_at") or "").replace("Z", "+00:00")
    try:
        created_at = datetime.fromisoformat(created_text)
    except ValueError as exc:
        raise ValueError("Backup creation timestamp is missing or invalid") from exc
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)
    if age < timedelta(minutes=-5) or age > timedelta(hours=60):
        raise ValueError("Backup must be fresh (at most 60h) before live repair")
    if details.get("restore_verification") != "passed":
        raise ValueError("Backup metadata has no successful isolated restore verification")
    dump_path = metadata_path.with_suffix(".dump")
    if not dump_path.is_file():
        raise ValueError("Backup dump not found adjacent to metadata")
    if dump_path.stat().st_size != details.get("size_bytes"):
        raise ValueError("Backup dump size mismatch")
    checksum = hashlib.sha256()
    with dump_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            checksum.update(chunk)
    if checksum.hexdigest().lower() != str(details.get("sha256", "")).lower():
        raise ValueError("Backup dump SHA256 mismatch")
    if details.get("source_schema_revision") != details.get("restored_schema_revision"):
        raise ValueError("Backup source/restore schema revisions disagree")


def repair_candidate_batch(session: Session, *, limit: int = 100, apply: bool = False) -> dict:
    """Only archive unambiguous invalid rows; never remove source history."""
    limit = max(1, min(100, int(limit)))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = session.scalars(
        select(ProcessedLot).where(
            ProcessedLot.is_archived.is_(False),
            ProcessedLot.duplicate_of_id.is_(None),
        ).order_by(ProcessedLot.id)
    ).yield_per(500)
    reasons: Counter[str] = Counter()
    examples: dict[str, list[int]] = {}
    changes = 0
    for lot in rows:
        reason = proposed_archive_reason(lot)
        if reason is None:
            continue
        reasons[reason] += 1
        examples.setdefault(reason, [])
        if len(examples[reason]) < 10:
            examples[reason].append(int(lot.id))
        if changes >= limit:
            continue
        changes += 1
        if not apply:
            continue
        previous = lot.auction_status
        lot.is_archived = True
        lot.archived_at = now
        # Keep upstream status unchanged: business exclusion isn't proof that
        # an otherwise active auction was legally closed by the provider.
        session.add(LotStatusHistory(
            lot_id=lot.id, old_status=previous, new_status="archived",
            changed_at=now, source="p16_historical_repair",
        ))
        session.add(LotStatusEvent(
            lot_id=lot.id, source="p16_historical_repair",
            source_status=str(previous or "unknown"), normalized_status="archived",
            status_confidence="high", trace_reason=reason, observed_at=now,
            metadata_json={"archive_reason": reason, "batch": "p16"},
        ))
        for row in session.scalars(
            select(SourceLot).where(SourceLot.processed_lot_id == lot.id)
        ):
            row.is_archived = True
            row.is_active = False
            row.archived_at = now
            row.archive_reason = reason
    if apply:
        session.commit()
    else:
        session.rollback()
    return {
        "dry_run": not apply,
        "archived_rows": changes if apply else 0,
        "proposed_rows_in_first_batch": changes,
        "reason_counts": dict(sorted(reasons.items())),
        "sample_processed_lot_ids": examples,
        "no_physical_deletes": True,
        "remaining_batches_require_independent_review": sum(reasons.values()) > limit,
    }


def quarantine_weak_cadastral_geo(
    session: Session, *, limit: int = 25, region_code: str = "76", apply: bool = False
) -> dict:
    """P16: bounded Yaroslavl-first revalidation, preserving historical GEO."""
    limit = max(1, min(50, int(limit)))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    accepted = ("nspd", "ik12_cadastral", "pkk")
    rows = session.scalars(
        select(ProcessedLot).where(
            ProcessedLot.is_archived.is_(False),
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.region_code == region_code,
            ProcessedLot.cadastral_number.isnot(None),
            ProcessedLot.current_geo_lat.isnot(None),
            ProcessedLot.current_geo_lon.isnot(None),
            ~ProcessedLot.current_geo_source.in_(accepted),
        ).order_by(ProcessedLot.id).limit(limit)
    ).all()
    ids = [int(item.id) for item in rows]
    if apply:
        for lot in rows:
            session.add(LotGeoSnapshot(
                lot_id=lot.id,
                geo_source=str(lot.current_geo_source or "unknown"),
                geo_method="p16_quarantined_geo_hint",
                geo_confidence=str(lot.current_geo_confidence or "unknown"),
                centroid_lat=lot.current_geo_lat,
                centroid_lon=lot.current_geo_lon,
                observed_at=now,
                metadata_json={
                    "reason": "cadastral_address_fallback_unverified",
                    "previous_geo_source": lot.current_geo_source,
                    "cadastral_number": lot.cadastral_number,
                },
                trace_reason="P16: coordinate quarantined for exact cadastral revalidation",
            ))
            lot.current_geo_lat = None
            lot.current_geo_lon = None
            lot.current_geo_source = None
            lot.current_geo_confidence = None
            lot.current_geo_observed_at = None
            lot.needs_geo_check = True
            lot.geo_input_hash = None
        session.commit()
    else:
        session.rollback()
    return {
        "mode": "bounded_geo_revalidation",
        "dry_run": not apply,
        "region_code": region_code,
        "candidate_count": len(ids),
        "changed": len(ids) if apply else 0,
        "lot_ids": ids,
        "snapshot_evidence_preserved": True,
        "next_step": "run bounded geocoding worker and compare exact cadastral GEO before new map publication",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--geo-canary", action="store_true", help="quarantine weak cadastral GEO instead of archival repair")
    parser.add_argument("--region", default="76", help="bounded GEO canary region")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approved-batch", help="must be P16-APPROVED to permit mutations")
    parser.add_argument("--backup-metadata", type=Path)
    args = parser.parse_args()
    if args.apply:
        if args.approved_batch != "P16-APPROVED" or args.backup_metadata is None:
            parser.error("--apply requires --approved-batch P16-APPROVED and --backup-metadata")
        verified_backup(args.backup_metadata)
    with SessionLocal() as session:
        if args.geo_canary:
            result = quarantine_weak_cadastral_geo(session, limit=args.limit, region_code=args.region, apply=args.apply)
        else:
            result = repair_candidate_batch(session, limit=args.limit, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
