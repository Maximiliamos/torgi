from __future__ import annotations

import asyncio

from bankrotai.connectors.base import ConnectorPage
from bankrotai.domain import NormalizedLot
from bankrotai.services.scheduled_source_preflight import _probe_nistp_connector


def _lot(external_id: str) -> NormalizedLot:
    return NormalizedLot(
        external_id=external_id,
        source="nistp",
        source_system="nistp.ru",
        title="Квартира",
        description="Квартира",
        category="apartment",
        region_slug=None,
        region_name=None,
        address=None,
        cadastral_number=None,
        vin=None,
        area=None,
        start_price=1_000_000.0,
        current_price=None,
        auction_status="scheduled",
        lot_url="https://nistp.ru/card",
        source_url="https://nistp.ru/card",
        detail_level="search",
        raw_data={"catalogue_source_status_text": "Торги объявлены"},
    )


class _Connector:
    def __init__(self) -> None:
        self.pages = [
            [_lot("nistp.ru:500001:1"), _lot("nistp.ru:500002:1")],
            [_lot("nistp.ru:500003:1")],
        ]
        self.enriched: list[str] = []

    async def search(self, _filters, cursor=None):
        page_number = int(cursor or 1)
        return ConnectorPage(
            items=self.pages[page_number - 1],
            next_cursor=str(page_number + 1) if page_number < len(self.pages) else None,
            metadata={},
        )

    async def enrich_lot(self, lot):
        self.enriched.append(lot.external_id)
        if lot.external_id != "nistp.ru:500003:1":
            raise ValueError("catalogue-only row")
        lot.detail_level = "detail"
        lot.raw_data.update(
            {
                "detail_enrichment_status": "success",
                "individual_lot_identity_verified": True,
                "detail_start_price_verified": True,
                "detail_status_normalized": "scheduled",
            }
        )
        return lot


def test_nistp_preflight_searches_beyond_two_bad_first_page_rows() -> None:
    connector = _Connector()
    result = asyncio.run(
        _probe_nistp_connector(connector, max_pages=3, max_detail_attempts=5)
    )

    assert result["success"] is True
    assert result["pages_checked"] == 2
    assert result["detail_attempts"] == 3
    assert result["external_id"] == "nistp.ru:500003:1"
    assert connector.enriched == [
        "nistp.ru:500001:1",
        "nistp.ru:500002:1",
        "nistp.ru:500003:1",
    ]
