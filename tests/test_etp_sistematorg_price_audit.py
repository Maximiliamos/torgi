"""Sistematorg price diagnosis is read-only and does not infer multi-lot values."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bankrotai.services.etp_sistematorg_price_audit import audit_price_evidence


class FakeConnector:
    def __init__(self):
        self.detail_ids = []

    async def search(self, _filters):
        return SimpleNamespace(items=[
            SimpleNamespace(external_id="sistematorg.com:1",
                            raw_data={"reported_trade_lots": 2}),
            SimpleNamespace(external_id="sistematorg.com:2",
                            raw_data={"reported_trade_lots": 1}),
            SimpleNamespace(external_id="sistematorg.com:3",
                            raw_data={"reported_trade_lots": None}),
        ])

    async def fetch_lot(self, external_id):
        self.detail_ids.append(external_id)
        return SimpleNamespace(start_price=1_200_000, current_price=None)


def test_probe_only_requests_single_lot_detail_and_does_not_write():
    connector = FakeConnector()
    result = asyncio.run(audit_price_evidence(connector=connector))
    assert connector.detail_ids == ["sistematorg.com:2"]
    assert result["catalogue_trade_rows"] == 3
    assert result["multiple_or_unknown_lots"] == 2
    assert result["single_lot_detail_with_labelled_price"] == 1
    assert result["production_database_writes"] == 0
    assert result["archival_allowed"] is False
    assert result["coverage_complete"] is False


@pytest.mark.parametrize("limit, delay", [(0, 1), (21, 1), (1, 0.1)])
def test_probe_rejects_unsafe_budgets(limit, delay):
    with pytest.raises(ValueError):
        asyncio.run(audit_price_evidence(
            connector=FakeConnector(), max_details=limit, delay_seconds=delay,
        ))
