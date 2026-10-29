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


def test_readiness_is_not_conflated_with_production_import(monkeypatch):
    from unittest.mock import Mock
    from bankrotai.connectors.registry.direct_etp_catalogue import PROFILES
    from bankrotai.services import etp_source_inventory as survey

    observations = {
        "m-ets.ru": "existing_connector",
        "cdtrf.ru": "candidates_unverified",
        "roseltorg.ru": "needs_public_catalogue_contract",
    }
    def fake_probe(**kwargs):
        return {"sources": [
            {"source": item.source_id,
             "status": observations.get(item.source_id, "blocked_or_parser_error"),
             "candidates": [{"title": "PRIVATE DETAILS"}]
             if item.source_id == "cdtrf.ru" else []}
            for item in PROFILES
        ]}
    monkeypatch.setattr(survey, "batch_probe", fake_probe)
    report = survey.run_inventory(session=Mock())
    stages = {x["source_id"]: x for x in report["sources"]}
    assert stages["cdtrf.ru"]["readiness_stage"] == "S1"
    assert stages["roseltorg.ru"]["readiness_stage"] == "S0"
    assert stages["m-ets.ru"]["readiness_stage"] == "not_assessed"
    assert stages["cdtrf.ru"]["next_required_action"] == "verify_detail_identity_cadastral_price_and_status"
    assert all(x["production_ingestion_enabled_by_inventory"] is False for x in report["sources"])
    assert "PRIVATE DETAILS" not in str(report)
    assert sum(report["source_status_counts"].values()) == 22
