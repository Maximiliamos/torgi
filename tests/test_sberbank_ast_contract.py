from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import requests

from bankrotai.connectors.etp_sources import (
    SOURCE_CONNECTOR_TYPES,
    SourceSchemaError,
    parse_detail_html,
    parse_sberbank_json,
)
from bankrotai.connectors.etp_sources.profiles import PROFILES


class FakeResponse:
    def __init__(self, status_code: int, content: bytes, url: str = "https://www.sberbank-ast.ru/api/Processing/main") -> None:
        self.status_code = status_code
        self.content = content
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return json.loads(self.content)


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.headers: dict[str, str] = {}
        self.posts: list[tuple[str, object]] = []

    def post(self, url, *, data=None, json=None, **_kwargs):
        self.posts.append((url, data if data is not None else json))
        return self.response

    def get(self, *_args, **_kwargs):
        return self.response


BANKRUPTCY = "Продажа имущества (предприятия) банкротов"
FIXTURES = Path(__file__).parent / "fixtures" / "sberbank_ast"


def _profile():
    return next(item for item in PROFILES if item.source_id == "sberbank-ast.ru")


def _row(
    *,
    goods_id: int,
    price: str = "1000000",
    branch: str = "Недвижимое имущество",
    purch_code: str = "SBR013-TEST-MULTI.1",
) -> dict:
    return {
        "objectHref": f"https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/11/0/0/{goods_id}",
        "title": f"Нежилое помещение, лот {goods_id}",
        "purchName": "Продажа имущества должника",
        "ObjectSource": BANKRUPTCY,
        "SourceTradeSectionId": "13",
        "BranchGroup": branch,
        "purchAmountRUB": price,
        "PurchaseStageTerm": "Прием заявок",
        "purchCode": purch_code,
        "Address": "Тестовый адрес",
    }


@pytest.mark.parametrize(
    "source,section",
    [
        ("Приватизация, аренда и продажа прав", "13"),
        (BANKRUPTCY, ""),
        (BANKRUPTCY, "99"),
    ],
)
def test_sberbank_catalogue_fails_closed_without_confirmed_bankruptcy_scope(
    source: str, section: str,
) -> None:
    row = _row(goods_id=101)
    row["ObjectSource"] = source
    row["SourceTradeSectionId"] = section
    with pytest.raises(SourceSchemaError, match="bankruptcy filter is not confirmed"):
        parse_sberbank_json(_profile(), {"PurchaseList": [{"_source": row}]}, page=1)


def test_sberbank_uses_structured_property_branch_not_free_text() -> None:
    vehicle = _row(goods_id=101, branch="Транспорт")
    vehicle["title"] = "Земельный участок написан в свободном тексте"
    realty = _row(goods_id=102)
    lots = parse_sberbank_json(
        _profile(),
        {"PurchaseList": [{"_source": vehicle}, {"_source": realty}]},
        page=1,
    )
    assert [lot.external_id for lot in lots] == ["sberbank-ast.ru:11-0-0-102"]


def test_sberbank_multi_lot_procedure_keeps_distinct_goods_ids_and_prices() -> None:
    first = _row(goods_id=201, price="1250000", purch_code="SBR013-2607300003-0.1")
    second = _row(goods_id=202, price="2750000", purch_code="SBR013-2607300003-0.2")
    lots = parse_sberbank_json(
        _profile(),
        {"PurchaseList": [{"_source": first}, {"_source": second}]},
        page=1,
    )
    assert [lot.external_id for lot in lots] == [
        "sberbank-ast.ru:11-0-0-201",
        "sberbank-ast.ru:11-0-0-202",
    ]
    assert [lot.start_price for lot in lots] == [1250000, 2750000]
    assert {lot.procedure_number for lot in lots} == {"SBR013-2607300003-0"}
    assert [lot.raw_data["source_lot_number"] for lot in lots] == ["1", "2"]
    assert [lot.raw_data["source_lot_code"] for lot in lots] == [
        "SBR013-2607300003-0.1",
        "SBR013-2607300003-0.2",
    ]


def test_sberbank_duplicate_goods_identity_fails_closed() -> None:
    row = _row(goods_id=301)
    with pytest.raises(SourceSchemaError, match="duplicate GoodsView identity"):
        parse_sberbank_json(
            _profile(),
            {"PurchaseList": [{"_source": row}, {"_source": dict(row)}]},
            page=1,
        )


def test_sberbank_display_cap_is_not_terminal_source_end() -> None:
    payload = {"Total": 10000, "PurchaseList": [{"_source": _row(goods_id=401)}]}
    connector = SOURCE_CONNECTOR_TYPES["sberbank-ast.ru"](
        session=FakeSession(FakeResponse(200, json.dumps(payload).encode()))
    )
    with pytest.raises(SourceSchemaError, match="display cap, not a proven source end"):
        asyncio.run(connector.search({}, cursor="477"))


def test_sberbank_display_cap_is_not_reported_as_source_total() -> None:
    payload = {"Total": 10000, "PurchaseList": [{"_source": _row(goods_id=402)}]}
    connector = SOURCE_CONNECTOR_TYPES["sberbank-ast.ru"](
        session=FakeSession(FakeResponse(200, json.dumps(payload).encode()))
    )
    result = asyncio.run(connector.search({}))
    assert result.next_cursor == "2"
    assert result.metadata["total_items"] is None
    assert result.metadata["api_total_reported"] == 10000
    assert result.metadata["api_total_is_source_end"] is False
    assert result.metadata["terminal_page_proven"] is False


def test_sberbank_sanitized_catalogue_fixture_is_verified() -> None:
    payload = json.loads(
        (FIXTURES / "catalogue_bankruptcy_real_estate.json").read_text(encoding="utf-8")
    )
    lots = parse_sberbank_json(_profile(), payload, page=1)
    assert len(lots) == 1
    assert lots[0].external_id == "sberbank-ast.ru:11-0-0-900001"
    assert lots[0].start_price == 24643766
    assert lots[0].raw_data["bankruptcy_filter_verified"] is True


def test_sberbank_goodsview_detail_identity_and_fields() -> None:
    html = (FIXTURES / "goodsview_detail.html").read_text(encoding="utf-8")
    external_id = "sberbank-ast.ru:11-0-0-900001"
    detail = parse_detail_html(
        _profile(),
        html,
        detail_url="https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/11/0/0/900001",
        external_id=external_id,
    )
    assert detail.external_id == external_id
    assert detail.start_price == 24643766
    assert detail.current_price == 23000000
    assert detail.cadastral_number == "77:05:0000000:3121"
    assert detail.area == 87.2
    assert detail.procedure_number == "SBR013-TEST-MULTI"
    assert detail.application_deadline.isoformat() == "2026-11-25T14:00:00"
    assert detail.auction_at.isoformat() == "2026-12-03T14:00:00"
    assert detail.raw_data["individual_lot_identity_verified"] is True


def test_sberbank_goodsview_rejects_catalogue_detail_identity_mismatch() -> None:
    html = ("<html><body><h1>Лот № 1 — помещение</h1>"
            "Начальная цена 1 000 000 руб. Кадастровый номер 77:05:0000000:3121 "
            + ("описание имущества " * 30) + "</body></html>")
    with pytest.raises(SourceSchemaError, match="identity does not match"):
        parse_detail_html(
            _profile(),
            html,
            detail_url="https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/11/0/0/500",
            external_id="sberbank-ast.ru:11-0-0-501",
        )
