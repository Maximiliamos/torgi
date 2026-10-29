"""DB/geo/journal ETP acceptance report without personal details or writes."""
import pytest

from bankrotai.services.etp_postimport_audit import inspect_import, report


class FakeSession:
    def __init__(self, counts, current=None):
        self._counts = iter(counts)
        self._current = current
    def scalar(self, stmt):
        try:
            return next(self._counts)
        except StopIteration:
            return self._current


def test_one_source_acceptance_counts_are_aggregated():
    s = FakeSession([2, 2, 0, 1, 1, 2, 1, 0, 1])
    result = inspect_import(s, "m-ets.ru")
    assert result["source_lots"] == 2
    assert result["processed_lots"] == 2
    assert result["linked_source_lots"] == 2
    assert result["with_coordinates"] == 1
    assert result["cross_source_duplicates"] == 1
    assert result["geo_snapshots"] == 1
    assert result["status_history_events"] == 1
    assert result["price_events"] == 0
    assert result["verified_new_lot_map_publication"] is False
    assert not any("title" in key or "address" in key for key in result)


def test_unknown_source_rejected_before_access():
    with pytest.raises(ValueError):
        inspect_import(FakeSession([]), "rts-tender.ru")


def test_report_requires_external_map_verification():
    from types import SimpleNamespace
    result = report(FakeSession([SimpleNamespace(version="m1", published_at=None)] + [0]*18))
    assert result["database_writes"] == 0
    assert result["requires_external_map_tile_verification"] is True
    assert result["current_map_dataset_published"] is False
    assert len(result["sources"]) == 2


def test_exact_imported_source_ids_require_correct_active_link_in_database():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from bankrotai.db import Base, CanonicalLot, ProcessedLot, SourceLot
    from bankrotai.services.etp_postimport_audit import verify_expected_source_lots

    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            canonical = CanonicalLot(canonical_key="source:m-ets.ru:243003-1", title="Lot")
            processed = ProcessedLot(
                external_id="243003-1", source="m-ets.ru", source_system="m-ets.ru",
                title="Lot", category="land", auction_status="active",
                cadastral_number="76:01:000100:1",
            )
            session.add_all([canonical, processed])
            session.flush()
            source = SourceLot(
                canonical_lot_id=canonical.id, processed_lot_id=processed.id,
                source_system="m-ets.ru", external_id="243003-1",
                cadastral_number="76:01:000100:1",
            )
            session.add(source)
            session.flush()
            verified = verify_expected_source_lots(session, "m-ets.ru", ["243003-1"])
            assert verified == [{
                "external_id": "243003-1", "source_link_verified": True,
                "geo_observed": False, "public_map_verified": False,
            }]
            with pytest.raises(RuntimeError, match="active"):
                verify_expected_source_lots(session, "m-ets.ru", ["243003-2"])
            processed.auction_status = "closed"
            session.flush()
            with pytest.raises(RuntimeError, match="active"):
                verify_expected_source_lots(session, "m-ets.ru", ["243003-1"])
            processed.auction_status = "active"
            source.cadastral_number = "76:01:000100:2"
            session.flush()
            with pytest.raises(RuntimeError, match="active"):
                verify_expected_source_lots(session, "m-ets.ru", ["243003-1"])
    finally:
        engine.dispose()


@pytest.mark.parametrize("ids", [
    [], ["id-1", "id-2", "id-3"], ["id-1", "id-1"],
    ["; rm -rf /"], ["lot number"], ["bad\nvalue"],
])
def test_exact_source_audit_rejects_invalid_ids_before_any_query(ids):
    from bankrotai.services.etp_postimport_audit import verify_expected_source_lots
    with pytest.raises(ValueError, match="unique public source IDs"):
        verify_expected_source_lots(FakeSession([]), "m-ets.ru", ids)


def test_etp_workflow_requires_actual_new_ids_not_aggregate_only():
    from pathlib import Path
    workflow = (Path(__file__).resolve().parents[1] /
                ".github/workflows/etp-manual-verified-import.yml").read_text(
                    encoding="utf-8")
    assert "etp-canary-result.json" in workflow
    assert "--expected-external-id" in workflow
    assert "$canary.persisted_count -ne $ids.Count" in workflow
    assert "'--expected-external-id',[string]$id" in workflow
