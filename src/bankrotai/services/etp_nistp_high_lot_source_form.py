"""Strictly read-only source-form discovery for NISTP high numbered lots.

A catalogue search row is NOT an independent individual lot detail. This
diagnostic never promotes any source record to validated inventory, import,
archival, GEO or map status.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from bankrotai.connectors.registry.direct_etp_catalogue import (
    PROFILE_BY_ID, parse_nistp_lot_observations,
)
from bankrotai.connectors.etp_sources.nistp_detail_sections import _TRADE_CODE
from bankrotai.services.etp_nistp_full_catalogue import (
    ACTIVE_SOURCE_STATES, _ACTIVE_STATE_KEYS, _active_filters_are_visible,
)

SOURCE = "nistp.ru"
TRADE_ID = "502379"
HIGH_LOTS = (139, 179, 239, 259, 339, 439, 539)
PAGE = "https://nistp.ru/"
TRADE_URL = f"https://nistp.ru/bankrot/trade_view.php?trade_nid={TRADE_ID}"
TIMEOUT = (10, 30)
BODY_LIMIT = 12_000_000


def search_params(trade_code: str, number: int) -> list[tuple[str, str]]:
    """Official public GET form, with source-chosen active states; no login."""
    if not re.fullmatch(
        r"\d{3,9}-(?:ОАОФ|ОТПП|ЗТПП|ОКОФ|ОКЗФ)", trade_code
    ):
        raise ValueError("Invalid exact NISTP trade code")
    if number not in HIGH_LOTS:
        raise ValueError("Unapproved NISTP lot number")
    result = [
        ("lot_description", ""),
        ("trade_number", trade_code),
        ("lot_number", str(number)),
        ("trade_org", ""), ("arbitr_info", ""), ("debtor_info", ""),
        ("app_start_from", ""), ("app_start_to", ""),
        ("app_end_from", ""), ("app_end_to", ""),
        ("view_type", "lot"),
    ]
    result.extend(("trade_state[]", status) for status in ACTIVE_SOURCE_STATES)
    result.append(("pagenum", "1"))
    return result


def fetch_html(session: requests.Session, url: str, *, params=None) -> bytes:
    if url not in {PAGE, TRADE_URL}:
        raise ValueError("Unapproved provider URL")
    response = session.get(
        url, params=params, timeout=TIMEOUT, allow_redirects=False,
        headers={
            "Accept": "text/html",
            "User-Agent": "TORGI-NISTP-high-lot-source-readonly/1",
        },
    )
    if response.status_code in {401, 403, 429}:
        raise ValueError(f"provider_refused_http_{response.status_code}")
    if response.status_code != 200 or len(response.content) > BODY_LIMIT:
        raise ValueError("provider_status_or_size_unverified")
    if "text/html" not in response.headers.get("Content-Type", "").lower():
        raise ValueError("provider_content_type_unverified")
    return response.content


def extract_trade_code(content: bytes) -> str:
    soup = BeautifulSoup(content, "html.parser")
    codes = {
        match.group(1)
        for head in soup.select("h1.node_header")
        if (match := _TRADE_CODE.search(head.get_text(" ", strip=True)))
    }
    if len(codes) != 1:
        raise ValueError("trade_code_not_uniquely_proven")
    return codes.pop()


def inspect_exact_source_search(
    html: bytes, *, trade_code: str, number: int,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one("form#nodesearch")
    inputs = {}
    if form is not None:
        for inp in form.select("input[name]"):
            name = str(inp.get("name"))
            if name in {"trade_number", "lot_number"}:
                inputs[name] = str(inp.get("value") or "").strip()
    query_echo_proven = (
        inputs.get("trade_number") == trade_code
        and inputs.get("lot_number") == str(number)
    )
    observations = parse_nistp_lot_observations(
        PROFILE_BY_ID[SOURCE], html.decode("utf-8", errors="replace"),
        limit=100,
    )
    expected_id = f"{SOURCE}:{TRADE_ID}:{number}"
    matches = [
        item for item in observations
        if item["provider_external_id"] == expected_id
    ]
    returned_unexpected = [
        item["provider_external_id"] for item in observations
        if item["provider_external_id"] != expected_id
    ]
    status = matches[0]["source_status_text"] if len(matches) == 1 else None
    status_key = (
        status.casefold().replace("ё", "е") if status is not None else None
    )
    price = matches[0]["listing_price_rub"] if len(matches) == 1 else None
    return {
        "external_id": expected_id,
        "form_trade_and_lot_echo_proven": query_echo_proven,
        "selected_active_filters_visible": _active_filters_are_visible(html),
        "returned_rows": len(observations),
        "exact_matching_rows": len(matches),
        "other_ids_returned_count": len(returned_unexpected),
        "exact_row_price_present": (
            type(price) in (int, float) and price > 0
        ),
        "exact_row_in_selected_active_states": status_key in _ACTIVE_STATE_KEYS,
        "exact_search_scope_proven": (
            query_echo_proven and _active_filters_are_visible(html)
            and len(matches) == 1 and not returned_unexpected
            and type(price) in (int, float) and price > 0
            and status_key in _ACTIVE_STATE_KEYS
        ),
        "independent_lot_detail_verified": False,
        "source_accepted": False,
    }


def run_probe(*, session: requests.Session, sleep=time.sleep) -> dict[str, Any]:
    report: dict[str, Any] = {
        "mode": "nistp_exact_high_lot_source_form_readonly_probe",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "trade_id": TRADE_ID,
        "high_lot_numbers": list(HIGH_LOTS),
        "search_rows": [],
        "errors": [],
        "independent_lot_detail_verified": False,
        "full_catalogue_inventory_proven": False,
        "source_accepted": False,
        "production_database_writes": 0,
        "archival_allowed": False,
        "reconcile_missing": False,
        "map_publication": False,
    }
    try:
        trade_code = extract_trade_code(fetch_html(session, TRADE_URL))
    except (ValueError, requests.RequestException) as exc:
        report["errors"].append({
            "stage": "trade_code", "code": type(exc).__name__,
        })
        return report
    report["trade_code_extracted_from_official_trade_page"] = True
    for i, lot in enumerate(HIGH_LOTS):
        if i:
            sleep(1)
        started = time.monotonic()
        try:
            result = inspect_exact_source_search(
                fetch_html(session, PAGE, params=search_params(trade_code, lot)),
                trade_code=trade_code, number=lot,
            )
            report["search_rows"].append(result)
            code = (
                "catalogue_row_only" if result["exact_search_scope_proven"]
                else "unproven_exact_form_query"
            )
        except (ValueError, requests.RequestException) as exc:
            code = type(exc).__name__
            report["errors"].append({
                "external_id": f"{SOURCE}:{TRADE_ID}:{lot}",
                "stage": "source_search", "code": code,
            })
        print(
            f"NISTP exact high lot {lot}: outcome={code} "
            f"elapsed_s={time.monotonic() - started:.1f}",
            flush=True,
        )
    report["catalogue_only_rows_proven"] = sum(
        x["exact_search_scope_proven"] for x in report["search_rows"]
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-root", type=Path,
        default=Path("D:/BankrotAI/etp-evidence/two-source-detail-qa"),
    )
    args = parser.parse_args()
    with requests.Session() as session:
        report = run_probe(session=session)
    args.evidence_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.evidence_root / f"nistp-high-lot-source-form-{stamp}.json"
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"NISTP read-only evidence: {out}", flush=True)
    print(json.dumps({
        "observed": len(report["search_rows"]),
        "catalogue_only_rows_proven": report.get("catalogue_only_rows_proven", 0),
        "errors": report["errors"],
        "independent_lot_detail_verified": False,
        "source_accepted": False, "production_database_writes": 0,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
