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
