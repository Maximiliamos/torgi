"""Read-only preview of proposed P13–P17 public-map eligibility on live Home DB.

This is a PRE-PUBLICATION GATE, not a repair or a geocode task. No DB mutation,
no credential/PII export. Compares same-day DB candidates to current immutable
MapDataset and never treats a paused source as confirmed coverage.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, not_, or_, select
from sqlalchemy.orm import Session

from bankrotai.db import CanonicalLot, MapDataset, ProcessedLot, SessionLocal, SourceLot
from bankrotai.core import get_settings

ACTIVE = ("active", "scheduled", "published", "open", "applications_submission")
PROPERTY = (
    "land", "real_estate", "apartment", "house", "commercial", "commercial_room",
    "commercial_building", "commercial_building_with_land", "parking", "unfinished",
    "complex", "office", "retail", "living",
)
AUTO = ("torgi.gov.ru", "lot-online.ru", "torgi-russia.ru", "bidexpert.ru")
CAD_GEO = ("nspd", "ik12_cadastral", "pkk")
TITLE_BANS = (
    "аренд", "субаренд", "договор найма", "право пользования",
    "мопед", "мотоцикл", "автомобил", "транспортн", "прицеп",
    "грузовик", "спецтехник", "катер", "лодк", "снегоход",
)
DESC_BANS = (
    "Вид торгов : Аренда", "Вид торгов: Аренда",
    "право заключения договора аренды",
)


def proposed_filters(*, cutoff: datetime) -> tuple:
    independent = (
        select(SourceLot.id)
        .join(CanonicalLot, SourceLot.canonical_lot_id == CanonicalLot.id)
        .where(
            CanonicalLot.legacy_processed_lot_id == ProcessedLot.id,
            SourceLot.source_system.in_(AUTO),
            SourceLot.is_active.is_(True),
            SourceLot.is_archived.is_(False),
            SourceLot.source_status.in_(ACTIVE),
            or_(
                SourceLot.auction_type.is_(None),
                ~or_(
                    SourceLot.auction_type.ilike("%аренд%"),
                    SourceLot.auction_type.ilike("%lease%"),
                    SourceLot.auction_type.ilike("%rent%"),
                ),
            ),
            SourceLot.last_seen_at >= cutoff,
        ).exists()
    )
    return (
        ProcessedLot.duplicate_of_id.is_(None),
        ProcessedLot.is_archived.is_(False),
        ProcessedLot.auction_status.in_(ACTIVE),
        ProcessedLot.category.in_(PROPERTY),
        ProcessedLot.vin.is_(None),
        independent,  # NEVER trust synthetic/test sources for live production.
        ProcessedLot.needs_geo_check.is_(False),
        or_(
            ProcessedLot.cadastral_number.is_(None),
            ProcessedLot.current_geo_source.in_(CAD_GEO),
        ),
        not_(or_(*(ProcessedLot.title.like(f"%{variant}%")
                   for token in TITLE_BANS for variant in
                   (token, token.capitalize(), token.upper())))),
        not_(or_(*(ProcessedLot.description.like(f"%{variant}%")
                   for token in DESC_BANS for variant in
                   (token, token.lower(), token.upper())))),
        ProcessedLot.current_geo_lat.between(-85.05112878, 85.05112878),
        ProcessedLot.current_geo_lon.between(-180.0, 180.0),
    )


def breakdown(session: Session, filters: tuple, attr) -> dict[str, int]:
    rows = session.execute(
        select(attr, func.count(ProcessedLot.id))
        .where(*filters)
        .group_by(attr)
        .order_by(func.count(ProcessedLot.id).desc())
        .limit(35)
    ).all()
    return {str(key or "unknown"): int(n) for key, n in rows}


def build_report(session: Session) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    base = (
        ProcessedLot.duplicate_of_id.is_(None),
        ProcessedLot.is_archived.is_(False),
        ProcessedLot.current_geo_lat.between(-85.05112878, 85.05112878),
        ProcessedLot.current_geo_lon.between(-180.0, 180.0),
    )
    proposed = proposed_filters(cutoff=now - timedelta(hours=72))
    current = session.scalar(
        select(MapDataset).where(MapDataset.is_current.is_(True))
    )
    raw_count = int(session.scalar(select(func.count(ProcessedLot.id)).where(*base)) or 0)
    candidate_count = int(session.scalar(select(func.count(ProcessedLot.id)).where(*proposed)) or 0)
    old_point_count = int(current.point_count or 0) if current else 0
    required_coverage_ratio = float(get_settings().min_map_coverage_ratio)
    preview_ratio = candidate_count / old_point_count if old_point_count else None
    # Even this upper bound can fail the *existing* MapBuilder promotion
    # guard; note a hard blocker instead of masking it with "high CI %".
    coverage_guard_must_block = bool(
        preview_ratio is not None and preview_ratio < required_coverage_ratio
    )
    # Candidate count is an UPPER bound on final new points because additional
    # spatial/locality checks run in MapBuilder. Not a publication success claim.
    candidates_by_source = breakdown(session, proposed, ProcessedLot.source_system)
    candidates_by_region = breakdown(session, proposed, ProcessedLot.region_code)
    candidates_by_category = breakdown(session, proposed, ProcessedLot.category)
    control_cadastrals = (
        "76:23:060521:48", "76:22:010717:536",
        "76:09:082601:3891", "76:02:022201:38",
    )
    control = {}
    for cad in control_cadastrals:
        matches = session.scalars(
            select(ProcessedLot.id).where(ProcessedLot.cadastral_number == cad).limit(15)
        ).all()
        eligible = session.scalars(
            select(ProcessedLot.id).where(*proposed, ProcessedLot.cadastral_number == cad).limit(15)
        ).all()
        control[cad] = {"found": len(matches), "eligible_id_count": len(eligible), "sample_id": [int(x) for x in eligible]}
    vin = "LWJPCBL24RB000657"
    count_vin = int(session.scalar(
        select(func.count(ProcessedLot.id)).where(ProcessedLot.vin == vin)
    ) or 0)
    eligible_vin = int(session.scalar(
        select(func.count(ProcessedLot.id)).where(*proposed, ProcessedLot.vin == vin)
    ) or 0)
    control["reported_moped_vin"] = {"found": count_vin, "eligible": eligible_vin}

    return {
        "read_only": True,
        "no_mutations": True,
        "observed_at_utc": now.isoformat() + "Z",
        "main_contract_preview": "P13–P17 proposed public map SQL, not currently deployed",
        "current_dataset": {
            "version": current.version if current else None,
            "point_count": old_point_count,
            "tile_count": int(current.tile_count or 0) if current else 0,
        },
        "db_unarchived_mapped_primary": raw_count,
        "proposed_maximum_eligible_points": candidate_count,
        "upper_bound_ratio_to_current_dataset": round(preview_ratio, 4) if preview_ratio is not None else None,
        "existing_required_min_coverage_ratio": required_coverage_ratio,
        "preview_fails_existing_coverage_guard": coverage_guard_must_block,
        "safety": {
            "requires_human_inspection_before_activation": True,
            "map_builder_spatial_rejections_not_applied": True,
            "will_not_publish_or_mutate": True,
        },
        "eligible_by_source": candidates_by_source,
        "eligible_by_region": candidates_by_region,
        "eligible_by_category": candidates_by_category,
        "five_owner_cases": control,
    }


def main() -> None:
    with SessionLocal() as session:
        output = build_report(session)
        session.rollback()
    print(json.dumps(output, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
