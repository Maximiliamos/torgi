"""Normalized ETP evidence checks never promote candidates into accepted sources."""
from __future__ import annotations

from types import SimpleNamespace

from bankrotai.services.etp_contract_evidence import (
    lot_contract_findings, summarize_lot_contract,
)


def lot(**overrides):
    data = {
        "source_system": "m-ets.ru",
        "external_id": "m-ets.ru:123-1",
        "lot_url": "https://m-ets.ru/123-1",
        "title": "Земельный участок",
        "start_price": 900000.0,
        "current_price": None,
        "auction_status": "active",
        "raw_data": {"safe_for_archival": False, "reconcile_missing": False},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_verified_candidate_has_no_basic_field_findings():
    assert lot_contract_findings(lot()) == ()


def test_missing_price_and_unknown_status_remain_visible_not_invented():
    result = summarize_lot_contract([
        lot(start_price=None, auction_status="unknown"),
    ])
    assert result["lots_checked"] == 1
    assert result["field_findings"]["missing_price"] == 1
    assert result["field_findings"]["unverified_status"] == 1
    assert result["source_accepted"] is False
    assert result["production_database_writes"] == 0


def test_untrusted_url_and_unsafe_archival_claim_are_reported():
    issues = lot_contract_findings(lot(
        lot_url="https://m-ets.ru:bad/123-1",
        raw_data={"safe_for_archival": True, "reconcile_missing": True},
    ))
    assert "unverified_lot_url" in issues
    assert "unsafe_new_etp_archival_claim" in issues


def test_nonfinite_and_negative_prices_are_not_accepted():
    for value in (-100, float("nan"), float("inf")):
        assert "invalid_start_price" in lot_contract_findings(lot(start_price=value))
