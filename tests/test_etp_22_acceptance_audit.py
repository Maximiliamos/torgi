from __future__ import annotations

import hashlib

from bankrotai.services.etp_22_acceptance_audit import evaluate_batch, evaluate_source


DIGEST = hashlib.sha256(b"official capture").hexdigest()


def _evidence():
    record = "m-ets.ru:12345-1"
    return {
        "source_id": "m-ets.ru",
        "total_pages": 1,
        "last_page_proven": True,
        "catalogue_pages": [{"number": 1, "snapshot_sha256": DIGEST, "lot_ids": [record]}],
        "detail_lots": [{
            "external_id": record, "official_url": "https://m-ets.ru/12345-1",
            "detail_sha256": DIGEST, "title": "Земельный участок",
            "start_price": "123000.00", "current_price": None,
            "auction_status": "active", "cadastral_number": "76:10:012345:123",
        }],
        "database_lots": [{
            "external_id": record, "source_system": "m-ets.ru",
            "title": "Земельный участок", "start_price": "123000.00",
            "current_price": None, "auction_status": "active",
            "cadastral_number": "76:10:012345:123",
        }],
        "map_lots": [{
            "external_id": record, "source_system": "m-ets.ru",
            "latitude": 57.63, "longitude": 39.89,
            "geo_evidence": "exact_cadastre",
        }],
        "operator_review": {
            "live_official_cards_checked": True,
            "reviewed_by": "independent_reviewer",
            "checked_at_utc": "2026-11-02T14:00:00Z",
        },
    }


def test_acceptance_requires_consistent_sources_details_db_and_geo():
    result = evaluate_source(_evidence())
    assert result["status"] == "accepted"
    assert result["catalogue_lots"] == 1


def test_missing_last_page_fails_closed():
    evidence = _evidence()
    evidence["total_pages"] = 2
    assert evaluate_source(evidence)["status"] == "rejected"


def test_wrong_db_price_and_missing_detail_fail():
    evidence = _evidence()
    evidence["database_lots"][0]["start_price"] = "987000.00"
    assert any("mismatch" in x for x in evaluate_source(evidence)["errors"])
    evidence["detail_lots"] = []
    assert evaluate_source(evidence)["status"] == "rejected"


def test_shared_procedure_id_cannot_replace_two_lot_ids():
    evidence = _evidence()
    evidence["catalogue_pages"][0]["lot_ids"] *= 2
    assert any("duplicate" in x for x in evaluate_source(evidence)["errors"])


def test_blocked_etp_remains_rejected_even_with_synthetic_evidence():
    evidence = _evidence()
    evidence["source_id"] = "rts-tender.ru"
    assert evaluate_source(evidence)["status"] == "rejected"


def test_invalid_coordinates_and_unsupported_geo_provenance_fail():
    evidence = _evidence()
    evidence["map_lots"][0]["longitude"] = 250.0
    evidence["map_lots"][0]["geo_evidence"] = "city_centroid"
    assert evaluate_source(evidence)["status"] == "rejected"


def test_complete_batch_lists_all_missing_sources():
    batch = evaluate_batch({"sources": [_evidence()]})
    assert batch["accepted"] == 1
    assert len(batch["missing_sources"]) == 21
    assert batch["writes_to_production"] is False


def test_operator_review_must_be_independent():
    evidence = _evidence()
    evidence.pop("operator_review")
    assert evaluate_source(evidence)["status"] == "rejected"
