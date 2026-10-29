"""Never present a first-page ETP hit as verified, safe-to-publish data."""
from unittest.mock import Mock

from bankrotai.connectors.registry.direct_etp_catalogue import PROFILES
from bankrotai.services import etp_source_inventory as module


def test_sanitized_inventory_covers_all_22_profiles_without_pii(monkeypatch):
    def fake_probe(*, session, ids, limit, delay_seconds):
        assert ids is None and limit == 5 and delay_seconds == 1.0
        return {
            "sources": [
                {"source": p.source_id,
                 "status": "candidates_unverified",
                 "candidates": [{"title": "PERSONAL DATA", "debtor": "Name", "url": "secret"}],
                 "catalogue_url": "https://some-provider.example/"}
                for p in PROFILES
            ]
        }
    monkeypatch.setattr(module, "batch_probe", fake_probe)
    report = module.run_inventory(session=Mock())
    assert report["registered_profile_count"] == 22
    assert report["probed_source_count"] == 22
    assert report["production_database_writes"] == 0
    assert report["complete_source_inventory"] is False
    assert report["archival_allowed"] is False
    assert report["map_publication"] is False
    assert all(s["candidate_count"] == 1 and not s["detail_verified"]
               and not s["pagination_verified"] for s in report["sources"])
    assert "PERSONAL DATA" not in str(report)
    assert "debtor" not in str(report)
    assert "secret" not in str(report)


def test_report_preserves_blocked_source_without_inventing_records(monkeypatch):
    def fake_probe(**kwargs):
        return {"sources": [
            {"source": "rts-tender.ru", "status": "blocked_or_parser_error",
             "error_type": "EtpCatalogError", "candidates": []}
        ]}
    monkeypatch.setattr(module, "batch_probe", fake_probe)
    report = module.run_inventory(session=Mock(), source_ids=("rts-tender.ru",))
    assert report["probed_source_count"] == 1
    item = report["sources"][0]
    assert item["status"] == "blocked_or_parser_error"
    assert item["error_type"] == "EtpCatalogError"
    assert item["candidate_count"] == 0
    assert not item["production_ingestion_enabled_by_inventory"]
