"""P13 pre-production read-only impact must not mutate or miscount sample lots."""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, LotSyncRun, LotSyncSourceRun, MapDataset, ProcessedLot, SourceLot


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
        assert result["sequential_exclusion_ladder"] == {
            "01_geo_nonarchived_primary": 4,
            "02_and_active_status": 3,
            "03_and_real_estate_no_vin": 2,
            "04_and_sale_not_rental_transport": 1,
            "05_and_strict_cadastral_geo": 1,
            "06_and_fresh_active_source": 1,
        }
        assert sum(result["exclusion_counts_by_stage"].values()) == 3
        reasons = result["root_cause_diagnostics"]
        assert reasons["status_distribution_before_filters"] == {
            "active": 3, "closed": 1,
        }
        assert reasons["geo_before_stage_by_source"] == {"nspd": 1}
        assert reasons["geo_needs_check_or_unknown"] == 0
        assert reasons["geo_cadastral_untrusted_source"] == 0
        assert reasons["before_fresh_source_by_primary_system"] == {
            "torgi.gov.ru": 1,
        }
        assert "not the canonical SourceLot" in reasons["source_label_semantics"]
        assert result["upper_bound_ratio_to_current_dataset"] == 0.01
        assert result["preview_fails_existing_coverage_guard"] is True
        assert result["existing_required_min_coverage_ratio"] >= 0.01
        assert result["five_owner_cases"]["76:22:010717:536"]["eligible_id_count"] == 0
        assert result["five_owner_cases"]["76:09:082601:3891"]["eligible_id_count"] == 0
        assert result["five_owner_cases"]["reported_moped_vin"]["eligible"] == 0
        assert session.query(ProcessedLot).count() == 4
        assert session.query(SourceLot).count() == 4
        assert reasons["source_has_active_proof_ignoring_freshness"] == 1
        assert reasons["source_no_active_canonical_proof"] == 0
        assert reasons["source_only_freshness_expired"] == 0

        # Mutate only the disposable test fixture; audit remains SELECT-only.
        source = session.query(SourceLot).filter_by(external_id="sale").one()
        source.last_seen_at = datetime(2020, 1, 1)
        session.flush()
        expired = impact.build_report(session)
        stale_reasons = expired["root_cause_diagnostics"]
        assert expired["proposed_maximum_eligible_points"] == 0
        assert stale_reasons["source_no_active_canonical_proof"] == 0
        assert stale_reasons["source_only_freshness_expired"] == 1
        session.rollback()


def test_p14_expired_primary_with_fresh_canonical_sibling_is_reported_without_mutation() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        row = ProcessedLot(
            external_id="expired-with-active-canonical-proof",
            source="test", source_system="tbankrot.ru",
            title="Продажа земельного участка", description="",
            category="land", auction_status="expired",
            is_archived=False, current_geo_lat=57.6, current_geo_lon=39.8,
            current_geo_source="nspd", needs_geo_check=False,
        )
        session.add(row)
        session.flush()
        canonical = CanonicalLot(
            canonical_key="test-p14-expired-active-proof",
            legacy_processed_lot_id=row.id, title=row.title, category=row.category,
        )
        session.add(canonical)
        session.flush()
        session.add(SourceLot(
            canonical_lot_id=canonical.id, processed_lot_id=row.id,
            source_system="torgi.gov.ru", external_id="verified-current-sale",
            source_status="active", is_active=True, is_archived=False,
            last_seen_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ))
        session.commit()
        result = impact.build_report(session)
        status = result["root_cause_diagnostics"]["status_provenance"]
        assert result["root_cause_diagnostics"]["status_distribution_before_filters"] == {
            "expired": 1
        }
        assert status["expired_primary_by_source"] == {"tbankrot.ru": 1}
        assert status["expired_primary_direct_link_statuses"] == {"active": 1}
        assert status["expired_primary_with_no_direct_source_link"] == 0
        assert status["expired_primary_with_active_canonical_proof_any_age"] == 1
        assert status["expired_primary_with_fresh_active_canonical_proof"] == 1
        assert status["expired_with_active_unarchived_direct_source"] == 1
        assert status["expired_with_fresh_active_unarchived_direct_source"] == 1
        assert status["expired_direct_source_lifecycle"] == [{
            "source_status": "active", "is_active": True,
            "is_archived": False, "archive_reason": "none", "count": 1,
        }]
        assert session.query(ProcessedLot).one().auction_status == "expired"
        assert session.query(SourceLot).one().is_active is True



