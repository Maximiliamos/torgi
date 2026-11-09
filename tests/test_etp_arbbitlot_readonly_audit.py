from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bankrotai.services.etp_arbbitlot_readonly_audit import run_audit


class FakeConnector:
    def __init__(self) -> None:
        self.calls: list[str | None] = []

    @staticmethod
    def listing(external_id: str, procedure_id: str, lot_number: str, current_price=None):
        return SimpleNamespace(
            external_id=external_id,
            start_price=100.0,
            current_price=current_price,
            auction_status="active",
            raw_data={"procedure_id": procedure_id, "lot_number": lot_number},
        )

    async def search(self, _filters, cursor=None):
        self.calls.append(cursor)
        if cursor is None:
            return SimpleNamespace(
                items=[
                    self.listing("arbbitlot.ru:auctions-1", "777", "1"),
                    self.listing("arbbitlot.ru:public-offers-2", "778", "1", 90.0),
                ],
                next_cursor="2",
            )
        return SimpleNamespace(
            items=[self.listing("arbbitlot.ru:auctions-3", "777", "2")],
            next_cursor=None,
        )

    async def fetch_lot(self, external_id):
        is_public_offer = ":public-offers-" in external_id
        return SimpleNamespace(
            external_id=external_id,
            start_price=100.0,
            current_price=90.0 if is_public_offer else None,
            auction_status="active",
            procedure_number="777" if external_id.endswith(("1", "3")) else "778",
            cadastral_number="76:01:000000:1",
            area=20.0,
            address="Test",
            raw_data={
                "procedure_id": "777" if external_id.endswith(("1", "3")) else "778",
                "lot_number": "2" if external_id.endswith("3") else "1",
            },
        )


def test_arbbitlot_readonly_audit_proves_terminal_and_repeatable_ids() -> None:
    connectors: list[FakeConnector] = []

    def factory():
        connector = FakeConnector()
        connectors.append(connector)
        return connector

    report = asyncio.run(run_audit(factory, detail_limit=3, max_pages=10))

    assert report["read_only"] is True
    assert report["production_enabled"] is False
    assert report["pass_1"]["page_count"] == 2
    assert report["pass_1"]["terminal_page_proven"] is True
    assert report["pass_1"]["unique_ids"] == 3
    assert report["pass_1"]["duplicate_ids"] == []
    assert report["passes_have_identical_ids"] is True
    assert report["details"]["details_checked"] == 3
    assert report["details"]["detail_success"] == 3
    assert report["details"]["start_price_success"] == 3
    assert report["details"]["catalogue_detail_procedure_match"] == 3
    assert report["details"]["catalogue_detail_lot_number_match"] == 3
    assert report["details"]["catalogue_detail_start_price_match"] == 3
    assert report["details"]["status_success"] == 3
    assert report["details"]["catalogue_detail_status_match"] == 3
    assert report["details"]["public_offer_details_checked"] == 1
    assert report["details"]["catalogue_current_price_success"] == 1
    assert report["details"]["detail_current_price_success"] == 1
    assert report["details"]["catalogue_detail_current_price_match"] == 1
    assert report["details"]["multi_lot_procedures"] == {
        "777": ["arbbitlot.ru:auctions-1", "arbbitlot.ru:auctions-3"]
    }
    assert connectors[0].calls == [None, "2"]
    assert connectors[1].calls == [None, "2"]
