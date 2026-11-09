from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from bankrotai.services.etp_ruson_contract_audit import audit_ruson_contract


class FakeConnector:
    def __init__(self) -> None:
        self.page = 0

    async def search(self, _filters, cursor=None):
        number = int(cursor or 1)
        ids = {
            1: ["rus-on.ru:100", "rus-on.ru:101"],
            2: ["rus-on.ru:102", "rus-on.ru:103"],
        }[number]
        return SimpleNamespace(
            items=[SimpleNamespace(external_id=value) for value in ids],
            next_cursor="2" if number == 1 else None,
        )

    async def fetch_lot(self, external_id):
        public = external_id.endswith(("100", "102"))
        closed = external_id.endswith("103")
        schedule = (
            [{"starts_at": "2026-11-08T11:00:00", "ends_at": "2026-11-11T11:00:00", "price": 293222.25}]
            if public else None
        )
        return SimpleNamespace(
            external_id=external_id,
            source_system="rus-on.ru",
            start_price=325802.50 if public else 7415000,
            current_price=293222.25 if public else None,
            auction_status="closed" if closed else "active",
            application_start_at=datetime(2026, 11, 8, 14, 0),
            application_deadline=datetime(2026, 11, 11, 13, 59),
            auction_at=None if public else datetime(2026, 12, 21, 10, 0),
            auction_type="public_offer" if public else "auction",
            public_offer_schedule=schedule,
            category="real_estate",
            raw_data={
                "ruson_initial_price_format": "decimal_dot",
                "ruson_initial_price_had_currency": external_id.endswith("103"),
            },
        )


def test_ruson_audit_proves_two_pass_terminal_identity_and_read_only_detail_sample() -> None:
    report = asyncio.run(audit_ruson_contract(
        target_details=4,
        max_attempts=12,
        max_pages=5,
        delay_seconds=0,
        connector_factory=FakeConnector,
    ))
    assert report["catalogue_pages_pass_1"] == 2
    assert report["catalogue_pages_pass_2"] == 2
    assert report["terminal_page"] == 2
    assert report["terminal_page_proven"] is True
    assert report["unique_catalogue_ids"] == 4
    assert report["two_pass_ids_equal"] is True
    assert report["details_verified"] == 4
    assert report["initial_price_success_rate"] == 1.0
    assert report["status_success_rate"] == 1.0
    assert report["ordinary_auction_details"] == 2
    assert report["public_offer_details"] == 2
    assert report["ended_details"] == 1
    assert report["production_database_writes"] == 0
    assert report["production_enabled"] is False
    assert report["archival_allowed"] is False
