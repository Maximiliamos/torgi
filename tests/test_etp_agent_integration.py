from __future__ import annotations

import asyncio

import pytest

from bankrotai.connectors.base import AuctionConnector
from bankrotai.connectors.etp_sources import PROFILES, SOURCE_CONNECTOR_TYPES


AGENT_SOURCE_IDS = {
    "alfalot.ru",
    "centerr.ru",
    "arbbitlot.ru",
    "rus-on.ru",
    "etpu.ru",
    "zakazrf.ru",
    "utpl.ru",
    "aukcioncenter.ru",
    "fabrikant.ru",
    "vertrades.ru",
    "sistematorg.com",
    "sberbank-ast.ru",
}


def test_all_twelve_agent_sources_remain_registered() -> None:
    assert AGENT_SOURCE_IDS <= SOURCE_CONNECTOR_TYPES.keys()
    for source_id in AGENT_SOURCE_IDS:
        connector = SOURCE_CONNECTOR_TYPES[source_id]()
        assert isinstance(connector, AuctionConnector)
        assert connector.source_id == source_id
        assert connector.safe_for_reconciliation is False


def test_unproven_alfalot_contract_is_fail_closed() -> None:
    connector = SOURCE_CONNECTOR_TYPES["alfalot.ru"]()
    with pytest.raises(Exception, match="(?i)(alfalot|contract|verified|production|http)"):
        asyncio.run(connector.search({}))
    health = asyncio.run(connector.healthcheck())
    assert health.status == "blocked"


def test_verified_source_specific_connectors_own_detail_enrichment() -> None:
    for source_id in {
        "centerr.ru",
        "arbbitlot.ru",
        "etpu.ru",
        "zakazrf.ru",
        "utpl.ru",
        "aukcioncenter.ru",
        "fabrikant.ru",
        "vertrades.ru",
        "sistematorg.com",
        "sberbank-ast.ru",
        "rus-on.ru",
    }:
        connector = SOURCE_CONNECTOR_TYPES[source_id]()
        assert "detail_enrichment" in connector.capabilities


def test_sberbank_profile_points_at_bankruptcy_entry() -> None:
    profile = next(item for item in PROFILES if item.source_id == "sberbank-ast.ru")
    assert profile.listing_url == "https://utp.sberbank-ast.ru/Bankruptcy/NBT/Index/0/0/0/0"
