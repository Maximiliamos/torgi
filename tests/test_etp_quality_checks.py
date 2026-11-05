"""Quality gates across all 22 direct/source checklist candidates (no network)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bankrotai.services.etp_quality_checks import (
    audit_catalogue_lots, audit_detail_pair,
)


def lot(source="etpu.ru", external_id=None, price=500000,
        status="active", deposit=None, raw=None, detail="search"):
    return SimpleNamespace(
        source_system=source,
        external_id=external_id or f"{source}:auctions-121",
        start_price=price, current_price=None,
        auction_status=status, deposit_amount=deposit,
        raw_data=raw if raw is not None else {},
        detail_level=detail,
    )


def test_full_catalogue_sample_not_just_first_detail_probe():
    first = lot(external_id="etpu.ru:auctions-1")
    second = lot(external_id="etpu.ru:auctions-2", price=None, status="unknown")
    audit = audit_catalogue_lots("etpu.ru", [first, second])
    assert audit["catalogue_price_coverage"]["state"] == "NOT_TESTED"
    assert audit["catalogue_price_coverage"]["count"] == 1
    assert audit["catalogue_status_coverage"]["state"] == "NOT_TESTED"
    assert audit["catalogue_status_coverage"]["count"] == 1
    assert audit["money_integrity"]["state"] == "PASS"


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -10, 0, 1.234])
def test_no_invalid_money_promoted_to_green(price):
    audit = audit_catalogue_lots("etpu.ru", [lot(price=price)])
    assert audit["money_integrity"]["state"] == "FAIL"
    assert audit["money_integrity"]["reason"] == "invalid_amount_range_or_precision"


def test_deposit_equal_to_initial_price_requires_independent_evidence():
    audit = audit_catalogue_lots("etpu.ru", [lot(price=100000, deposit=100000)])
    assert audit["money_integrity"]["state"] == "NOT_TESTED"
    assert audit["money_integrity"]["reason"] == "start_price_equals_deposit_needs_detail"


def test_trade_level_money_is_never_accepted_for_individual_property():
    item = lot(source="sistematorg.com",
               external_id="sistematorg.com:19431:2",
               price=900000, raw={
                   "trade_level_listing": True,
                   "lot_number": "2",
                   "individual_lot_identity_verified": False,
               })
    audit = audit_catalogue_lots("sistematorg.com", [item])
    assert audit["money_integrity"]["state"] == "FAIL"
    assert audit["lot_id_provenance"]["state"] == "NOT_TESTED"


@pytest.mark.parametrize("source", [
    "fabrikant.ru", "ausib.ru", "aukcioncenter.ru", "roseltorg.ru",
])
def test_procedure_level_url_is_not_green_individual_lot_identity(source):
    audit = audit_catalogue_lots(source, [lot(source=source)])
    assert audit["lot_id_provenance"]["state"] == "NOT_TESTED"
    assert audit["lot_id_provenance"]["reason"] == "procedure_level_identity_needs_per_lot_detail"


def test_multi_lot_source_requires_correct_trade_plus_lot_number():
    correct = lot(source="nistp.ru", external_id="nistp.ru:1234:3",
                  raw={"lot_number": "3"}, price=None)
    incorrect = lot(source="nistp.ru", external_id="nistp.ru:1234:1",
                    raw={"lot_number": "3"}, price=None)
    assert audit_catalogue_lots("nistp.ru", [correct])["lot_id_provenance"]["state"] == "PASS"
    assert audit_catalogue_lots("nistp.ru", [incorrect])["lot_id_provenance"]["state"] == "FAIL"


def test_existing_lot_online_identity_does_not_require_generic_source_prefix():
    item = lot(source="lot-online.ru", external_id="LOT-100")
    assert audit_catalogue_lots("lot-online.ru", [item])["lot_id_provenance"]["state"] == "PASS"


def test_detail_price_mismatch_is_detected_without_logging_private_values():
    listing = lot(price=1_000_000)
    detail = lot(price=100_000, detail="detail")
    result = audit_detail_pair("etpu.ru", listing, detail)
    assert result["detail_price_consistency"]["state"] == "FAIL"
    assert result["detail_price_consistency"]["reason"] == "start_price_conflicts_with_individual_detail"
    assert "100000" not in str(result)


def test_detail_source_identity_mismatch_is_rejected():
    listing = lot(external_id="etpu.ru:auctions-121")
    detail = lot(external_id="etpu.ru:auctions-999", detail="detail")
    result = audit_detail_pair("etpu.ru", listing, detail)
    assert result["detail_price_consistency"]["state"] == "FAIL"
    assert result["detail_status_consistency"]["state"] == "FAIL"


def test_status_changed_from_listing_requires_recency_not_false_fail():
    listing = lot(status="scheduled")
    detail = lot(status="active", detail="detail")
    result = audit_detail_pair("etpu.ru", listing, detail)
    assert result["detail_status_consistency"]["state"] == "NOT_TESTED"
    assert result["detail_price_consistency"]["state"] == "PASS"


def test_procedure_details_cannot_verify_multi_lot_money_by_string_identity():
    listing = lot(source="fabrikant.ru")
    detail = lot(source="fabrikant.ru", detail="detail")
    result = audit_detail_pair("fabrikant.ru", listing, detail)
    assert result["detail_price_consistency"]["state"] == "NOT_TESTED"
    assert result["detail_status_consistency"]["state"] == "NOT_TESTED"


def test_current_price_drift_requires_recency_not_false_match():
    listing = lot()
    detail = lot(detail="detail")
    listing.current_price = 900000
    detail.current_price = 850000
    evidence = audit_detail_pair("etpu.ru", listing, detail)
    assert evidence["detail_price_consistency"]["state"] == "NOT_TESTED"
    assert evidence["detail_price_consistency"]["reason"] == "current_price_changed_needs_recency"


def test_detail_must_not_reuse_unverified_current_price_from_listing():
    listing = lot()
    detail = lot(detail="detail")
    listing.current_price = 900000
    detail.current_price = None
    evidence = audit_detail_pair("etpu.ru", listing, detail)
    assert evidence["detail_price_consistency"]["state"] == "NOT_TESTED"


def test_invalid_individual_current_price_fails_closed():
    listing = lot()
    detail = lot(detail="detail")
    detail.current_price = float("inf")
    evidence = audit_detail_pair("etpu.ru", listing, detail)
    assert evidence["detail_price_consistency"]["state"] == "FAIL"
    assert evidence["detail_price_consistency"]["reason"] == "invalid_catalogue_or_detail_money"


def test_every_22_source_profile_has_a_quality_audit_path():
    from bankrotai.connectors.etp_sources import PROFILES, SOURCE_CONNECTOR_TYPES
    from bankrotai.services.etp_parser_checklist import source_ids

    all_ids = source_ids()
    direct_ids = {p.source_id for p in PROFILES}
    assert len(all_ids) == 22
    assert len(direct_ids) == 21
    assert set(all_ids) == direct_ids | {"lot-online.ru"}
    assert set(SOURCE_CONNECTOR_TYPES) == direct_ids
    for p in PROFILES:
        assert p.listing_url.startswith("https://")
        if p.blocked_reason:
            continue
        assert p.allowed_hosts
        fake = lot(source=p.source_id, external_id=f"{p.source_id}:sample")
        quality = audit_catalogue_lots(p.source_id, [fake])
        assert set(quality) == {
            "lot_id_provenance", "money_integrity",
            "catalogue_price_coverage", "catalogue_status_coverage",
        }
        # Most procedures need separate detail before true per-lot acceptance.
        assert quality["lot_id_provenance"]["state"] in {"PASS", "NOT_TESTED", "FAIL"}
