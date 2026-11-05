from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
import requests

from bankrotai.connectors.base import ConnectorPage
from bankrotai.domain import NormalizedLot
from bankrotai.services.etp_nistp_full_catalogue import (
    NistpCatalogueScanError,
    run_two_independent_passes,
    scan_complete_nistp_catalogue,
)


@dataclass
class _Response:
    content: bytes
    url: str


def _lot(external_id: str, price: float = 100.0, status: str = "active") -> NormalizedLot:
    return NormalizedLot(
        external_id=external_id, source="nistp", source_system="nistp.ru",
        title=external_id, description=external_id, category="other",
        region_slug=None, region_name=None, address=None, cadastral_number=None,
        vin=None, area=None, start_price=price, current_price=None,
        auction_status=status, lot_url="https://nistp.ru/card", source_url="https://nistp.ru/card",
        detail_level="search", raw_data={},
    )


class _Connector:
    def __init__(self, pages: list[list[NormalizedLot]]) -> None:
        self.pages = pages
        self._last_response = None

    async def search(self, _filters, cursor=None):
        page = int(cursor)
        rows = []
        for item in self.pages[page - 1]:
            _, provider_id, lot_number = item.external_id.split(":")
            status = {
                "active": "Прием заявок",
                "applications_closed": "Прием заявок завершен",
            }.get(item.auction_status, "Неясно")
            rows.append(
                "<tr>"
                f"<td>70000-ОАОФ</td><td>Организатор</td>"
                f"<td>Должник Лот № {lot_number}: Квартира "
                f"<a href='/bankrot/trade_view.php?trade_nid={provider_id}'>Открыть</a></td>"
                f"<td>{item.start_price:.2f}</td>"
                "<td>01.01.2026</td><td>02.01.2026</td>"
                f"<td>{status}</td></tr>"
            )
        self._last_response = _Response(
            content=(
                "<form id='nodesearch'>"
                "<input checked name='trade_state[]' value='Торги объявлены'>"
                "<input checked name='trade_state[]' value='Прием заявок'>"
                "<input checked name='trade_state[]' value='Идут торги'>"
                "</form>"
                f"<table>{''.join(rows)}</table>"
                f"<ul class='pagination'><li class='active'>"
                f"<a onclick='pagenum_send({page})'>{page}</a></li></ul>"
            ).encode(),
            url=f"https://nistp.ru/?view_type=lot&pagenum={page}",
        )
        return ConnectorPage(
            items=self.pages[page - 1],
            next_cursor=str(page + 1) if page < len(self.pages) else None,
            metadata={},
        )


def test_full_walk_proves_selected_last_page_and_unique_individual_lots() -> None:
    report = asyncio.run(scan_complete_nistp_catalogue(_Connector([
        [_lot("nistp.ru:501:1"), _lot("nistp.ru:501:2")],
        [_lot("nistp.ru:502:1")],
    ]), delay_seconds=0))
    assert report["last_page_proven"] is True
    assert report["last_page"] == 2
    assert report["unique_lot_ids"] == 3
    assert report["active_filter_proven_on_every_page"] is True
    assert report["trade_states"] == [
        "Торги объявлены", "Прием заявок", "Идут торги",
    ]
    assert report["production_database_writes"] == 0
    assert report["archival_allowed"] is False


@pytest.mark.parametrize("bad", [
    _lot("nistp.ru:501:1", price=0),
    _lot("nistp.ru:501:1", status="unknown"),
])
def test_full_walk_rejects_missing_individual_price_or_status(bad: NormalizedLot) -> None:
    with pytest.raises(NistpCatalogueScanError):
        asyncio.run(scan_complete_nistp_catalogue(_Connector([[bad]]), delay_seconds=0))


def test_active_walk_rejects_application_intake_ended_as_out_of_scope() -> None:
    with pytest.raises(NistpCatalogueScanError, match="Out-of-scope NISTP status"):
        asyncio.run(scan_complete_nistp_catalogue(
            _Connector([[_lot("nistp.ru:499191:1", status="applications_closed")]]),
            delay_seconds=0,
        ))


