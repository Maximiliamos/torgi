from __future__ import annotations

import json
from pathlib import Path

import pytest

from bankrotai.connectors.etp_sources.sberbank_ast_contract import (
    API_URL,
    BANKRUPTCY_SOURCE,
    DISPLAY_LIMIT,
    REAL_ESTATE_BRANCH,
    SberbankAstContractError,
    build_catalogue_request,
    detail_url_from_external_id,
    page_digest,
    parse_catalogue_page,
    parse_catalogue_row,
    parse_detail,
    parse_initial_price,
    split_sberbank_lot_code,
    terminal_page_for_total,
    verify_identity,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sberbank_ast"


def _row(
    *,
    goods_id: int,
    price: str = "1000000",
    branch: str = REAL_ESTATE_BRANCH,
    purch_code: str = "SBR013-TEST-MULTI.1",
) -> dict:
    return {
        "objectHref": f"https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/11/0/0/{goods_id}",
        "title": f"Нежилое помещение, лот {goods_id}",
        "purchName": "Продажа имущества должника",
        "ObjectSource": BANKRUPTCY_SOURCE,
        "SourceTradeSectionId": "13",
        "BranchGroup": branch,
        "purchAmountRUB": price,
        "PurchaseStageTerm": "Прием заявок",
        "purchCode": purch_code,
        "Address": "Тестовый адрес",
        "RequestStopDate": "25.11.2026 14:00",
    }


def test_catalogue_request_encodes_bankruptcy_filter_and_zero_based_page() -> None:
    url, payload = build_catalogue_request(2)
    assert url == API_URL
    assert payload["windowCode"] == "/ESGoodsList"
    assert "<pagenum>1</pagenum>" in payload["filterBody"]
    assert f"<sourceTerm>{BANKRUPTCY_SOURCE}</sourceTerm>" in payload["filterBody"]


@pytest.mark.parametrize(
    "source,section",
    [
        ("Приватизация, аренда и продажа прав", "13"),
        (BANKRUPTCY_SOURCE, ""),
        (BANKRUPTCY_SOURCE, "99"),
    ],
)
def test_catalogue_fails_closed_without_confirmed_bankruptcy_scope(
    source: str, section: str,
) -> None:
    row = _row(goods_id=101)
    row["ObjectSource"] = source
    row["SourceTradeSectionId"] = section
    with pytest.raises(SberbankAstContractError, match="bankruptcy filter is not confirmed"):
        parse_catalogue_row(row, page=1)


def test_structured_property_branch_overrides_free_text() -> None:
    vehicle = _row(goods_id=101, branch="Транспорт")
    vehicle["title"] = "Земельный участок написан в свободном тексте"
    assert parse_catalogue_row(vehicle, page=1) is None

    realty = _row(goods_id=102)
    realty["title"] = "Автомобиль написан в свободном тексте"
    lot = parse_catalogue_row(realty, page=1)
    assert lot is not None
    assert lot.category == "real_estate"
    assert lot.raw_data["structured_category_verified"] is True


def test_multi_lot_procedure_keeps_distinct_goods_ids_and_prices() -> None:
    payload = {
        "Total": 2,
        "PurchaseList": [
            {"_source": _row(goods_id=201, price="1250000", purch_code="SBR013-2607300003-0.1")},
            {"_source": _row(goods_id=202, price="2750000", purch_code="SBR013-2607300003-0.2")},
        ],
    }
    page = parse_catalogue_page(payload, page=1)
    assert [lot.external_id for lot in page.lots] == [
        "sberbank-ast.ru:11-0-0-201",
        "sberbank-ast.ru:11-0-0-202",
    ]
    assert [lot.start_price for lot in page.lots] == [1250000, 2750000]
    assert {lot.procedure_number for lot in page.lots} == {"SBR013-2607300003-0"}
    assert [lot.raw_data["source_lot_number"] for lot in page.lots] == ["1", "2"]


def test_source_lot_code_parses_procedure_and_individual_lot_number() -> None:
    assert split_sberbank_lot_code("SBR013-2607300003-0.12") == (
        "SBR013-2607300003-0.12",
        "SBR013-2607300003-0",
        "12",
    )


def test_duplicate_goods_identity_fails_closed() -> None:
    row = _row(goods_id=301)
    payload = {
        "Total": 2,
        "PurchaseList": [{"_source": row}, {"_source": dict(row)}],
    }
    with pytest.raises(SberbankAstContractError, match="duplicate GoodsView identity"):
        parse_catalogue_page(payload, page=1)


def test_display_cap_is_not_terminal_source_end() -> None:
    payload = {"Total": DISPLAY_LIMIT, "PurchaseList": [{"_source": _row(goods_id=401)}]}
    page = parse_catalogue_page(payload, page=1)
    assert page.source_total_proven is False
    assert page.terminal_page is None
    assert page.terminal_page_proven is False
    assert page.next_page == 2

    with pytest.raises(SberbankAstContractError, match="display cap, not a proven source end"):
        parse_catalogue_page(payload, page=477)


def test_terminal_page_is_proven_only_below_display_cap() -> None:
    assert terminal_page_for_total(22) == 2
    payload = {"Total": 22, "PurchaseList": [{"_source": _row(goods_id=501)}]}
    terminal = parse_catalogue_page(payload, page=2)
    assert terminal.terminal_page == 2
    assert terminal.terminal_page_proven is True
    assert terminal.source_total_proven is True
    assert terminal.next_page is None


def test_explicit_ruble_price_rules_do_not_invent_price() -> None:
    raw = _row(goods_id=601)
    raw["purchAmountRUB"] = "722100"
    raw["purchAmount"] = "900000"
    assert parse_initial_price(raw) == 722100

    raw["purchAmountRUB"] = None
    raw["purchCurrency"] = "USD"
    assert parse_initial_price(raw) is None

    raw["purchCurrency"] = "RUB"
    assert parse_initial_price(raw) == 900000


def test_sanitized_catalogue_fixture_is_verified() -> None:
    payload = json.loads(
        (FIXTURES / "catalogue_bankruptcy_real_estate.json").read_text(encoding="utf-8")
    )
    page = parse_catalogue_page(payload, page=1)
    assert len(page.lots) == 1
    lot = page.lots[0]
    assert lot.external_id == "sberbank-ast.ru:11-0-0-900001"
    assert lot.start_price == 24643766
    assert lot.raw_data["bankruptcy_filter_verified"] is True
    assert page.terminal_page_proven is True
    assert len(page_digest(page.lots)) == 64


def test_goodsview_detail_identity_and_fields() -> None:
    html = (FIXTURES / "goodsview_detail.html").read_text(encoding="utf-8")
    external_id = "sberbank-ast.ru:11-0-0-900001"
    detail = parse_detail(
        html,
        detail_url=detail_url_from_external_id(external_id),
        external_id=external_id,
    )
    assert detail.external_id == external_id
    assert detail.start_price == 24643766
    assert detail.current_price == 23000000
    assert detail.cadastral_number == "77:05:0000000:3121"
    assert detail.area == 87.2
    assert detail.procedure_number == "SBR013-TEST-MULTI"
    assert detail.application_start_at.isoformat() == "2026-11-10T09:00:00"
    assert detail.application_deadline.isoformat() == "2026-11-25T14:00:00"
    assert detail.auction_at.isoformat() == "2026-12-03T14:00:00"
    assert detail.raw_data["individual_lot_identity_verified"] is True


def test_goodsview_rejects_catalogue_detail_identity_mismatch() -> None:
    with pytest.raises(SberbankAstContractError, match="identity does not match"):
        verify_identity(
            external_id="sberbank-ast.ru:11-0-0-501",
            detail_url="https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/11/0/0/500",
        )


def test_goodsview_rejects_generic_shell() -> None:
    html = "<html><body>" + ("информационная страница " * 30) + "</body></html>"
    external_id = "sberbank-ast.ru:11-0-0-700"
    with pytest.raises(SberbankAstContractError, match="does not prove an individual property card"):
        parse_detail(
            html,
            detail_url=detail_url_from_external_id(external_id),
            external_id=external_id,
        )
