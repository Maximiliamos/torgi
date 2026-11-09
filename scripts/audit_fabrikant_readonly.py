from __future__ import annotations

"""GET-only Fabrikant live source-contract probe.

All source-specific HTML interpretation lives in
bankrotai.connectors.etp_sources.fabrikant.  This script only performs public
GET requests, traverses pages, compares passes, and emits sanitized evidence.
"""

import argparse
import hashlib
import json
import time
from collections import Counter
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from bankrotai.connectors.etp_sources.fabrikant import (
    CATALOGUE_URL,
    PROCEDURE_PATH_RE,
    build_catalogue_url,
    external_id_from_lot_url,
    lot_urls,
    parse_catalogue_page,
    parse_detail_fields,
)


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "TORGI-Fabrikant-Audit/2.0 "
            "(+https://github.com/Maximiliamos/torgi; public read-only audit)"
        )
    })
    return session


def _fetch(
    session: requests.Session, url: str, *, timeout: float
) -> requests.Response:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _procedure_id(url: str) -> str:
    match = PROCEDURE_PATH_RE.search(urlparse(url).path)
    return match.group(1) if match else urlparse(url).path


def _page_record(
    response: requests.Response, *, page: int
) -> dict[str, Any]:
    parsed = parse_catalogue_page(
        response.content,
        base_url=response.url or build_catalogue_url(page),
        page=page,
    )
    ids = [_procedure_id(url) for url in parsed.procedure_urls]
    counts = Counter(ids)
    return {
        "page": page,
        "http_status": response.status_code,
        "response_path": urlparse(response.url or "").path,
        "content_sha256": hashlib.sha256(response.content).hexdigest(),
        "content_bytes": len(response.content),
        "procedure_ids": ids,
        "procedure_count": len(ids),
        "duplicate_ids_on_page": sorted(
            key for key, value in counts.items() if value > 1
        ),
        "visible_page_numbers": list(parsed.visible_page_numbers),
        "has_next_link": parsed.has_next_page,
        "selected_controls": [
            {"name": name, "value": value, "kind": kind}
            for name, value, kind in parsed.selected_controls
        ],
        "total_hints": list(parsed.total_hints),
    }


def _traverse(
    session: requests.Session,
    *,
    max_pages: int,
    timeout: float,
    pause: float,
) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    all_ids: list[str] = []
    all_urls: list[str] = []
    fingerprints: dict[str, int] = {}
    terminal_page: int | None = None
    terminal_reason = "max_pages"

    for page in range(1, max_pages + 1):
        response = _fetch(session, build_catalogue_url(page), timeout=timeout)
        record = _page_record(response, page=page)
        pages.append(record)

        if response.status_code != 200:
            terminal_page = page
            terminal_reason = f"http_{response.status_code}"
            break

        fingerprint = record["content_sha256"]
        prior_page = fingerprints.get(fingerprint)
        if prior_page is not None:
            terminal_page = page - 1 if page > 1 else 1
            terminal_reason = f"repeated_page_{prior_page}"
            break
        fingerprints[fingerprint] = page

        parsed = parse_catalogue_page(
            response.content,
            base_url=response.url or build_catalogue_url(page),
            page=page,
        )
        all_urls.extend(parsed.procedure_urls)
        all_ids.extend(record["procedure_ids"])

        if not parsed.procedure_urls:
            terminal_page = page - 1 if page > 1 else 1
            terminal_reason = "empty_page"
            break
        if not parsed.has_next_page:
            terminal_page = page
            terminal_reason = "no_next_link"
            break
        time.sleep(pause)

    unique_urls = list(dict.fromkeys(all_urls))
    unique_ids = list(dict.fromkeys(all_ids))
    digest = hashlib.sha256(
        "\n".join(unique_ids).encode("utf-8")
    ).hexdigest()
    return {
        "pages": pages,
        "pages_checked": len(pages),
        "terminal_page": terminal_page,
        "terminal_reason": terminal_reason,
        "unique_procedure_ids": unique_ids,
        "unique_procedure_count": len(unique_ids),
        "procedure_urls": unique_urls,
        "duplicates_across_pages": sorted(
            key for key, value in Counter(all_ids).items() if value > 1
        ),
        "id_digest_sha256": digest,
    }


