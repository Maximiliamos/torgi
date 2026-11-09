from __future__ import annotations

import asyncio
import json

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


def _profile():
    return next(item for item in PROFILES if item.source_id == "sberbank-ast.ru")


def _row(*, goods_id: int, price: str = "1000000", branch: str = "Недвижимое имущество") -> dict:
    return {
        "objectHref": f"https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/11/0/0/{goods_id}",
        "title": f"Нежилое помещение, лот {goods_id}",
        "purchName": "Продажа имущества должника",
        "ObjectSource": BANKRUPTCY,
        "SourceTradeSectionId": "13",
        "BranchGroup": branch,
        "purchAmountRUB": price,
        "PurchaseStageTerm": "Прием заявок",
        "purchCode": "SBR013-TEST-MULTI",
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
    first = _row(goods_id=201, price="1250000")
    second = _row(goods_id=202, price="2750000")
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
    assert {lot.procedure_number for lot in lots} == {"SBR013-TEST-MULTI"}


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


def test_sberbank_goodsview_detail_identity_and_fields() -> None:
    html = """
    <html><body>
      <h1>Лот № 2 — нежилое помещение</h1>
      <p>Номер процедуры SBR013-TEST-MULTI</p>
      <p>Адрес: Тестовый город, Тестовая улица, д. 1</p>
      <p>Кадастровый номер 77:05:0000000:3121</p>
      <p>Площадь 87,2 кв. м</p>
      <p>Начальная цена 24 643 766 руб.</p>
      <p>Текущая цена 23 000 000 руб.</p>
      <p>Прием заявок</p>
      <p>Дата окончания приема заявок 25.11.2026 14:00</p>
      <p>Дата проведения 03.12.2026 14:00</p>
      <p>Дополнительное описание индивидуального имущества для проверки lot-level detail contract.</p>
    </body></html>
    """
    external_id = "sberbank-ast.ru:11-0-0-51583"
    detail = parse_detail_html(
        _profile(),
        html,
        detail_url="https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/11/0/0/51583",
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
