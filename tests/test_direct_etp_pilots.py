"""ETP pilot registry contract: visible to developers, not the production scheduler."""
from __future__ import annotations

import asyncio

import pytest

from bankrotai.connectors.base import ConnectorCapabilityError
from bankrotai.connectors.registry import connector_registry
from bankrotai.connectors.registry.direct_etp_pilots import (
    DirectEtpPilotConnector, PILOT_SOURCE_IDS,
)
from bankrotai.services.ingestion import default_source_specs

EXAMPLE = """
<table>
<tr><td>100928</td><td>Секц.</td><td>1</td>
<td><a href="/public/auctions/lots/view/1234/">Земельный участок 47:16:0420001:37</a></td>
<td>1 250 000</td><td>date</td><td>date</td><td>type</td><td>Приём заявок</td></tr>
</table>
"""


class StubResponse:
    status_code = 200
    headers = {"Content-Type": "text/html"}

    def __init__(self, html):
        self.text = html
        self.content = html.encode("utf-8")

    def raise_for_status(self):
        return None


class StubSession:
    def get(self, url, *, timeout, headers, allow_redirects):
        assert url == "https://bankrupt.etpu.ru/public/purchases-all/"
        assert allow_redirects is False
        return StubResponse(EXAMPLE)


def test_registry_maintains_14_distinct_readonly_pilots():
    assert len(PILOT_SOURCE_IDS) == 14
    for sid in PILOT_SOURCE_IDS:
        connector = connector_registry.create(sid)
        assert isinstance(connector, DirectEtpPilotConnector)
        assert connector.source_id == sid
        health = asyncio.run(connector.healthcheck())
        assert health.status == "pilot"
        assert health.metadata["can_reconcile_missing"] is False
        with pytest.raises(ConnectorCapabilityError):
            asyncio.run(connector.search(None))


def test_pilots_are_not_enabled_in_default_nationwide_sync():
    scheduled = {x.source_id for x in default_source_specs()}
    assert not scheduled.intersection(PILOT_SOURCE_IDS)


def test_preview_recovers_candidate_but_cannot_mutate_database():
    connector = DirectEtpPilotConnector("etpu.ru", session=StubSession())
    data = asyncio.run(connector.preview(limit=4))
    assert data["inspected"] == 1
    assert data["candidates"][0]["external_id"] == "100928:1"
    assert data["candidates"][0]["cadastral_number"] == "47:16:0420001:37"
    assert data["database_writes"] == 0
    assert data["ready_for_ingestion"] is False
    assert data["safe_for_archival"] is False


def test_unknown_pilot_and_overlarge_preview_fail_before_network():
    with pytest.raises(ValueError):
        DirectEtpPilotConnector("electro-torgi.ru")
    with pytest.raises(ValueError):
        asyncio.run(DirectEtpPilotConnector("etpu.ru", session=StubSession()).preview(limit=100))
