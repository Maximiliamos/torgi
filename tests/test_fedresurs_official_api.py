"""Contract regression tests for official EFRSB REST Publications API 1.4."""
from __future__ import annotations

import asyncio

import pytest

from bankrotai.connectors.base import ConnectorCapabilityError
from bankrotai.connectors.registry.fedresurs import FedresursConnector
from bankrotai.connectors.registry.fedresurs_publications import (
    DEMO_URL,
    FedresursProtocolError,
    FedresursPublicationsClient,
)


def _row(n: int) -> dict:
    return {
        "guid": f"00000000-0000-4000-8000-{n:012d}",
        "number": str(n),
        "datePublish": "2026-10-27T12:00:00",
        "type": "BiddingInvitation",
        "trade": {"number": f"T-{n}", "guid": f"00000000-0000-4000-8000-{n + 100:012d}"},
        "tradePlaceGuid": "00000000-0000-4000-8000-000000000099",
    }


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, rows=None, *, mismatched_total=False):
        self.calls = []
        self.rows = [_row(1), _row(2), _row(3)] if rows is None else rows
        self.mismatched_total = mismatched_total

    def post(self, url, *, json, timeout, allow_redirects):
        self.calls.append(("POST", url, json, allow_redirects))
        assert url == f"{DEMO_URL}/v1/auth"
        assert json == {"login": "demo-user", "password": "p@ss-word"}
        assert allow_redirects is False
        return FakeResponse({"jwt": "temporary-test-token"})

    def get(self, url, *, params, headers, timeout, allow_redirects):
        self.calls.append(("GET", url, params, allow_redirects))
        assert url == f"{DEMO_URL}/v1/trade-messages"
        assert headers["Authorization"] == "Bearer temporary-test-token"
        assert params["includeContent"] == "false"
        assert params["sort"] == "DatePublish:asc"
        assert params["datePublishBegin"] == "gte:2026-10-27T00:00:00"
        assert params["datePublishEnd"] == "lt:2026-10-28T00:00:00"
        assert allow_redirects is False
        offset = params["offset"]
        payload = {
            "pageData": self.rows[offset : offset + params["limit"]],
            "total": len(self.rows) + (1 if self.mismatched_total and offset else 0),
        }
        return FakeResponse(payload)


def client(fake=None):
    fake = fake or FakeSession()
    return FedresursPublicationsClient(
        login="demo-user", password="p@ss-word",
        base_url=DEMO_URL, session=fake,
    ), fake


def test_official_auth_is_plain_password_over_https_and_reuses_jwt():
    instance, fake = client()
    result = instance.collect_bounded(
        kind="trade-messages",
        start_date="2026-10-27", end_date="2026-10-27",
        page_size=2, max_pages=2,
    )
    assert result["complete"] is True
    assert result["collected"] == 3
    assert result["reported_total"] == 3
    assert len([call for call in fake.calls if call[0] == "POST"]) == 1
    assert [call[2]["offset"] for call in fake.calls if call[0] == "GET"] == [0, 2]


def test_bounded_pilot_reports_incomplete_not_a_complete_sync():
    instance, _ = client()
    result = instance.collect_bounded(
        kind="trade-messages", start_date="2026-10-27",
        end_date="2026-10-27", page_size=2, max_pages=1,
    )
    assert result["collected"] == 2
    assert result["reported_total"] == 3
    assert result["complete"] is False


def test_total_change_fails_closed():
    instance, _ = client(FakeSession(mismatched_total=True))
    with pytest.raises(FedresursProtocolError, match="total changed"):
        instance.collect_bounded(
            kind="trade-messages", start_date="2026-10-27",
            end_date="2026-10-27", page_size=2, max_pages=2,
        )


def test_duplicate_page_fails_closed():
    instance, _ = client(FakeSession(rows=[_row(1), _row(2), _row(1)]))
    with pytest.raises(FedresursProtocolError, match="Duplicate"):
        instance.collect_bounded(
            kind="trade-messages", start_date="2026-10-27",
            end_date="2026-10-27", page_size=2, max_pages=2,
        )


@pytest.mark.parametrize(("start", "end"), [
    ("2026-10-27", "2026-10-26"),
    ("2026-10-01", "2026-11-10"),
    ("not-a-date", "2026-10-27"),
])
def test_invalid_date_periods_refused_without_network(start, end):
    instance, fake = client()
    with pytest.raises(ValueError):
        instance.list_page(
            kind="trade-messages", start_date=start, end_date=end
        )
    assert fake.calls == []


def test_refuse_unofficial_https_destination_with_credentials():
    with pytest.raises(ValueError):
        FedresursPublicationsClient(
            login="secret", password="secret",
            base_url="https://not-federal.example",
        )


def test_no_efrsb_event_is_silently_persisted_as_a_sale_lot():
    instance = FedresursConnector(login="demo-user", password="p@ss-word", base_url=DEMO_URL)
    with pytest.raises(ConnectorCapabilityError, match="not individual sale lots"):
        asyncio.run(instance.search({}))
    with pytest.raises(ConnectorCapabilityError, match="not a lot identifier"):
        asyncio.run(instance.fetch_lot("00000000-0000-4000-8000-000000000001"))


def test_event_index_never_exposes_raw_content_or_credentials():
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts/probe_fedresurs_official.py"
    spec = importlib.util.spec_from_file_location("fedresurs_probe", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    instance, _ = client()
    report = module.report_for_period(
        instance, kind="trade-messages", start="2026-10-27", end="2026-10-27",
        types=None, page_size=2, max_pages=2,
    )
    assert report["data_written_to_torgi"] is False
    assert report["complete"]
    assert "password" not in str(report)
    assert "content" not in report["event_index"][0]
    assert "number" in report["event_index"][0]