def _resolve_lots(
    session: requests.Session,
    procedure_urls: list[str],
    *,
    timeout: float,
    pause: float,
) -> dict[str, Any]:
    resolved_urls: list[str] = []
    resolved_ids: list[str] = []
    unresolved_ids: list[str] = []

    for procedure_url in procedure_urls:
        response = _fetch(session, procedure_url, timeout=timeout)
        if response.status_code != 200:
            unresolved_ids.append(_procedure_id(procedure_url))
            continue

        urls = lot_urls(
            response.content,
            base_url=response.url or procedure_url,
        )
        if not urls:
            unresolved_ids.append(_procedure_id(procedure_url))
            continue

        for detail_url in urls:
            external_id = external_id_from_lot_url(detail_url)
            if external_id and external_id not in resolved_ids:
                resolved_ids.append(external_id)
                resolved_urls.append(detail_url)
        time.sleep(pause)

    return {
        "unique_lot_ids": resolved_ids,
        "lot_urls": resolved_urls,
        "unique_lot_count": len(resolved_ids),
        "unresolved_procedure_ids": unresolved_ids,
        "unresolved_procedure_count": len(unresolved_ids),
        "lot_id_digest_sha256": hashlib.sha256(
            "\n".join(resolved_ids).encode("utf-8")
        ).hexdigest(),
    }


def _detail_record(
    response: requests.Response,
    *,
    external_id: str,
) -> dict[str, Any]:
    fields = parse_detail_fields(response.content)
    return {
        "external_id": external_id,
        "http_status": response.status_code,
        "response_path": urlparse(response.url or "").path,
        "content_sha256": hashlib.sha256(response.content).hexdigest(),
        "content_bytes": len(response.content),
        "title_present": fields.title is not None,
        "initial_price_present": fields.start_price is not None,
        "current_price_present": fields.current_price is not None,
        "auction_status": (
            "unknown"
            if fields.source_status_text is None
            else (
                "active"
                if "при" in fields.source_status_text.lower()
                else (
                    "paused"
                    if "приостанов" in fields.source_status_text.lower()
                    else (
                        "closed"
                        if any(
                            token in fields.source_status_text.lower()
                            for token in ("закончено", "отказ организатора")
                        )
                        else "unknown"
                    )
                )
            )
        ),
        "category": fields.category,
        "address_present": fields.address is not None,
        "cadastre_present": fields.cadastral_number is not None,
        "area_present": fields.area is not None,
        "application_deadline_present": fields.application_deadline is not None,
        "auction_at_present": fields.auction_at is not None,
    }


def _out_of_range(
    session: requests.Session,
    terminal_page: int | None,
    *,
    timeout: float,
) -> dict[str, Any] | None:
    if terminal_page is None:
        return None
    target = terminal_page + 1
    response = _fetch(session, build_catalogue_url(target), timeout=timeout)
    return _page_record(response, page=target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=200)
    parser.add_argument("--detail-limit", type=int, default=30)
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--pause", type=float, default=0.4)
    args = parser.parse_args()

    if not 1 <= args.max_pages <= 500:
        raise SystemExit("--max-pages must be 1..500")
    if not 0 <= args.detail_limit <= 100:
        raise SystemExit("--detail-limit must be 0..100")
    if not 1 <= args.passes <= 2:
        raise SystemExit("--passes must be 1..2")

    session = _session()
    passes = [
        _traverse(
            session,
            max_pages=args.max_pages,
            timeout=args.timeout,
            pause=args.pause,
        )
        for _ in range(args.passes)
    ]
    first = passes[0]

    resolved = _resolve_lots(
        session,
        first["procedure_urls"],
        timeout=args.timeout,
        pause=args.pause,
    )
    details: list[dict[str, Any]] = []
    for external_id, detail_url in list(zip(
        resolved["unique_lot_ids"],
        resolved["lot_urls"],
        strict=True,
    ))[:args.detail_limit]:
        response = _fetch(session, detail_url, timeout=args.timeout)
        details.append(
            _detail_record(response, external_id=external_id)
        )
        time.sleep(args.pause)

    stable = len(passes) == 2 and (
        passes[0]["unique_procedure_ids"] == passes[1]["unique_procedure_ids"]
        and passes[0]["id_digest_sha256"] == passes[1]["id_digest_sha256"]
        and passes[0]["terminal_page"] == passes[1]["terminal_page"]
    )

    status_success = sum(
        1 for item in details if item["auction_status"] != "unknown"
    )
    initial_price_success = sum(
        1 for item in details if item["initial_price_present"]
    )
    report = {
        "source": "fabrikant.ru",
        "mode": "public_read_only",
        "database_writes": 0,
        "map_writes": 0,
        "production_enabled": False,
        "catalogue_url_path": urlparse(CATALOGUE_URL).path,
        "catalogue_query": parse_qs(urlparse(CATALOGUE_URL).query),
        "passes": [
            {key: value for key, value in item.items() if key != "procedure_urls"}
            for item in passes
        ],
        "stable_two_passes": stable,
        "out_of_range_after_terminal": _out_of_range(
            session, first["terminal_page"], timeout=args.timeout
        ),
        "individual_lots": {
            key: value for key, value in resolved.items() if key != "lot_urls"
        },
        "details_checked": len(details),
        "initial_price_success": initial_price_success,
        "status_success": status_success,
        "details": details,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
