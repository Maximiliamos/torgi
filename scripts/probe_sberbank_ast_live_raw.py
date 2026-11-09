"""Standalone read-only live evidence probe for Sberbank-AST bankruptcy catalogue.

Pipe into the trusted Home worker. No BankrotAI imports, DB access, auth, or provider writes.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import defaultdict
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
TIMEOUT = 30
GOODS_RE = re.compile(
    r"^https://utp\.sberbank-ast\.ru/Catalog/NBT/GoodsView/(\d+)/(\d+)/(\d+)/(\d+)/?$"
)
SBER_CODE_RE = re.compile(r"^(SBR\d+-[A-ZА-Я0-9-]+?)(?:\.(\d+))?$", re.I)
CADASTRE_RE = re.compile(r"(?<!\d)\d{2}:\d{2}(?::\d{1,7}){2,4}(?!\d)")
AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", re.I)
MONEY_RE = re.compile(
    r"(начальн\w*\s+цен\w*|текущ\w*\s+цен\w*)[^\d]{0,60}"
    r"(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:\.|лей)?)",
    re.I,
)
DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b")


def _split_code(value: object) -> tuple[str | None, str | None, str | None]:
    raw = str(value or "").strip()
    if not raw:
        return None, None, None
    match = SBER_CODE_RE.fullmatch(raw)
    if not match:
        return raw, raw, None
    return raw, match.group(1), match.group(2)


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


def _page(session: requests.Session, page_index: int) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]], Any]:
    response = session.post(API_URL, json=_payload(page_index), timeout=TIMEOUT, allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError(f"api_http_{response.status_code}")
    data = response.json()
    total = int(data.get("Total"))
    items = data.get("PurchaseList")
    if total <= 0 or not isinstance(items, list):
        raise RuntimeError("api_schema_invalid")
    if len(items) > PAGE_SIZE:
        raise RuntimeError("purchase_list_exceeds_requested_page_size")

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
        if not match or urlsplit(href).query or urlsplit(href).fragment:
            raise RuntimeError("real_estate_row_without_verified_goodsview")
        raw_code, procedure, lot_number = _split_code(raw.get("purchCode"))
        accepted.append({
            "identity": "-".join(match.groups()),
            "href": href,
            "source_lot_code": raw_code,
            "procedure": procedure,
            "lot_number": lot_number,
            "price": _safe_amount(raw),
            "status": str(raw.get("PurchaseStageTerm") or raw.get("purchStateNameTerm") or "").strip() or None,
            "request_stop": str(raw.get("RequestStopDate") or "").strip() or None,
            "has_address": bool(str(raw.get("Address") or "").strip()),
            "has_cadastre": bool(
                CADASTRE_RE.search(
                    str(raw.get("cadastralNumber") or "") + " " + str(raw.get("title") or "")
                )
            ),
        })
    ids = [row["identity"] for row in accepted]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate_goodsview_identity_on_page")
    return total, items, accepted, data.get("BranchGroup")


def _detail(session: requests.Session, row: dict[str, Any]) -> dict[str, Any]:
    response = session.get(row["href"], timeout=TIMEOUT, allow_redirects=False)
    result: dict[str, Any] = {
        "identity": row["identity"],
        "procedure": row["procedure"],
        "lot_number": row["lot_number"],
        "source_lot_code": row["source_lot_code"],
        "catalogue_price": row["price"],
        "catalogue_status": row["status"],
        "http_status": response.status_code,
        "url_sha256": hashlib.sha256(row["href"].encode()).hexdigest(),
    }
    if response.status_code != 200:
        return {**result, "verified": False, "reason": f"http_{response.status_code}"}
    if len(response.content) > 3_000_000:
        return {**result, "verified": False, "reason": "oversized_detail"}

    text = " ".join(BeautifulSoup(response.content, "html.parser").stripped_strings)
    if len(text) < 250:
        return {**result, "verified": False, "reason": "generic_or_empty_shell"}

    money: dict[str, float] = {}
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
    return {
        **result,
        "verified": bool(
            CADASTRE_RE.search(text) or AREA_RE.search(text) or money or re.search(r"лот\s*№", text, re.I)
        ),
        "has_cadastre": bool(CADASTRE_RE.search(text)),
        "has_area": bool(AREA_RE.search(text)),
        "initial_price": money.get("initial"),
        "current_price": money.get("current"),
        "status_markers": statuses[:4],
        "date_count": len(DATE_RE.findall(text)),
        "has_application_dates": bool(re.search(r"(?:прием|приём|представлен)\w*\s+заяв", text, re.I)),
        "has_auction_date": bool(re.search(r"дата\s+(?:проведения|торгов|аукциона)", text, re.I)),
    }


def _walk_all(
    session: requests.Session,
    page_count: int,
    expected_total: int,
    *,
    retain_rows: bool,
) -> tuple[str, int, dict[str, dict[str, Any]], dict[str, Any]]:
    ordered_page_signatures: list[str] = []
    all_rows: dict[str, dict[str, Any]] = {}
    raw_count = 0
    terminal: dict[str, Any] = {}
    for page_index in range(page_count):
        total, items, rows, branch_group = _page(session, page_index)
        if total != expected_total:
            raise RuntimeError(f"api_total_changed_{expected_total}_to_{total}")
        raw_count += len(items)
        ids = [row["identity"] for row in rows]
        page_signature = hashlib.sha256("\n".join(ids).encode()).hexdigest()
        ordered_page_signatures.append(f"{page_index + 1}:{page_signature}:{len(items)}:{len(ids)}")
        if retain_rows:
            for row in rows:
                if row["identity"] in all_rows:
                    raise RuntimeError("duplicate_goodsview_identity_across_pages")
                all_rows[row["identity"]] = row
        if page_index == page_count - 1:
            terminal = {
                "page": page_index + 1,
                "raw_rows": len(items),
                "property_rows": len(rows),
                "branch_group_present": branch_group is not None,
            }
        time.sleep(0.12)
    digest = hashlib.sha256("\n".join(ordered_page_signatures).encode()).hexdigest()
    return digest, raw_count, all_rows, terminal


def main() -> int:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "TORGI-Sberbank-AST-readonly-audit/1.0 (+https://github.com/Maximiliamos/torgi)",
        "Accept": "application/json,text/plain,*/*",
    })

    total, first_items, first_property, branch_group = _page(session, 0)
    if total >= DISPLAY_CAP:
        # Probe the first two pages only; the cap is explicitly NOT a source end.
        page2_total, page2_items, page2_property, _ = _page(session, 1)
        if len(first_items) != PAGE_SIZE or len(page2_items) != PAGE_SIZE:
            raise RuntimeError("capped_catalogue_returned_short_page_before_source_end")
        if page2_total != total:
            raise RuntimeError(f"api_total_changed_{total}_to_{page2_total}")
        result = {
            "source": "sberbank-ast.ru",
            "read_only": True,
            "database_writes": 0,
            "production_changes": 0,
            "bankruptcy_filter": {"requested": BANKRUPTCY, "returned_rows_verified": True, "source_trade_section_id": SECTION_ID},
            "property_scope": {"structured_branch": REAL_ESTATE, "free_text_fallback_used": False},
            "api_total": total,
            "display_cap": DISPLAY_CAP,
            "completeness": "NOT_PROVEN_AT_DISPLAY_CAP",
            "terminal_page": None,
            "proof_pages": [1, 2],
            "page1_raw_rows": len(first_items),
            "page2_raw_rows": len(page2_items),
            "page1_property_rows": len(first_property),
            "page2_property_rows": len(page2_property),
            "branch_group_aggregation_present": branch_group is not None,
            "second_page_total_matches": page2_total == total,
        }
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 4

    page_count = math.ceil(total / PAGE_SIZE)
    digest1, raw_count1, discovered, terminal1 = _walk_all(
        session, page_count, total, retain_rows=True
    )
    digest2, raw_count2, _, terminal2 = _walk_all(
        session, page_count, total, retain_rows=False
    )
    expected_terminal_rows = total - (page_count - 1) * PAGE_SIZE
    if raw_count1 != total or raw_count2 != total:
        raise RuntimeError(
            f"full_walk_raw_row_count_mismatch_expected_{total}_got_{raw_count1}_{raw_count2}"
        )
    if terminal1.get("raw_rows") != expected_terminal_rows or terminal2.get("raw_rows") != expected_terminal_rows:
        raise RuntimeError("terminal_page_row_count_conflicts_with_api_total")
    stable = digest1 == digest2 and raw_count1 == raw_count2 and terminal1 == terminal2

    detail_rows = list(discovered.values())[:DETAIL_TARGET]
    details = []
    for row in detail_rows:
        details.append(_detail(session, row))
        time.sleep(0.18)
    verified_details = [row for row in details if row.get("verified")]

    procedure_ids: dict[str, list[str]] = defaultdict(list)
    prices_by_procedure: dict[str, list[float | None]] = defaultdict(list)
    lot_numbers_by_procedure: dict[str, list[str | None]] = defaultdict(list)
    for row in discovered.values():
        if row["procedure"]:
            procedure_ids[row["procedure"]].append(row["identity"])
            prices_by_procedure[row["procedure"]].append(row["price"])
            lot_numbers_by_procedure[row["procedure"]].append(row["lot_number"])

    multi = []
    for procedure, ids in procedure_ids.items():
        if len(set(ids)) > 1:
            multi.append({
                "procedure": procedure,
                "lot_count_observed": len(set(ids)),
                "distinct_price_count": len({p for p in prices_by_procedure[procedure] if p is not None}),
                "lot_numbers": sorted({n for n in lot_numbers_by_procedure[procedure] if n is not None})[:20],
            })
        if len(multi) >= 10:
            break

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
            "branch_group_aggregation_present": branch_group is not None,
        },
        "api_total": total,
        "display_cap": DISPLAY_CAP,
        "completeness": "PROVEN_BELOW_CAP",
        "page_count": page_count,
        "terminal_page": terminal1["page"],
        "terminal_page_raw_rows": terminal1["raw_rows"],
        "terminal_page_expected_raw_rows": expected_terminal_rows,
        "terminal_page_property_rows": terminal1["property_rows"],
        "first_walk_raw_rows": raw_count1,
        "second_walk_raw_rows": raw_count2,
        "first_walk_digest": digest1,
        "second_walk_digest": digest2,
        "two_full_walks_stable": stable,
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
    if len(discovered) >= DETAIL_TARGET and len(verified_details) < DETAIL_TARGET:
        return 5
    if len(discovered) < DETAIL_TARGET and len(verified_details) != len(discovered):
        return 6
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
