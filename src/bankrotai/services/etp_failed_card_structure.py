"""Minimal read-only structural evidence for 17 failed NISTP/CDTRF details.

Read only exact public cards from a validated prior QA JSON report. The output
contains no debtor names, descriptions, prices, raw HTML or provider text.
Do not use these probes to authorize imports, archival, GEO or map publication.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag

from bankrotai.connectors.etp_sources.nistp_detail_sections import NistpCardError
from bankrotai.connectors.registry.cdtrf_public import (
    CdtrfEvidenceError, _ssr_lot, _text, money,
)
from bankrotai.services.etp_cdtrf_canary import API, REQUEST_TIMEOUT
from bankrotai.services.etp_two_source_detail_qa import _cdtrf_page

NISTP_ID = re.compile(r"nistp\.ru:([1-9]\d{0,11}):([1-9]\d{0,4})\Z")
CDTRF_ID = re.compile(r"cdtrf:([1-9]\d{2,11})\Z")
TABLE = re.compile(r"table_lot_([1-9]\d{0,4})\Z")
TRADE_CODE = re.compile(r"Код торгов:\s*(\d+)")
HTML_CAP = 12_000_000
USER_AGENT = "TORGI-readonly-detail-structure/1"


def previous_failures(path: Path, source: str, last_page: int) -> list[dict[str, Any]]:
    """Fail closed on wrong source, scope, identity, duplicate or huge report."""
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("Prior diagnostic JSON missing or oversized")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if (not isinstance(data, dict) or data.get("source") != source
            or data.get("mode") != "bounded_independent_readonly_lot_detail_sample"
            or data.get("last_page_from_previous_full_walk") != last_page
            or data.get("source_accepted") is not False):
        raise ValueError("Untrusted prior QA source/scope")
    errors = data.get("errors")
    if not isinstance(errors, list) or not 1 <= len(errors) <= 50:
        raise ValueError("Untrusted prior QA failure count")
    identity = NISTP_ID if source == "nistp.ru" else CDTRF_ID
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in errors:
        if not isinstance(item, dict):
            raise ValueError("Invalid prior QA error entry")
        page, external_id = item.get("page"), item.get("external_id")
        if (type(page) is not int or not 1 <= page <= last_page
                or not isinstance(external_id, str) or not identity.fullmatch(external_id)
                or external_id in seen
                or item.get("stage") != "detail"):
            raise ValueError("Invalid or duplicate prior detail failure identity")
        seen.add(external_id)
        result.append({"page": page, "external_id": external_id})
    return result


def _http_html(session: requests.Session, url: str) -> tuple[BeautifulSoup | None, str | None]:
    try:
        response = session.get(
            url, timeout=REQUEST_TIMEOUT, allow_redirects=False,
            headers={"Accept": "text/html", "User-Agent": USER_AGENT},
        )
    except requests.RequestException as exc:
        return None, "transport_" + type(exc).__name__[:60]
    if response.status_code != 200:
        return None, f"http_{response.status_code}"
    if len(response.content) > HTML_CAP:
        return None, "html_over_cap"
    if "text/html" not in response.headers.get("Content-Type", "").lower():
        return None, "unexpected_content_type"
    return BeautifulSoup(response.content, "html.parser"), None


def _table_fields(table: Tag) -> dict[str, Any]:
    names: list[str] = []
    for row in table.select("tbody > tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) == 2 and "label" in cells[0].get("class", []):
            names.append(re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True)).casefold())
    description = sum(
        1 for name in names
        if re.search(r"[cс]ведения об имуществе", name) and "описание" in name
    )
    descriptions_alternative = sum(
        1 for name in names
        if re.search(r"[cс]ведения об имуществе", name) and "описание" not in name
    )
    return {
        "declared_lot_number_present": "номер лота" in names,
        "asset_title_present": "наименование имущества" in names,
        "initial_price_present": "начальная цена" in names,
        "status_present": "статус торгов" in names,
        "expected_description_labels": description,
        "alternative_description_labels": descriptions_alternative,
        "numbered_table_has_thead": bool(table.select_one("thead th.table_header")),
    }


def nistp_structure(soup: BeautifulSoup, requested_numbers: list[int]) -> dict[str, Any]:
    numbered: dict[int, Tag] = {}
    malformed_table_ids = 0
    for table in soup.select('table[id^="table_lot_"]'):
        m = TABLE.fullmatch(str(table.get("id") or ""))
        if m and int(m.group(1)) not in numbered:
            numbered[int(m.group(1))] = table
        else:
            malformed_table_ids += 1
    ids = sorted(numbered)
    incomplete_other_tables = sum(
        _table_fields(table)["expected_description_labels"] != 1
        for table in numbered.values()
    )
    entries = []
    for number in requested_numbers:
        table = numbered.get(number)
        entries.append({
            "requested_number": number,
            "requested_numbered_table_present": table is not None,
            "fields": _table_fields(table) if table else None,
        })
    return {
        "numbered_table_count": len(ids),
        "lowest_table_number": ids[0] if ids else None,
        "highest_table_number": ids[-1] if ids else None,
        "malformed_or_duplicate_table_ids": malformed_table_ids,
        "tables_without_exact_description_label": incomplete_other_tables,
        "requested_lots": entries,
    }


def cdtrf_structure(soup: BeautifulSoup, trade_id: str, lot_id: str) -> dict[str, Any]:
    header = soup.select_one(".lot__header")
    code = TRADE_CODE.search(_text(header))
    title = _text(soup.select_one(".lot__header h1"))
    try:
        lot, trade = _ssr_lot(soup)
    except (CdtrfEvidenceError, TypeError, KeyError, IndexError, ValueError):
        return {"nuxt_structure_parsed": False, "html_trade_code_matches_api":
                bool(code) and code.group(1) == trade_id}
    nuxt_trade = str(trade.get("tradeId"))
    nuxt_lot = str(lot.get("tradeLotId"))
    nuxt_name = str(lot.get("name") or "").strip()
    html_trade_matches = bool(code) and code.group(1) == trade_id
    ssr_trade_matches = nuxt_trade == trade_id
    ssr_lot_matches = nuxt_lot == lot_id
    name_matches = bool(title and nuxt_name and title == nuxt_name)
    sidebar_price = money(_text(soup.select_one(".lot-sidebar__price-val")))
    nuxt_price = money(str(lot.get("priceBegin") or ""))
    return {
        "nuxt_structure_parsed": True,
        "html_trade_code_present": bool(code),
        "html_trade_code_matches_api": html_trade_matches,
        "nuxt_trade_id_matches_api": ssr_trade_matches,
        "nuxt_lot_id_matches_api": ssr_lot_matches,
        "html_title_present": bool(title),
        "nuxt_lot_name_present": bool(nuxt_name),
        "html_title_matches_nuxt_lot_name": name_matches,
        "html_and_nuxt_price_match": (
            bool(sidebar_price and nuxt_price) and sidebar_price == nuxt_price
        ),
        "title_only_mismatch_if_all_ids_match": (
            html_trade_matches and ssr_trade_matches and ssr_lot_matches
            and bool(title and nuxt_name) and not name_matches
        ),
    }


def scan_nistp(session: requests.Session, failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    for row in failures:
        m = NISTP_ID.fullmatch(row["external_id"])
        assert m is not None
        grouped[m.group(1)].append((row["external_id"], row["page"], int(m.group(2))))
    output = []
    for i, (trade_id, targets) in enumerate(grouped.items()):
        if i:
            time.sleep(1)
        soup, issue = _http_html(
            session, f"https://nistp.ru/bankrot/trade_view.php?trade_nid={trade_id}"
        )
        data = nistp_structure(soup, [number for _, _, number in targets]) if soup else {}
        for external_id, page, number in targets:
            request = next(
                (x for x in data.get("requested_lots", []) if x["requested_number"] == number),
                None,
            )
            output.append({
                "external_id": external_id, "original_page": page,
                "transport_issue": issue,
                "numbered_table_count": data.get("numbered_table_count"),
                "lowest_table_number": data.get("lowest_table_number"),
                "highest_table_number": data.get("highest_table_number"),
                "tables_without_exact_description_label":
                    data.get("tables_without_exact_description_label"),
                "requested_section": request,
            })
        if issue in {"http_401", "http_403", "http_429"}:
            break
    return output


def scan_cdtrf(session: requests.Session, failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for i, item in enumerate(failures):
        if i:
            time.sleep(1)
        row: dict[str, Any] = {
            "external_id": item["external_id"], "original_page": item["page"],
        }
        try:
            data = _cdtrf_page(session, item["page"])
        except (requests.RequestException, ValueError) as exc:
            row["catalogue_issue_class"] = type(exc).__name__
            output.append(row)
            if "HTTP 429" in str(exc) or "HTTP 403" in str(exc):
                break
            continue
        candidates = [
            candidate for candidate in data["property_candidates"]
            if candidate["external_id"] == item["external_id"]
        ]
        if len(candidates) != 1:
            row["prior_lot_still_on_same_catalogue_page"] = False
            output.append(row)
            continue
        row["prior_lot_still_on_same_catalogue_page"] = True
        candidate = candidates[0]
        trade_id = candidate["trade_id"]
        lot_id = candidate["trade_lot_id"]
        soup, issue = _http_html(session, f"https://torgi.cdtrf.ru/trades/{trade_id}")
        row["transport_issue"] = issue
        if soup is not None:
            row.update(cdtrf_structure(soup, trade_id, lot_id))
        output.append(row)
        if issue in {"http_401", "http_403", "http_429"}:
            break
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nistp-report", type=Path, required=True)
    parser.add_argument("--cdtrf-report", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path,
                        default=Path("D:/BankrotAI/etp-evidence/two-source-detail-qa"))
    args = parser.parse_args()
    nistp = previous_failures(args.nistp_report, "nistp.ru", 142)
    cdtrf = previous_failures(args.cdtrf_report, "cdtrf.ru", 64)
    with requests.Session() as nistp_session:
        print("NISTP: checking exact shared trade structures...", flush=True)
        nistp_result = scan_nistp(nistp_session, nistp)
    with requests.Session() as cdtrf_session:
        print("CDTRF: checking independent API/HTML/Nuxt identity...", flush=True)
        cdtrf_result = scan_cdtrf(cdtrf_session, cdtrf)
    report = {
        "mode": "read_only_failed_lot_structure_evidence",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "nistp": nistp_result,
        "cdtrf": cdtrf_result,
        "production_database_writes": 0,
        "source_accepted": False,
        "archival_allowed": False,
        "map_publication": False,
        "source_html_saved": False,
    }
    args.evidence_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = args.evidence_root / f"failed-card-structure-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Structural evidence: {path}", flush=True)
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    if len(nistp_result) != len(nistp) or len(cdtrf_result) != len(cdtrf):
        raise SystemExit("Provider access blocked or diagnostic incomplete")


if __name__ == "__main__":
    main()
