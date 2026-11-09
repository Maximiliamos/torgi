"""Standalone read-only live evidence probe for Sberbank-AST bankruptcy catalogue.

Designed to be piped into the trusted Home worker:
    Get-Content scripts/probe_sberbank_ast_live_raw.py -Raw |
      docker exec -i bankrotai-home-ingestion-worker python -

No BankrotAI imports, no database access, no authentication and no writes to the provider.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from typing import Any
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

API_URL = "https://www.sberbank-ast.ru/api/Processing/main"
BANKRUPTCY = "Продажа имущества (предприятия) банкротов"
SECTION_ID = "13"
REAL_ESTATE = "Недвижимое имущество"
PAGE_SIZE = 21
DISPLAY_CAP = 10_000
DETAIL_TARGET = 30
MAX_DISCOVERY_PAGES = 80
TIMEOUT = 30
GOODS_RE = re.compile(
    r"^https://utp\.sberbank-ast\.ru/Catalog/NBT/GoodsView/(\d+)/(\d+)/(\d+)/(\d+)/?$"
)
CADASTRE_RE = re.compile(r"(?<!\d)\d{2}:\d{2}(?::\d{1,7}){2,4}(?!\d)")
AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", re.I)
MONEY_RE = re.compile(
    r"(начальн\w*\s+цен\w*|текущ\w*\s+цен\w*)[^\d]{0,60}"
    r"(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:\.|лей)?)",
    re.I,
)
DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b")


def _filter_body(page_index: int) -> str:
    return (
        "<query><targetPageCode>UtpESGoodsList</targetPageCode><searchbyorg></searchbyorg>"
        f"<pagesize>{PAGE_SIZE}</pagesize><pagenum>{page_index}</pagenum><sortType></sortType>"
        f"<sortOrder>desc</sortOrder><sourceTerm>{BANKRUPTCY}</sourceTerm>"
        "<searchBarType>anyWord</searchBarType><saleType></saleType><org></org>"
        "<cadastralNumber></cadastralNumber><isOnMapTerm></isOnMapTerm>"
        "<hasRequest></hasRequest><hasDeposit></hasDeposit><orgInnKpp></orgInnKpp></query>"
    )


def _payload(page_index: int) -> dict[str, Any]:
    return {
        "windowCode": "/ESGoodsList",
        "actionType": "MONITOR",
        "actionCode": "default",
        "documentBody": "",
        "parm": "es",
        "filterBody": _filter_body(page_index),
        "options": {},
    }


def _safe_amount(raw: dict[str, Any]) -> float | None:
    value = raw.get("purchAmountRUB")
    currency = str(raw.get("purchCurrency") or raw.get("CurrencyCode") or "").strip().upper()
    if value is None and currency in {"RUB", "643"}:
        value = raw.get("purchAmount")
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        amount = float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    return amount if 0 < amount < 1e15 else None


def _page(session: requests.Session, page_index: int) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    response = session.post(API_URL, json=_payload(page_index), timeout=TIMEOUT, allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError(f"api_http_{response.status_code}")
    if "application/json" not in response.headers.get("content-type", "").lower():
        raise RuntimeError("api_non_json_response")
    data = response.json()
    total = int(data.get("Total"))
    items = data.get("PurchaseList")
    if total <= 0 or not isinstance(items, list):
        raise RuntimeError("api_schema_invalid")

    accepted: list[dict[str, Any]] = []
    for item in items:
        raw = item.get("_source") if isinstance(item, dict) else None
        if not isinstance(raw, dict):
            raise RuntimeError("malformed_purchase_row")
        if str(raw.get("ObjectSource") or "").strip() != BANKRUPTCY:
            raise RuntimeError("bankruptcy_source_not_confirmed")
        if str(raw.get("SourceTradeSectionId") or "").strip() != SECTION_ID:
            raise RuntimeError("bankruptcy_section_not_confirmed")
        if str(raw.get("BranchGroup") or "").strip() != REAL_ESTATE:
            continue
        href = str(raw.get("objectHref") or "").strip()
        match = GOODS_RE.fullmatch(href)
        if not match:
            raise RuntimeError("real_estate_row_without_goodsview")
        if urlsplit(href).query or urlsplit(href).fragment:
            raise RuntimeError("goodsview_url_has_unexpected_query")
        accepted.append({
            "identity": "-".join(match.groups()),
            "href": href,
            "procedure": str(raw.get("purchCode") or "").strip() or None,
            "price": _safe_amount(raw),
            "status": str(raw.get("PurchaseStageTerm") or raw.get("purchStateNameTerm") or "").strip() or None,
            "request_stop": str(raw.get("RequestStopDate") or "").strip() or None,
            "has_address": bool(str(raw.get("Address") or "").strip()),
            "has_cadastre": bool(CADASTRE_RE.search(str(raw.get("cadastralNumber") or "") + " " + str(raw.get("title") or ""))),
            "branch": REAL_ESTATE,
        })
    ids = [row["identity"] for row in accepted]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate_goodsview_identity_on_page")
    return total, items, accepted


def _detail(session: requests.Session, row: dict[str, Any]) -> dict[str, Any]:
    response = session.get(row["href"], timeout=TIMEOUT, allow_redirects=False)
    result: dict[str, Any] = {
        "identity": row["identity"],
        "procedure": row["procedure"],
        "catalogue_price": row["price"],
        "catalogue_status": row["status"],
        "http_status": response.status_code,
        "url_sha256": hashlib.sha256(row["href"].encode()).hexdigest(),
    }
    if response.status_code != 200:
        result["verified"] = False
        result["reason"] = f"http_{response.status_code}"
        return result
    if len(response.content) > 3_000_000:
        result["verified"] = False
        result["reason"] = "oversized_detail"
        return result
    soup = BeautifulSoup(response.content, "html.parser")
    text = " ".join(soup.stripped_strings)
    if len(text) < 250:
        result["verified"] = False
        result["reason"] = "generic_or_empty_shell"
        return result
    money = {}
    for label, amount in MONEY_RE.findall(text):
        kind = "current" if "текущ" in label.lower() else "initial"
        try:
            money[kind] = float(amount.replace("\xa0", "").replace(" ", "").replace(",", "."))
        except ValueError:
            pass
    statuses = [
        marker for marker in (
            "прием заявок", "приём заявок", "торги объявлены", "идут торги",
            "торги завершены", "торги не состоялись", "приостанов",
        ) if marker in text.lower()
    ]
    result.update({
        "verified": bool(
            CADASTRE_RE.search(text)
            or AREA_RE.search(text)
            or money
            or re.search(r"лот\s*№", text, re.I)
        ),
        "has_cadastre": bool(CADASTRE_RE.search(text)),
        "has_area": bool(AREA_RE.search(text)),
        "initial_price": money.get("initial"),
        "current_price": money.get("current"),
        "status_markers": statuses[:4],
        "date_count": len(DATE_RE.findall(text)),
        "has_application_dates": bool(re.search(r"(?:прием|приём|представлен)\w*\s+заяв", text, re.I)),
        "has_auction_date": bool(re.search(r"дата\s+(?:проведения|торгов|аукциона)", text, re.I)),
    })
    return result


def _snapshot(session: requests.Session, page_indexes: list[int]) -> dict[str, Any]:
    pages: dict[str, Any] = {}
    for idx in page_indexes:
        total, raw_items, accepted = _page(session, idx)
        pages[str(idx + 1)] = {
            "total": total,
            "raw_rows": len(raw_items),
            "property_rows": len(accepted),
            "ids": [row["identity"] for row in accepted],
        }
        time.sleep(0.15)
    return pages


def main() -> int:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "TORGI-Sberbank-AST-readonly-audit/1.0 (+https://github.com/Maximiliamos/torgi)",
        "Accept": "application/json,text/plain,*/*",
    })

    total, first_raw, first_property = _page(session, 0)
    terminal_index = math.ceil(total / PAGE_SIZE) - 1 if total < DISPLAY_CAP else None
    proof_pages = [0]
    if total > PAGE_SIZE:
        proof_pages.append(1)
    if terminal_index is not None and terminal_index not in proof_pages:
        proof_pages.append(terminal_index)

    first_pass = _snapshot(session, proof_pages)
    second_pass = _snapshot(session, proof_pages)
    stable = all(
        first_pass[key]["ids"] == second_pass[key]["ids"]
        for key in first_pass
    )

    discovered: dict[str, dict[str, Any]] = {row["identity"]: row for row in first_property}
    scanned_pages = 1
    max_page = min(
        math.ceil(total / PAGE_SIZE) if total < DISPLAY_CAP else MAX_DISCOVERY_PAGES,
        MAX_DISCOVERY_PAGES,
    )
    for page_index in range(1, max_page):
        if len(discovered) >= DETAIL_TARGET:
            break
        page_total, _, rows = _page(session, page_index)
        if page_total != total:
            # Live catalogue can move; keep evidence but do not claim a stable full snapshot.
            stable = False
        for row in rows:
            discovered.setdefault(row["identity"], row)
        scanned_pages += 1
        time.sleep(0.15)

    detail_rows = list(discovered.values())[:DETAIL_TARGET]
    details = []
    for row in detail_rows:
        details.append(_detail(session, row))
        time.sleep(0.2)

    procedure_ids: dict[str, list[str]] = defaultdict(list)
    prices_by_procedure: dict[str, list[float | None]] = defaultdict(list)
    for row in discovered.values():
        if row["procedure"]:
            procedure_ids[row["procedure"]].append(row["identity"])
            prices_by_procedure[row["procedure"]].append(row["price"])
    multi = []
    for procedure, ids in procedure_ids.items():
        unique = list(dict.fromkeys(ids))
        if len(unique) > 1:
            multi.append({
                "procedure": procedure,
                "lot_count_observed": len(unique),
                "distinct_identity_count": len(set(unique)),
                "distinct_price_count": len({p for p in prices_by_procedure[procedure] if p is not None}),
            })
        if len(multi) >= 5:
            break

    verified_details = [row for row in details if row.get("verified")]
    result = {
        "source": "sberbank-ast.ru",
        "read_only": True,
        "database_writes": 0,
        "production_changes": 0,
        "bankruptcy_filter": {
            "requested": BANKRUPTCY,
            "returned_rows_verified": True,
            "source_trade_section_id": SECTION_ID,
        },
        "property_scope": {
            "structured_branch": REAL_ESTATE,
            "free_text_fallback_used": False,
        },
        "api_total": total,
        "display_cap": DISPLAY_CAP,
        "completeness": "PROVEN_BELOW_CAP" if total < DISPLAY_CAP else "NOT_PROVEN_AT_DISPLAY_CAP",
        "terminal_page": terminal_index + 1 if terminal_index is not None else None,
        "proof_pages": [idx + 1 for idx in proof_pages],
        "proof_page_results": first_pass,
        "two_pass_ids_stable": stable,
        "discovery_pages_scanned": scanned_pages,
        "unique_property_goodsview_ids": len(discovered),
        "details_requested": len(detail_rows),
        "details_verified": len(verified_details),
        "detail_initial_price_count": sum(row.get("initial_price") is not None for row in verified_details),
        "detail_current_price_count": sum(row.get("current_price") is not None for row in verified_details),
        "detail_status_count": sum(bool(row.get("status_markers")) for row in verified_details),
        "detail_cadastre_count": sum(bool(row.get("has_cadastre")) for row in verified_details),
        "detail_area_count": sum(bool(row.get("has_area")) for row in verified_details),
        "multi_lot_procedure_examples": multi,
        "details": details,
    }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    if not stable:
        return 3
    if total >= DISPLAY_CAP:
        return 4
    if len(detail_rows) >= DETAIL_TARGET and len(verified_details) < DETAIL_TARGET:
        return 5
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({
            "source": "sberbank-ast.ru",
            "read_only": True,
            "error": type(exc).__name__,
            "message": str(exc)[:500],
        }, ensure_ascii=False, separators=(",", ":")))
        raise
