"""P13 pre-production read-only impact must not mutate or miscount sample lots."""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, MapDataset, ProcessedLot, SourceLot


PATH = Path(__file__).resolve().parents[1] / "scripts/p13-production-eligibility-impact.py"
spec = importlib.util.spec_from_file_location("p13_impact", PATH)
assert spec is not None and spec.loader is not None
impact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(impact)


def test_live_impact_preview_is_only_readonly_and_conservative() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        current = MapDataset(
            version="sample-r6-bundle-s3", status="ready",
            is_current=True, point_count=100, tile_count=24,
        )
        session.add(current)
        for label, status, category, title, cadastral, vin in (
            ("sale", "active", "land", "Продажа земельного участка", None, None),
            ("rent", "active", "land", "Аренда земельного участка", "76:22:010717:536", None),
            ("closed", "closed", "land", "Продажа участка", "76:09:082601:3891", None),
            ("moped", "active", "vehicle", "Мопед Альфа", None, "LWJPCBL24RB000657"),
        ):
            row = ProcessedLot(
                external_id=label, source="test", source_system="torgi.gov.ru",
                title=title, description="", category=category, auction_status=status,
                cadastral_number=cadastral, vin=vin, is_archived=False,
                current_geo_lat=57.6, current_geo_lon=39.8,
                current_geo_source="nspd", needs_geo_check=False,
            )
            session.add(row)
            session.flush()
            canonical = CanonicalLot(
                canonical_key=f"test-p13-impact-{label}",
                legacy_processed_lot_id=row.id, title=row.title, category=row.category,
            )
            session.add(canonical)
            session.flush()
            session.add(SourceLot(
                canonical_lot_id=canonical.id, processed_lot_id=row.id,
                source_system="torgi.gov.ru", external_id=label, source_status=status,
                is_active=True, is_archived=False,
                last_seen_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ))
        session.commit()
        result = impact.build_report(session)
        assert result["read_only"] is True
        assert result["no_mutations"] is True
        assert result["db_unarchived_mapped_primary"] == 4
        assert result["proposed_maximum_eligible_points"] == 1
        assert result["upper_bound_ratio_to_current_dataset"] == 0.01
        assert result["preview_fails_existing_coverage_guard"] is True
        assert result["existing_required_min_coverage_ratio"] >= 0.01
        assert result["five_owner_cases"]["76:22:010717:536"]["eligible_id_count"] == 0
        assert result["five_owner_cases"]["76:09:082601:3891"]["eligible_id_count"] == 0
        assert result["five_owner_cases"]["reported_moped_vin"]["eligible"] == 0
        assert session.query(ProcessedLot).count() == 4
        assert session.query(SourceLot).count() == 4
