"""Synthetic non-personal checks for public captured ETP API responses."""
import copy
import pytest

from bankrotai.connectors.registry.captured_etp_api import (
    ApiEvidenceError, parse_cdtrf_api_page, parse_sber_bankruptcy_api_page,
    verify_cdtrf_page_pair,
)


def cdt(lot_id=900):
    return {"success": True, "totalCount": 10455, "items": [
        {"tradeId": 800, "tradeLotId": lot_id, "name": "PERSON NAME - DO NOT EXTRACT",
         "categories": ",21,", "regionId": 13, "actualPrice": 150000,
         "tradeStatusDescription": "Торги объявлены"},
        {"tradeId": 801, "tradeLotId": lot_id + 1, "categories": ",1,",
         "name": "Автомобиль", "actualPrice": 30000},
    ]}


def sber():
    def hit(i, branch):
        return {"_source": {
            "ObjectSource": "Продажа имущества (предприятия) банкротов",
            "SourceTradeSectionId": "13", "Goods_id": str(i),
            "SourceObjectId": str(i + 100), "BranchGroup": branch,
            "objectHref": f"https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/12/0/0/{i}",
            "title": "Земельный участок, кадастр 47:16:0420001:37",
            "purchAmountRUB": "722100", "PurchaseStageTerm": "Опубликована",
            "OrgFullName": "PERSONAL DETAILS - NEVER EMIT",
        }}
    return {"Total": 375, "PurchaseList": [hit(101, "Недвижимое имущество"),
                                           hit(102, "Движимое имущество")]}


def test_cdtrf_identity_and_category_without_debtor_name():
    a = parse_cdtrf_api_page(cdt(), page_num=1)
    x = a["property_candidates"][0]
    assert a["reported_total"] == 10455
    assert len(a["property_candidates"]) == 1
    assert x["external_id"] == "cdtrf:900"
    assert x["detail_url"] == "https://torgi.cdtrf.ru/trades/800"
    assert x["title"] is None and x["title_unverified"] is True
    assert x["current_price_rub"] is None
    assert "PERSON" not in str(a)
    assert a["complete_source_inventory"] is False


def test_cdtrf_two_pages_distinct_and_no_archival():
    first = parse_cdtrf_api_page(cdt(900), page_num=1)
    second = parse_cdtrf_api_page(cdt(902), page_num=2)
    pair = verify_cdtrf_page_pair(first, second)
    assert pair["observed_unique_lots"] == 4
    assert pair["distinct_pages_confirmed"] is True
    assert pair["safe_for_archival"] is False
    with pytest.raises(ApiEvidenceError, match="overlapping"):
        verify_cdtrf_page_pair(first, parse_cdtrf_api_page(cdt(900), page_num=2))


def test_cdtrf_rejects_duplicate_and_broken_category():
    b = cdt()
    b["items"].append(copy.deepcopy(b["items"][0]))
    with pytest.raises(ApiEvidenceError, match="Duplicate"):
        parse_cdtrf_api_page(b, page_num=1)
    b = cdt(); b["items"][0]["categories"] = ",abc,"
    with pytest.raises(ApiEvidenceError, match="category"):
        parse_cdtrf_api_page(b, page_num=1)


def test_sber_property_only_strict_and_no_personal_data():
    a = parse_sber_bankruptcy_api_page(sber())
    assert a["reported_total"] == 375
    assert len(a["property_candidates"]) == 1
    row = a["property_candidates"][0]
    assert row["external_id"] == "sber_ast_goods:101"
    assert row["source_lot_id"] == "201"
    assert row["cadastral_numbers"] == ["47:16:0420001:37"]
    assert row["current_price_rub"] is None
    assert "PERSONAL" not in str(a)
    assert a["complete_source_inventory"] is False


def test_sber_rejects_unfiltered_and_external_redirects():
    a = sber(); a["PurchaseList"][0]["_source"]["ObjectSource"] = "Приватизация"
    with pytest.raises(ApiEvidenceError, match="Unfiltered"):
        parse_sber_bankruptcy_api_page(a)
    a = sber(); a["PurchaseList"][0]["_source"]["objectHref"] = "https://evil.example/lot"
    with pytest.raises(ApiEvidenceError, match="Unsafe"):
        parse_sber_bankruptcy_api_page(a)


def test_sber_disallows_empty_or_oversized_pages():
    a = sber(); a["Total"] = 1
    with pytest.raises(ApiEvidenceError):
        parse_sber_bankruptcy_api_page(a)