def test_p14_archive_history_uses_completed_run_evidence_not_active_text() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        archived_day = datetime(2026, 10, 8, 9, 30)
        primary = ProcessedLot(
            external_id="historical-1", source="test", source_system="bidexpert.ru",
            title="Земельный участок", description="", category="land",
            auction_status="expired", is_archived=False,
            current_geo_lat=57.6, current_geo_lon=39.8,
            current_geo_source="nspd", needs_geo_check=False,
        )
        session.add(primary)
        session.flush()
        canonical = CanonicalLot(
            canonical_key="p14-history-1", legacy_processed_lot_id=primary.id,
            title=primary.title, category="land",
        )
        session.add(canonical)
        session.flush()
        session.add(SourceLot(
            canonical_lot_id=canonical.id, processed_lot_id=primary.id,
            source_system="bidexpert.ru", external_id="bid-1",
            source_status="active", is_active=False, is_archived=True,
            archived_at=archived_day,
            archive_reason="missing_after_two_complete_syncs",
        ))
        session.add(LotSyncRun(
            id="p14-complete-1", status="success",
            trigger_type="manual", total_sources=1,
        ))
        session.flush()
        session.add(LotSyncSourceRun(
            sync_run_id="p14-complete-1", source_system="bidexpert.ru",
            status="success", complete_source_run=True, items_seen=9,
            pages_scanned=2, items_archived=1, finished_at=archived_day,
        ))
        session.commit()
        before = session.query(ProcessedLot).one().auction_status
        report = impact.build_report(session)
        evidence = report["root_cause_diagnostics"]["status_provenance"]
        assert evidence["expired_direct_source_lifecycle"] == [{
            "source_status": "active", "is_active": False,
            "is_archived": True,
            "archive_reason": "missing_after_two_complete_syncs", "count": 1,
        }]
        assert evidence["largest_archive_date_cohorts"] == [{
            "source_system": "bidexpert.ru", "archive_date": "2026-10-08", "count": 1,
        }]
        assert evidence["recent_source_sync_run_evidence"]["bidexpert.ru"] == [{
            "finished_at_utc": "2026-10-08T09:30:00Z",
            "status": "success", "complete_source_run": True,
            "pages_scanned": 2, "items_seen": 9,
            "items_archived": 1, "coverage_guard_rejected": False,
        }]
        assert session.query(ProcessedLot).one().auction_status == before == "expired"


