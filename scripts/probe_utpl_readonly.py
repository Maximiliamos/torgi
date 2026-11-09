"""Read-only UTPL individual-lot evidence probe.

Usage:
 python scripts/probe_utpl_readonly.py --url https://bankrupt.utpl.ru/public/public-offers/lots/view/127814/ [...]
Only explicit canonical provider lot URLs are permitted. Outputs redacted field evidence;
does not store HTML, write DB, or crawl linked/unknown endpoints.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bankrotai.connectors.etp_sources.utpl_contract import (  # noqa: E402
    normalize_utpl_status,
    parse_utpl_detail_fields,
)

URL_PATH = re.compile(r"/public/(?:auctions|contests|public-offers)/lots/view/([0-9]+)/?\Z")


def check_url(url: str) -> str:
    part = urlsplit(url)
    if (
        part.scheme != "https" or part.hostname != "bankrupt.utpl.ru"
        or part.port is not None or part.username is not None
        or part.password is not None or part.query or part.fragment
        or not URL_PATH.fullmatch(part.path)
    ):
        raise ValueError("Only HTTPS canonical UTPL individual-lot URLs are permitted")
    return URL_PATH.fullmatch(part.path).group(1)


def probe(urls: list[str]) -> dict:
    if not 1 <= len(urls) <= 100 or len(set(urls)) != len(urls):
        raise ValueError("Provide 1..100 distinct explicit detail URLs")
    session = requests.Session()
    session.trust_env = False
    session.headers["User-Agent"] = "BankrotAI-UTPL-readonly-contract-audit/1.0"
    observations = []
    for url in urls:
        lot_id = check_url(url)
        response = session.get(url, timeout=(5, 15), allow_redirects=False)
        if response.status_code != 200:
            observations.append({"detail_id": lot_id, "http_status": response.status_code, "verified": False})
            continue
        try:
            fields = parse_utpl_detail_fields(response.content)
            observations.append({
                "detail_id": lot_id,
                "lot_number": fields["lot_number"],
                "title_sha256": hashlib.sha256(str(fields["title"]).encode()).hexdigest(),
                "category": fields["category"],
                "classifier_present": fields["classifier"] is not None,
                "initial_price": fields["initial_price"],
                "current_price": fields["current_price"],
                "status": normalize_utpl_status(fields["source_status_text"]),
                "verified": True,
            })
        except ValueError as exc:
            observations.append({"detail_id": lot_id, "verified": False, "error": type(exc).__name__})
    return {
        "source": "utpl.ru",
        "kind": "read_only_explicit_detail_probe",
        "requested": len(urls),
        "parsed": sum(item["verified"] for item in observations),
        "catalogue_terminal_verified": False,
        "complete_source_inventory": False,
        "safe_for_archival": False,
        "production_enabled": False,
        "observations": observations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", action="append", required=True)
    args = parser.parse_args()
    print(json.dumps(probe(args.url), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
