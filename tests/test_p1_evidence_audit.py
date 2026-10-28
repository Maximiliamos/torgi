"""Read-only GEO and source snapshot regression tests."""
from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from bankrotai.db import Base, ProcessedLot

PATH = Path(__file__).resolve().parents[1] / "scripts/audit_p1_source_geo_evidence.py"
SPEC = importlib.util.spec_from_file_location("p1_evidence_audit", PATH)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def test_geo_rejection_matches_map_builder_region_check() -> None:
    assert AUDIT.region_rejection_reason(57.6261, 39.8845, "76", "76:23:010101:1") is None
    assert AUDIT.region_rejection_reason(57.6261, 39.8845, "01", "01:01:010101:1")


def test_readonly_snapshot_does_not_change_auction_data() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = ProcessedLot(
            external_id="geo-reject",
            source_system="test",
            source="test",
            title="Sample",
            description="",
            category="land",
            region_code="01",
            cadastral_number="01:01:010101:1",
            start_price=Decimal("10000"),
            auction_status="active",
            current_geo_lat=57.6261,
            current_geo_lon=39.8845,
        )
        session.add(lot)
        session.commit()
        before = lot.id
        report = AUDIT.collect_evidence(session)
        assert report["read_only"] is True
        assert report["geo_rejections_total"] == 1
        assert report["geo_rejections_sample"][0]["lot_id"] == before
        assert report["geo_rejections_sample"][0]["reason"]
        assert report["source_health"][0]["source"] == "test"
        assert report["data_quality"]["total_lots"] == 1
        assert session.get(ProcessedLot, before).region_code == "01"
