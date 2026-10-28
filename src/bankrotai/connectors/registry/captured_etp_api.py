"""Fail-closed parsing of public catalogue API evidence (read-only pilots).

Grounded in browser captures dated 2026-10-29. The payloads can include
personal information, so this module emits only lot-level fields. No network,
DB, archive, Celery or S3 operations are performed. The extracted rows are
unverified candidates, never an authoritative complete inventory.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

CDTRF_PROPERTY_CATEGORY_IDS = frozenset({21, 22, 23, 31, 32, 33, 34, 41})
SBER_BANKRUPTCY_SECTION = "Продажа имущества (предприятия) банкротов"
SBER_GOODS_PATH = re.compile(r"^/Catalog/NBT/GoodsView/([1-9]\d{0,2})/0/0/([1-9]\d{1,12})/?$")
CADASTRE = re.compile(r"(?<!\d)\d{2}:\d{1,3}:\d{5,9}:\d{1,9}(?!\d)")


class ApiEvidenceError(ValueError):
    """The saved public API body fails to support a reliable observation."""


def _positive_id(value: object) -> str:
    if isinstance(value, bool) or not re.fullmatch(r"[1-9]\d{0,15}", str(value)):
        raise ApiEvidenceError("Missing or invalid numeric source identifier")
    return str(value)


def _rub(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    s = str(value).strip().replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"\d{1,14}(?:\.\d{1,2})?", s):
        return None
    try:
        n = Decimal(s)
        return str(n.quantize(Decimal("0.01"))) if n > 0 else None
    except InvalidOperation:
        return None


def _page(body: object, array_key: str) -> tuple[list, int]:
    if not isinstance(body, dict) or not isinstance(body.get(array_key), list):
        raise ApiEvidenceError("Expected a catalogue JSON object with an item array")
    items = body[array_key]
    total = body.get("totalCount") if array_key == "items" else body.get("Total")
    if (isinstance(total, bool) or not isinstance(total, int) or total < 0
            or len(items) > 100 or len(items) > total):
        raise ApiEvidenceError("Invalid or oversized page/total count")
    return items, total


def parse_cdtrf_api_page(body: object, *, page_num: int, page_size: int = 20) -> dict:
    """Parse GET /api/Trade/trades; name is a *debtor*, not a property title.

    Category IDs are from the separate /api/Catalog/LotCategory public response.
    A category match is a hint only; final property verification needs detail.
    """
    if not 1 <= page_num <= 100000 or not 1 <= page_size <= 100:
        raise ValueError("Invalid CDTRF page number/size")
    items, total = _page(body, "items")
    if body.get("success") is not True or len(items) > page_size:
        raise ApiEvidenceError("CDTRF API reported failure or oversized page")
    seen: set[str] = set()
    candidates = []
    for item in items:
        if not isinstance(item, dict):
            raise ApiEvidenceError("Non-object CDTRF row")
        trade_id = _positive_id(item.get("tradeId"))
        lot_id = _positive_id(item.get("tradeLotId"))
        if lot_id in seen:
            raise ApiEvidenceError("Duplicate CDTRF lot ID on a page")
        seen.add(lot_id)
        categories = item.get("categories")
        if not isinstance(categories, str) or not re.fullmatch(r"(?:,\d+)+,?", categories):
            raise ApiEvidenceError("Malformed CDTRF category IDs")
        category_ids = {int(p) for p in categories.strip(",").split(",")}
        if not (category_ids & CDTRF_PROPERTY_CATEGORY_IDS):
            continue
        candidates.append({
            "source": "cdtrf.ru", "trade_id": trade_id, "trade_lot_id": lot_id,
            "external_id": f"cdtrf:{lot_id}",
            "detail_url": f"https://torgi.cdtrf.ru/trades/{trade_id}",
            "property_category_ids": sorted(category_ids & CDTRF_PROPERTY_CATEGORY_IDS),
            "title": None, "title_unverified": True,
            "region_id": item.get("regionId") if type(item.get("regionId")) is int else None,
            "source_status_text": str(item.get("tradeStatusDescription") or "")[:120],
            "listing_price_rub": _rub(item.get("actualPrice")),
            "current_price_rub": None,
            "evidence": "public_api_catalogue_only", "verified_detail": False,
            "ready_for_ingestion": False, "safe_for_archival": False,
        })
    return {
        "source": "cdtrf.ru", "page_num": page_num, "page_size": page_size,
        "reported_total": total, "observed_rows": len(items),
        "observed_lot_ids": sorted(seen), "property_candidates": candidates,
        "complete_source_inventory": False, "safe_for_archival": False,
    }


def verify_cdtrf_page_pair(first: dict, second: dict) -> dict:
    """Prove that two captured pages contain distinct lot IDs, not full coverage."""
    if first.get("page_num") != 1 or second.get("page_num") != 2:
        raise ApiEvidenceError("Expected CDTRF page 1 followed by page 2")
    if first.get("page_size") != second.get("page_size"):
        raise ApiEvidenceError("CDTRF page size changed")
    a, b = set(first.get("observed_lot_ids", [])), set(second.get("observed_lot_ids", []))
    if not a or not b or a & b:
        raise ApiEvidenceError("Empty or overlapping CDTRF pages")
    return {"distinct_pages_confirmed": True, "observed_unique_lots": len(a | b),
            "reported_total_page_1": first["reported_total"],
            "reported_total_page_2": second["reported_total"],
            "complete_source_inventory": False, "safe_for_archival": False}


def parse_sber_bankruptcy_api_page(body: object) -> dict:
    """Parse a filtered /ESGoodsList response, not an unfiltered default response.

    A response is accepted only if every item belongs to bankruptcy section 13;
    non-property items are deliberately omitted. Pagination isn't inferred.
    """
    items, total = _page(body, "PurchaseList")
    seen: set[str] = set()
    candidates = []
    for hit in items:
        if not isinstance(hit, dict) or not isinstance(hit.get("_source"), dict):
            raise ApiEvidenceError("Malformed Sberbank search hit")
        row = hit["_source"]
        if (row.get("ObjectSource") != SBER_BANKRUPTCY_SECTION
                or str(row.get("SourceTradeSectionId")) != "13"):
            raise ApiEvidenceError("Unfiltered or wrong-section Sberbank results")
        goods_id = _positive_id(row.get("Goods_id"))
        source_lot_id = _positive_id(row.get("SourceObjectId"))
        if goods_id in seen:
            raise ApiEvidenceError("Duplicate Sberbank Goods_id")
        seen.add(goods_id)
        if row.get("BranchGroup") != "Недвижимое имущество":
            continue
        url = str(row.get("objectHref") or "")
        parsed = urlsplit(url)
        path = SBER_GOODS_PATH.fullmatch(parsed.path)
        if (parsed.scheme != "https" or parsed.netloc != "utp.sberbank-ast.ru"
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or path is None or path.group(2) != goods_id):
            raise ApiEvidenceError("Unsafe or inconsistent Sberbank goods URL")
        title = str(row.get("title") or row.get("BidName") or "").strip()
        if not title:
            raise ApiEvidenceError("Empty property description")
        cad = list(dict.fromkeys(CADASTRE.findall(title)))
        candidates.append({
            "source": "sberbank-ast.ru", "goods_id": goods_id,
            "source_lot_id": source_lot_id,
            "external_id": f"sber_ast_goods:{goods_id}",
            "detail_url": url, "title": title[:600],
            "cadastral_numbers": cad,
            "source_status_text": str(row.get("PurchaseStageTerm") or "")[:120],
            "listing_price_rub": _rub(row.get("purchAmountRUB")),
            "current_price_rub": None,
            "verified_detail": False, "ready_for_ingestion": False,
            "safe_for_archival": False,
        })
    return {
        "source": "sberbank-ast.ru", "reported_total": total,
        "observed_rows": len(items), "property_candidates": candidates,
        "filtered_bankruptcy_section": True,
        "complete_source_inventory": False, "safe_for_archival": False,
    }
