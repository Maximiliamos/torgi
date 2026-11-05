"""No-network regression coverage for sampled independent NISTP/CDTRF details."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import bankrotai.services.etp_two_source_detail_qa as qa


def _detail(source: str, identity: str, *, price: float, status: str):
    return SimpleNamespace(
        external_id=identity, source_system=source,
        detail_level="detail" if source == "nistp.ru" else "public_detail",
        raw_data=(
            {"individual_lot_identity_verified": True}
            if source == "nistp.ru"
            else {"trade_id": "390364", "trade_lot_id": "390899", "property_right_kind": "ownership"}
        ),
        start_price=price, auction_status=status, category="land",
        cadastral_number="32:10:0010101:42", address=None, deposit_amount=None,
    )


def test_spread_pages_are_unique_cover_first_last_and_reject_unsafe_bounds() -> None:
    pages = qa.spaced_pages(142, 30)
    assert len(pages) == len(set(pages)) == 30
    assert pages[0] == 1
    assert pages[-1] == 142
    assert len(qa.planned_pages(64, 30)) >= 30
    with pytest.raises(ValueError):
        qa.spaced_pages(2, 3)
    with pytest.raises(ValueError):
        qa.planned_pages(64, 0)


def test_price_comparison_does_not_confuse_missing_or_subkopeck_values() -> None:
    assert qa._same_initial_price("100.00", 100.0)
    assert not qa._same_initial_price("100", 200)
    assert not qa._same_initial_price(None, 100)
    assert not qa._same_initial_price("NaN", 100)
    assert not qa._same_initial_price("100.001", 100)


def _nistp_observation(status: str = "Прием заявок") -> dict:
    return {
        "provider_external_id": "nistp.ru:501045:2",
        "is_property": True,
        "source_status_text": status,
        "listing_price_rub": 100.0,
    }


class NistpStub:
    def __init__(self, *, detail_status: str = "active", price: float = 100.0):
        self._last_response = SimpleNamespace(content=b"<html></html>")
        self.detail_status = detail_status
        self.price = price

    async def search(self, _filters, cursor=None):
        assert cursor == "1"
        return SimpleNamespace(items=[
            SimpleNamespace(external_id="nistp.ru:501045:2"),
        ])

    async def fetch_lot(self, identity):
        return _detail("nistp.ru", identity, price=self.price, status=self.detail_status)


def test_nistp_sample_verifies_detail_without_granting_source_acceptance(monkeypatch) -> None:
    monkeypatch.setattr(
        qa, "parse_nistp_lot_observations",
        lambda *args, **kwargs: [_nistp_observation()],
    )
    report = asyncio.run(qa.check_nistp_details(
        1, 1, connector_factory=NistpStub, sleep=lambda seconds: None,
    ))
    assert report["detail_sample_pass"] is True
    assert report["sample_verified"] == 1
    assert report["source_accepted"] is False
    assert report["archival_allowed"] is False
    assert report["production_database_writes"] == 0


def test_nistp_rejects_price_mismatch_and_does_not_mark_pass(monkeypatch) -> None:
    monkeypatch.setattr(
        qa, "parse_nistp_lot_observations",
        lambda *args, **kwargs: [_nistp_observation()],
    )
    report = asyncio.run(qa.check_nistp_details(
        1, 1, connector_factory=lambda: NistpStub(price=99.0),
        sleep=lambda seconds: None,
    ))
    assert report["sample_attempted"] == 1
    assert report["sample_verified"] == 0
    assert report["error_counts"] == {"listing_detail_initial_price_mismatch": 1}
    assert report["detail_sample_pass"] is False


def test_nistp_closed_row_is_not_selected_as_active(monkeypatch) -> None:
    monkeypatch.setattr(
        qa, "parse_nistp_lot_observations",
        lambda *args, **kwargs: [_nistp_observation("Торги завершены")],
    )
    report = asyncio.run(qa.check_nistp_details(
        1, 1, connector_factory=NistpStub, sleep=lambda seconds: None,
    ))
    assert report["sample_attempted"] == 0
    assert report["skipped_pages"] == 1
    assert report["detail_sample_pass"] is False


def _cdtrf_page(_session, page: int) -> dict:
    assert page == 1
    return {"property_candidates": [{
        "trade_id": "390364", "trade_lot_id": "390899",
        "external_id": "cdtrf:390899",
        "listing_price_rub": "100.00",  # actualPrice is not the initial price
    }]}



def test_cdtrf_sample_uses_operator_proven_land_category_41_and_four_statuses() -> None:
    urls = []

    class Session:
        def get(self, url, **kwargs):
            urls.append(url)
            assert kwargs["allow_redirects"] is False
            return SimpleNamespace(
                status_code=200,
                content=b'{"success":true,"totalCount":0,"items":[]}',
                headers={"Content-Type": "application/json"},
                json=lambda: {"success": True, "totalCount": 0, "items": []},
            )

    qa._cdtrf_page(Session(), 1)
    assert len(urls) == 1
    assert "Categories=21%2C22%2C23%2C31%2C32%2C33%2C34%2C41" in urls[0]
    assert "PageSize=100" in urls[0]
    assert "PageNum=1" in urls[0]
    for state in ("Declare", "RecieveReq", "DefinePart", "TradeGo"):
        assert f"{state}=true" in urls[0]


def test_cdtrf_detail_checks_trade_lot_and_initial_without_price_conflation() -> None:
    report = qa.check_cdtrf_details(
        1, 1, session=object(), sleep=lambda seconds: None,
        fetch_page=_cdtrf_page,
        fetch_detail=lambda trade, lot, session: _detail(
            "cdtrf.ru", f"cdtrf:{lot}", price=200.00, status="scheduled",
        ),
    )
    assert report["sample_verified"] == 1
    assert report["detail_sample_pass"] is True
    assert report["observations"][0]["catalogue_actual_price_not_assumed_initial"] is True
    assert report["source_accepted"] is False


def test_cdtrf_wrong_lot_fails_without_acceptance() -> None:
    report = qa.check_cdtrf_details(
        1, 1, session=object(), sleep=lambda seconds: None,
        fetch_page=_cdtrf_page,
        fetch_detail=lambda trade, lot, session: _detail(
            "cdtrf.ru", "cdtrf:999999", price=200.00, status="scheduled",
        ),
    )
    assert report["sample_verified"] == 0
    assert report["error_counts"] == {"detail_identity_unproven": 1}
    assert report["detail_sample_pass"] is False
    assert report["archival_allowed"] is False


def test_cdtrf_closed_detail_cannot_pass_selected_active_sample() -> None:
    report = qa.check_cdtrf_details(
        1, 1, session=object(), sleep=lambda seconds: None,
        fetch_page=_cdtrf_page,
        fetch_detail=lambda trade, lot, session: _detail(
            "cdtrf.ru", f"cdtrf:{lot}", price=200.00, status="closed",
        ),
    )
    assert report["error_counts"] == {"detail_status_out_of_selected_scope": 1}
    assert report["detail_sample_pass"] is False
