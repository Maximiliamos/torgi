"""Bounded read-only UTPL source-contract probe.

Modes:
  python scripts/probe_utpl_readonly.py --catalogue --passes 2 --details 30
  python scripts/probe_utpl_readonly.py --url <canonical-lot-url> [--url ...]

The probe performs only public GET/ASP.NET pagination POST requests already
advertised by the provider HTML. It never logs in, bypasses challenges, writes
application data, reconciles missing lots, archives lots, or publishes output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bankrotai.connectors.etp_sources import PROFILES, parse_detail_html, parse_listing_html  # noqa: E402
from bankrotai.connectors.etp_sources.utpl_contract import (  # noqa: E402
    normalize_utpl_status,
    parse_utpl_detail_fields,
    utpl_next_postback,
)

PROFILE = next(item for item in PROFILES if item.source_id == "utpl.ru")
CATALOGUE_URL = "https://bankrupt.utpl.ru/public/purchases-all/"
URL_PATH = re.compile(r"/public/(?:auctions|contests|public-offers)/lots/view/([0-9]+)/?\Z")
PROCEDURE = re.compile(
    r"Информация (?:об аукционе|о конкурсе|о публичном предложении)\s*№\s*0*(\d+)",
    re.I,
)


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers["User-Agent"] = "BankrotAI-UTPL-readonly-contract-audit/2.0"
    return session


def _check_provider_url(url: str, *, catalogue: bool = False) -> str:
    part = urlsplit(url)
    if (
        part.scheme != "https"
        or part.hostname != "bankrupt.utpl.ru"
        or part.port is not None
        or part.username is not None
        or part.password is not None
        or part.query
        or part.fragment
    ):
        raise ValueError("Only canonical HTTPS UTPL URLs are permitted")
    if catalogue:
        if part.path != "/public/purchases-all/":
            raise ValueError("Unexpected UTPL catalogue path")
        return "catalogue"
    match = URL_PATH.fullmatch(part.path)
    if match is None:
        raise ValueError("Only canonical UTPL individual-lot URLs are permitted")
    return match.group(1)


def check_url(url: str) -> str:
    return _check_provider_url(url)


def _response_ok(response: requests.Response) -> None:
    if response.status_code in {401, 403, 429}:
        raise RuntimeError(f"provider_access_blocked_{response.status_code}")
    if 300 <= response.status_code < 400:
        raise RuntimeError("provider_redirect_refused")
    response.raise_for_status()
    _check_provider_url(response.url, catalogue=response.url.rstrip("/") == CATALOGUE_URL.rstrip("/"))


def _post_next(
    session: requests.Session,
    response: requests.Response,
    *,
    page: int,
    delay: float,
) -> requests.Response | None:
    postback = utpl_next_postback(response.content, page)
    if postback is None:
        return None
    soup = BeautifulSoup(response.content, "html.parser")
    # ASP.NET posts back to the exact catalogue document we just verified.
    # Do not trust or invent a different action path.
    action = response.url
    _check_provider_url(action, catalogue=True)
    data = {
        str(node.get("name")): str(node.get("value") or "")
        for node in soup.select("input[type=hidden][name]")
    }
    data["__EVENTTARGET"], data["__EVENTARGUMENT"] = postback
    time.sleep(delay)
    next_response = session.post(
        action,
        data=data,
        timeout=(5, 20),
        allow_redirects=False,
    )
    _response_ok(next_response)
    return next_response


def walk_catalogue(
    session: requests.Session,
    *,
    max_pages: int,
    delay: float,
) -> dict:
    _check_provider_url(CATALOGUE_URL, catalogue=True)
    response = session.get(CATALOGUE_URL, timeout=(5, 20), allow_redirects=False)
    _response_ok(response)

    ids: list[str] = []
    urls: list[str] = []
    seen_ids: set[str] = set()
    page_digests: list[str] = []
    response_digests: set[str] = set()
    page = 1

    while True:
        response_digest = hashlib.sha256(response.content).hexdigest()
        if response_digest in response_digests:
            raise RuntimeError(f"repeated_catalogue_document_page_{page}")
        response_digests.add(response_digest)

        lots = parse_listing_html(PROFILE, response.content, page=page)
        if not lots:
            raise RuntimeError(f"no_verified_lots_page_{page}")
        page_ids = [lot.external_id for lot in lots]
        if len(page_ids) != len(set(page_ids)):
            raise RuntimeError(f"duplicate_id_within_page_{page}")
        overlap = seen_ids.intersection(page_ids)
        if overlap:
            raise RuntimeError(
                "duplicate_id_across_pages_" + ",".join(sorted(overlap)[:5])
            )
        seen_ids.update(page_ids)
        ids.extend(page_ids)
        urls.extend(lot.lot_url for lot in lots)
        page_digests.append(
            hashlib.sha256("\n".join(page_ids).encode("utf-8")).hexdigest()
        )

        next_response = _post_next(session, response, page=page, delay=delay)
        if next_response is None:
            terminal_page = page
            break
        if page >= max_pages:
            raise RuntimeError("max_pages_reached_before_terminal")
        page += 1
        response = next_response

    return {
        "pages": terminal_page,
        "terminal_page": terminal_page,
        "unique_ids": len(seen_ids),
        "ids": sorted(seen_ids),
        "urls": list(dict.fromkeys(urls)),
        "digest": hashlib.sha256(
            "\n".join(sorted(seen_ids)).encode("utf-8")
        ).hexdigest(),
        "page_digests": page_digests,
        "terminal_verified": True,
    }


def _procedure_number(html: str | bytes) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    match = PROCEDURE.search(text)
    return match.group(1) if match else None


def probe_urls(
    urls: list[str],
    *,
    session: requests.Session | None = None,
    delay: float = 0.2,
) -> dict:
    if not 1 <= len(urls) <= 100 or len(set(urls)) != len(urls):
        raise ValueError("Provide 1..100 distinct explicit detail URLs")
    session = session or _session()
    observations = []
    procedures: dict[str, list[str]] = {}

    for index, url in enumerate(urls):
        lot_id = check_url(url)
        if index:
            time.sleep(delay)
        response = session.get(url, timeout=(5, 20), allow_redirects=False)
        try:
            _response_ok(response)
        except Exception as exc:
            observations.append(
                {
                    "detail_id": lot_id,
                    "http_status": response.status_code,
                    "verified": False,
                    "error": type(exc).__name__,
                }
            )
            continue
        try:
            fields = parse_utpl_detail_fields(response.content)
            integrated = parse_detail_html(
                PROFILE,
                response.content,
                detail_url=url,
            )
            procedure = _procedure_number(response.content)
            if procedure:
                procedures.setdefault(procedure, []).append(lot_id)
            status = normalize_utpl_status(fields["source_status_text"])
            observations.append(
                {
                    "detail_id": lot_id,
                    "lot_number": fields["lot_number"],
                    "procedure": procedure,
                    "title_sha256": hashlib.sha256(
                        str(fields["title"]).encode("utf-8")
                    ).hexdigest(),
                    "category": fields["category"],
                    "property_kinds": list(fields["property_kinds"]),
                    "classifier_present": fields["classifier"] is not None,
                    "initial_price": fields["initial_price"],
                    "current_price": fields["current_price"],
                    "status": status,
                    "cadastral_present": fields["cadastral_number"] is not None,
                    "area_present": fields["area"] is not None,
                    "address_present": fields["address"] is not None,
                    "integration_identity": integrated.external_id,
                    "verified": True,
                }
            )
        except Exception as exc:
            observations.append(
                {
                    "detail_id": lot_id,
                    "verified": False,
                    "error": type(exc).__name__,
                }
            )

    parsed = [item for item in observations if item["verified"]]
    multi = {
        procedure: sorted(set(detail_ids))
        for procedure, detail_ids in procedures.items()
        if len(set(detail_ids)) > 1
    }
    return {
        "source": "utpl.ru",
        "kind": "read_only_individual_detail_probe",
        "requested": len(urls),
        "parsed": len(parsed),
        "price_success": sum(item["initial_price"] is not None for item in parsed),
        "status_success": sum(item["status"] != "unknown" for item in parsed),
        "structured_classifier_success": sum(
            item["classifier_present"] for item in parsed
        ),
        "multi_lot_procedures": multi,
        "safe_for_archival": False,
        "production_enabled": False,
        "observations": observations,
    }


def probe_catalogue(
    *,
    passes: int = 2,
    max_pages: int = 500,
    details: int = 30,
    delay: float = 0.2,
) -> dict:
    if not 1 <= passes <= 3:
        raise ValueError("passes must be 1..3")
    if not 1 <= max_pages <= 1000:
        raise ValueError("max_pages must be 1..1000")
    if not 0 <= details <= 100:
        raise ValueError("details must be 0..100")
    if not 0.1 <= delay <= 3.0:
        raise ValueError("delay must be 0.1..3.0")

    walks = []
    for pass_number in range(1, passes + 1):
        if pass_number > 1:
            time.sleep(delay)
        walk = walk_catalogue(_session(), max_pages=max_pages, delay=delay)
        walks.append(
            {
                "pass": pass_number,
                "pages": walk["pages"],
                "terminal_page": walk["terminal_page"],
                "unique_ids": walk["unique_ids"],
                "digest": walk["digest"],
                "page_digests": walk["page_digests"],
            }
        )
        if pass_number == 1:
            first = walk

    two_pass_match = len(walks) >= 2 and len({item["digest"] for item in walks}) == 1
    detail_urls = first["urls"][:details]
    detail_result = (
        probe_urls(detail_urls, session=_session(), delay=delay)
        if detail_urls
        else None
    )

    return {
        "source": "utpl.ru",
        "kind": "read_only_full_catalogue_contract_probe",
        "catalogue_url": CATALOGUE_URL,
        "passes": walks,
        "terminal_verified": all(item["terminal_page"] for item in walks),
        "two_pass_digest_match": two_pass_match if passes >= 2 else None,
        "detail_sample": detail_result,
        "complete_source_inventory": False,
        "safe_for_archival": False,
        "production_enabled": False,
        "database_writes": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", action="store_true")
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=500)
    parser.add_argument("--details", type=int, default=30)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--url", action="append", default=[])
    args = parser.parse_args()

    if args.catalogue:
        result = probe_catalogue(
            passes=args.passes,
            max_pages=args.max_pages,
            details=args.details,
            delay=args.delay,
        )
    elif args.url:
        result = probe_urls(args.url, delay=args.delay)
    else:
        parser.error("choose --catalogue or provide at least one --url")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