def test_full_walk_rejects_cross_page_duplicate_identity() -> None:
    connector = _Connector([[_lot("nistp.ru:501:1")], [_lot("nistp.ru:501:1")]])
    with pytest.raises(NistpCatalogueScanError, match="Cross-page duplicate"):
        asyncio.run(scan_complete_nistp_catalogue(connector, delay_seconds=0))


def test_two_passes_must_match_exact_identity_digest() -> None:
    reports = [
        [[_lot("nistp.ru:501:1")]],
        [[_lot("nistp.ru:501:1")]],
    ]
    result = asyncio.run(run_two_independent_passes(
        lambda: _Connector(reports.pop(0)), delay_seconds=0,
    ))
    assert result["passes_match"] is True
    assert result["last_page_proven_twice"] is True


def test_two_passes_must_also_match_prices_and_source_statuses() -> None:
    reports = [
        [[_lot("nistp.ru:501:1", price=100)]],
        [[_lot("nistp.ru:501:1", price=200)]],
    ]
    result = asyncio.run(run_two_independent_passes(
        lambda: _Connector(reports.pop(0)), delay_seconds=0,
    ))
    assert result["passes_match"] is False
    assert result["last_page_proven_twice"] is False


def test_absent_selected_page_marker_never_proves_completion() -> None:
    connector = _Connector([[_lot("nistp.ru:501:1")]])

    async def search(_filters, cursor=None):
        connector._last_response = _Response(b"<ul class='pagination'></ul>", "https://nistp.ru/")
        return ConnectorPage(items=[_lot("nistp.ru:501:1")], next_cursor=None, metadata={})

    connector.search = search
    with pytest.raises(NistpCatalogueScanError, match="not visibly selected"):
        asyncio.run(scan_complete_nistp_catalogue(connector, delay_seconds=0))


def test_missing_active_filter_selection_never_proves_catalogue_scope() -> None:
    connector = _Connector([[_lot("nistp.ru:501:1")]])
    original = connector.search

    async def search(filters, cursor=None):
        result = await original(filters, cursor=cursor)
        connector._last_response.content = connector._last_response.content.replace(
            b"<input checked", b"<input"
        )
        return result

    connector.search = search
    with pytest.raises(NistpCatalogueScanError, match="active trade_state filters"):
        asyncio.run(scan_complete_nistp_catalogue(connector, delay_seconds=0))


def test_transient_network_failure_is_retried_without_skipping_page() -> None:
    connector = _Connector([[_lot("nistp.ru:501:1")]])
    original = connector.search
    attempts = 0

    async def flaky_search(filters, cursor=None):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise requests.Timeout("transient")
        return await original(filters, cursor=cursor)

    connector.search = flaky_search
    result = asyncio.run(scan_complete_nistp_catalogue(
        connector, delay_seconds=0, sleep=lambda _seconds: None,
    ))
    assert attempts == 3
    assert result["unique_lot_ids"] == 1


def test_selected_schema_correct_empty_page_proves_terminal_sentinel() -> None:
    from bankrotai.connectors.etp_sources import SourceSchemaError

    connector = _Connector([[_lot("nistp.ru:501:1")], []])
    original = connector.search

    async def search(filters, cursor=None):
        page = int(cursor)
        if page == 1:
            return await original(filters, cursor=cursor)
        connector._last_response = _Response(
            content="""
            <form id='nodesearch'>
            <input checked name='trade_state[]' value='Торги объявлены'>
            <input checked name='trade_state[]' value='Прием заявок'>
            <input checked name='trade_state[]' value='Идут торги'>
            </form>
            <ul class='pagination'><li class='active'><a>2</a></li></ul>
            <table><tr><td>Код торгов</td><td>Организатор</td><td>Должник</td>
            <td>Начальная цена</td><td>Начало приема заявок</td>
            <td>Конец приема заявок</td><td>Состояние</td></tr></table>
            """.encode(),
            url="https://nistp.ru/?view_type=lot&pagenum=2",
        )
        raise SourceSchemaError("empty")

    connector.search = search
    report = asyncio.run(scan_complete_nistp_catalogue(connector, delay_seconds=0))
    assert report["last_page"] == 2
    assert report["last_page_proven"] is True
    assert report["pages"][-1]["empty_terminal_page"] is True
