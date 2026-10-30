"""P13 region repair evidence is bounded, direct-source only and read-only."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, ProcessedLot, SourceLot
from bankrotai.services.historical_region_review import (
    build_report, classify_region_evidence, direct_source_blocker,
    strict_cadastral_region,
)


@pytest.mark.parametrize("number, expected", [
    ("76:23:060521:48", "76"),
    ("77:01:000101:2", "77"),
    ("76:23:060521:48, 77:01:000101:2", None),
    ("76:23:060521", None),
    ("001:23:060521:48", None),
    ("00:23:060521:48", None),
    (None, None),
])
def test_cadastral_region_requires_one_structurally_valid_number(number, expected):
    assert strict_cadastral_region(number) == expected


def test_manual_evidence_never_coerces_conflicting_or_missing_source_regions():
    assert classify_region_evidence("76:23:060521:48", ["76"]) == (
        "consistent_requires_manual_approval", "76"
    )
    assert classify_region_evidence("76:23:060521:48", ["77"]) == (
        "source_cadastral_region_conflict", None
    )
    assert classify_region_evidence("76:23:060521:48", ["076"]) == (
        "unsupported_source_region_code", None
    )
    assert classify_region_evidence("76:23:060521:48", [None]) == (
        "no_direct_official_region_evidence", None
    )
    assert classify_region_evidence(
        "76:23:060521:48,77:01:000101:2", ["76"]
    ) == ("invalid_or_multiple_cadastres", None)


def test_historical_region_review_never_writes_and_ignores_stale_source():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            def add_lot(external_id, cadastre, source_region=None, fresh=True):
                row = ProcessedLot(
                    source="torgi.gov.ru", source_system="torgi.gov.ru",
                    external_id=external_id, title="Продажа земельного участка",
                    description="", category="land",
                    auction_status="active", region_code=None,
                    cadastral_number=cadastre, vin=None, is_archived=False,
                    current_geo_lat=57.6, current_geo_lon=39.8,
                    current_geo_source="nspd", needs_geo_check=False,
                )
                session.add(row)
                session.flush()
                canonical = CanonicalLot(
                    canonical_key=f"region-p13-{external_id}",
                    legacy_processed_lot_id=row.id,
                    title=row.title, category=row.category,
                )
                session.add(canonical)
                session.flush()
                if source_region is not None:
                    session.add(SourceLot(
                        canonical_lot_id=canonical.id, processed_lot_id=row.id,
                        source_system="torgi.gov.ru", external_id=external_id,
                        source_status="active", region_code=source_region,
                        is_active=True, is_archived=False,
                        last_seen_at=(
                            datetime.now(timezone.utc).replace(tzinfo=None)
                            if fresh else datetime(2020, 1, 1)
                        ),
                    ))
                return row.id

            good = add_lot("region-1", "76:23:060521:48", "76")
            conflict = add_lot("region-2", "76:23:060521:49", "77")
            stale = add_lot("region-3", "76:23:060521:50", "76", fresh=False)
            session.commit()
            report = build_report(session, limit=10)
            assert report["read_only"] and report["no_mutations"]
            assert report["sample_exhaustive"] is False
            assert report["same_as_full_p13_eligibility_population"] is False
            assert report["historical_region_backfill_completed"] is False
            assert report["restore_verified_backup_required_before_write"] is True
            assert report["sample_size"] == 3
            assert report["classification_counts_in_sample"] == {
                "consistent_requires_manual_approval": 1,
                "no_direct_official_region_evidence": 1,
                "source_cadastral_region_conflict": 1,
            }
            assert report["lineage_blocker_counts_in_sample"] == {
                "direct_official_region_proof_present": 2,
                "direct_official_source_stale": 1,
            }
            reviews = {x["processed_lot_id"]: x for x in report["bounded_review_items"]}
            assert reviews[good]["review_region_hint"] == "76"
            assert reviews[conflict]["review_region_hint"] is None
            assert reviews[stale]["review_region_hint"] is None
            assert reviews[stale]["lineage_blocker"] == "direct_official_source_stale"
            assert all(x["operation"] == "manual_review_only" for x in reviews.values())
            assert "cadastral_number" not in str(report)
            assert "address" not in str(report)
            assert session.query(ProcessedLot).count() == 3
            assert all(session.get(ProcessedLot, x).region_code is None
                       for x in (good, conflict, stale))
    finally:
        engine.dispose()


@pytest.mark.parametrize("bad_limit", [0, 101, True, -1])
def test_region_report_refuses_unbounded_or_boolean_limit(bad_limit):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with Session(engine) as session:
            with pytest.raises(ValueError, match="1..100"):
                build_report(session, limit=bad_limit)
    finally:
        engine.dispose()


def test_direct_source_blockers_distinguish_missing_link_stale_and_status():
    from datetime import timedelta
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    good = ("torgi.gov.ru", "76", True, False, "active", now)
    assert direct_source_blocker([], observed_at=now) == "no_direct_source_lot"
    assert direct_source_blocker(
        [("unknown-etp.example", "76", True, False, "active", now)],
        observed_at=now,
    ) == "direct_source_unapproved"
    assert direct_source_blocker(
        [("torgi.gov.ru", "76", False, False, "active", now)],
        observed_at=now,
    ) == "direct_official_source_inactive_or_archived"
    assert direct_source_blocker(
        [("torgi.gov.ru", "76", True, False, "active", now - timedelta(days=10))],
        observed_at=now,
    ) == "direct_official_source_stale"
    assert direct_source_blocker(
        [("torgi.gov.ru", "76", True, False, "unknown", now)],
        observed_at=now,
    ) == "direct_official_status_unverified"
    assert direct_source_blocker(
        [("torgi.gov.ru", None, True, False, "active", now)],
        observed_at=now,
    ) == "direct_official_region_missing"
    assert direct_source_blocker([good], observed_at=now) == (
        "direct_official_region_proof_present"
    )