def test_p13_direct_source_proof_works_without_legacy_canonical_pointer() -> None:
    """P0's new sale identity can have no historical legacy primary pointer."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        row = ProcessedLot(
            external_id="new-sale-001", source_system="torgi.gov.ru",
            source="torgi.gov.ru", title="Продажа земельного участка",
            description="", category="land", auction_status="active",
            is_archived=False, current_geo_lat=57.6,
            current_geo_lon=39.8, needs_geo_check=False,
        )
        other = ProcessedLot(
            external_id="unproven-002", source_system="torgi.gov.ru",
            source="torgi.gov.ru", title="Продажа другого участка",
            description="", category="land", auction_status="active",
            is_archived=False, current_geo_lat=57.6,
            current_geo_lon=39.8, needs_geo_check=False,
        )
        session.add_all([row, other])
        session.flush()
        canonical = CanonicalLot(
            canonical_key="source:torgi.gov.ru:new-sale-001",
            title=row.title, category=row.category,
            legacy_processed_lot_id=None,
        )
        session.add(canonical)
        session.flush()
        source = SourceLot(
            canonical_lot_id=canonical.id, processed_lot_id=row.id,
            source_system="torgi.gov.ru", external_id=row.external_id,
            source_status="active", is_active=True, is_archived=False,
            last_seen_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(source)
        session.commit()
        report = impact.build_report(session)
        assert report["proposed_maximum_eligible_points"] == 1
        assert report["sequential_exclusion_ladder"][
            "06_and_fresh_active_source"
        ] == 1
        diagnostics = report["root_cause_diagnostics"]
        assert diagnostics["source_direct_link_proof_not_in_legacy"] == 1
        assert diagnostics["source_has_active_proof_ignoring_freshness"] == 1
        assert report["read_only"] and report["no_mutations"]
        assert session.query(ProcessedLot).count() == 2
        assert other.auction_status == "active"

        # A stale source does not establish current availability.
        source.last_seen_at = datetime(2020, 1, 1)
        session.flush()
        stale = impact.build_report(session)
        assert stale["proposed_maximum_eligible_points"] == 0
        assert stale["root_cause_diagnostics"][
            "source_direct_link_proof_not_in_legacy"
        ] == 1
        session.rollback()


def test_p13_direct_source_proof_rejects_archived_or_rental_source() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        row = ProcessedLot(
            external_id="real-property-003", source_system="torgi.gov.ru",
            source="torgi.gov.ru", title="Земельный участок",
            description="", category="land", auction_status="active",
            is_archived=False, current_geo_lat=57.6,
            current_geo_lon=39.8, needs_geo_check=False,
        )
        session.add(row)
        session.flush()
        canonical = CanonicalLot(
            canonical_key="source:torgi.gov.ru:real-property-003",
            title=row.title, category="land", legacy_processed_lot_id=None,
        )
        session.add(canonical)
        session.flush()
        source = SourceLot(
            canonical_lot_id=canonical.id, processed_lot_id=row.id,
            source_system="torgi.gov.ru", external_id=row.external_id,
            source_status="active", is_active=True, is_archived=True,
            last_seen_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(source)
        session.flush()
        assert impact.build_report(session)["proposed_maximum_eligible_points"] == 0
        source.is_archived = False
        source.auction_type = "аренда"
        session.flush()
        assert impact.build_report(session)["proposed_maximum_eligible_points"] == 0
        session.rollback()


def test_p13_source_proof_uses_separate_indexable_identity_paths() -> None:
    """Avoid the OR-inside-JOIN that timed out on Home PostgreSQL."""
    from sqlalchemy.dialects import postgresql

    direct, legacy = impact.source_sale_proofs(
        cutoff=datetime(2026, 10, 29, 0, 0)
    )
    direct_sql = str(direct.compile(dialect=postgresql.dialect()))
    legacy_sql = str(legacy.compile(dialect=postgresql.dialect()))
    assert "source_lots.processed_lot_id = processed_lots.id" in direct_sql
    assert "JOIN canonical_lots" not in direct_sql
    assert "canonical_lots.legacy_processed_lot_id = processed_lots.id" in legacy_sql
    assert "JOIN canonical_lots" in legacy_sql
    assert "source_lots.processed_lot_id = processed_lots.id OR" not in legacy_sql
    assert "source_lots.last_seen_at >=" in direct_sql
    assert "source_lots.last_seen_at >=" in legacy_sql


def test_p13_legacy_only_source_proof_remains_eligible() -> None:
    """Splitting source predicates must not drop legitimate legacy-linked sales."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        primary = ProcessedLot(
            external_id="legacy-linked-sale", source="torgi.gov.ru",
            source_system="torgi.gov.ru", title="Земельный участок",
            description="", category="land", auction_status="active",
            is_archived=False, current_geo_lat=57.6, current_geo_lon=39.8,
            current_geo_source="nspd", needs_geo_check=False,
        )
        session.add(primary)
        session.flush()
        canonical = CanonicalLot(
            canonical_key="legacy-only-p13-proof",
            legacy_processed_lot_id=primary.id,
            title=primary.title, category="land",
        )
        session.add(canonical)
        session.flush()
        session.add(SourceLot(
            canonical_lot_id=canonical.id,
            processed_lot_id=None,
            source_system="torgi.gov.ru", external_id="legacy-proof",
            source_status="active", is_active=True, is_archived=False,
            last_seen_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ))
        session.commit()
        result = impact.build_report(session)
        assert result["proposed_maximum_eligible_points"] == 1
        assert result["root_cause_diagnostics"]["source_direct_link_proof_not_in_legacy"] == 0
        assert session.query(ProcessedLot).count() == 1
