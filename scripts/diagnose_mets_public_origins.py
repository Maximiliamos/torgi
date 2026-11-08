"""Bounded, PII-free live M-ETS detail origin diagnostics for GitHub-hosted CI.

This compares the old public domain with the official meetings mirror for
exactly one already publicly listed trade/lot. One GET per host, no redirects,
no retries, no credentials, no database access, no source completion claims.
Provider access can differ from the Home Windows host. All outcomes are
diagnostic, never acceptance of the full selected catalogue.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

import requests

from bankrotai.connectors.registry.etp_incremental import PROPERTY_CATEGORIES
from bankrotai.connectors.registry.mets_public import (
    MAX_HTML_BYTES,
    MetsEvidenceError,
    _host_lot_url,
    parse_mets_lot,
)

PUBLIC_ID = "244602-1"  # Known publicly indexed November 2026 apartment listing.
OFFICIAL_HOSTS = ("m-ets.ru", "meetings.m-ets.ru")
HEADERS = {
    "User-Agent": "TORGI-source-quality-pilot/0.1",
    "Accept": "text/html",
}


def probe_origin(
    host: str, *, session: requests.Session,
    lot_id: str = PUBLIC_ID,
) -> dict[str, object]:
    if host not in OFFICIAL_HOSTS:
        raise ValueError("unapproved M-ETS host")
    if lot_id != PUBLIC_ID:
        raise ValueError("non-approved diagnostic lot ID")
    url = f"https://{host}/{lot_id}"
    result: dict[str, object] = {
        "host": host, "request_count": 1, "lot_id": lot_id,
        "http_status": None, "redirect_to_official_same_id": False,
        "schema_verified": False, "category_verified": False,
        "cadastral_verified": False, "price_verified": False,
        "status_verified": False, "reason": None,
    }
    try:
        response = session.get(
            url, timeout=(5, 12), allow_redirects=False, headers=HEADERS,
        )
        result["http_status"] = int(response.status_code)
        if response.status_code in {301, 302, 307, 308}:
            location = response.headers.get("Location", "")
            try:
                normalized, _, _ = _host_lot_url(location)
                result["redirect_to_official_same_id"] = (
                    urlsplit(location).netloc == "meetings.m-ets.ru"
                    and normalized == f"https://m-ets.ru/{lot_id}"
                )
            except (ValueError, MetsEvidenceError):
                pass
            result["reason"] = (
                "official_identity_preserving_redirect"
                if result["redirect_to_official_same_id"]
                else "untrusted_redirect"
            )
            return result
        if response.status_code != 200:
            result["reason"] = "http_non_200"
            return result
        if len(response.content) > MAX_HTML_BYTES:
            result["reason"] = "html_size_exceeded"
            return result
        if "text/html" not in response.headers.get("Content-Type", "").lower():
            result["reason"] = "not_html"
            return result
        try:
            lot = parse_mets_lot(response.text, url)
        except (MetsEvidenceError, ValueError):
            result["reason"] = "detail_schema_invalid"
            return result
        result["schema_verified"] = lot.external_id == lot_id
        result["category_verified"] = lot.category in PROPERTY_CATEGORIES
        result["cadastral_verified"] = lot.cadastral_number is not None
        result["price_verified"] = lot.start_price is not None or lot.current_price is not None
        result["status_verified"] = lot.auction_status in {"active", "scheduled"}
        result["reason"] = "detail_parsed"
    except requests.RequestException as exc:
        # No exception message or raw response: these may include sensitive URLs.
        result["reason"] = type(exc).__name__
    return result


def main() -> int:
    with requests.Session() as session:
        rows = [probe_origin(host, session=session) for host in OFFICIAL_HOSTS]
    print(json.dumps({
        "source": "m-ets.ru",
        "scope": "one-known-public-lot-two-official-hosts",
        "provider_writes": 0, "database_writes": 0, "s3_writes": 0,
        "map_publication": False, "safe_for_archival": False,
        "complete_source_inventory": False, "diagnostic_only": True,
        "origins": rows,
    }, ensure_ascii=False, sort_keys=True))
    return 0  # Network blocking is diagnostic evidence, not a broken CI test.


if __name__ == "__main__":
    raise SystemExit(main())
