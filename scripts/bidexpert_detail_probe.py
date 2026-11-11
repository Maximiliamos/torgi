"""Bounded read-only probe of public BidExpert lot detail HTML."""
from __future__ import annotations

import json
import re
import sys

import requests
from bs4 import BeautifulSoup

IDS = ("944717", "919738", "1096225")
BASE = "https://bidexpert.ru/bids/lot/?n={}"
CAD = re.compile(r"\b\d{2}:\d{1,2}:\d{5,9}:\d{1,12}\b")
PROPERTY_HINT = re.compile(r"(?:кадастр|адрес|местонахожд|располож)", re.I)


def probe(lot_id: str) -> dict:
    url = BASE.format(lot_id)
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "BankrotAI-readonly-detail-probe/1.0",
                "Accept-Language": "ru-RU,ru;q=0.9",
            },
            timeout=(10, 30),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        response = getattr(exc, "response", None)
        return {
            "lot_id": lot_id,
            "status": "transport_failed",
            "error_type": type(exc).__name__,
            "http_status": getattr(response, "status_code", None),
        }
    soup = BeautifulSoup(response.text, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    evidence = []
    for node in soup.find_all(["div", "td", "th", "p", "span", "li"]):
        text = " ".join(node.get_text(" ", strip=True).split())
        if not text or len(text) > 1200 or not PROPERTY_HINT.search(text):
            continue
        classes = " ".join(str(value) for value in (node.get("class") or []))
        evidence.append({
            "tag": node.name,
            "id": str(node.get("id") or "")[:80],
            "class": classes[:160],
            "text": text[:700],
        })
        if len(evidence) >= 12:
            break
    page_text = " ".join(soup.get_text(" ", strip=True).split())
    return {
        "lot_id": lot_id,
        "http_status": response.status_code,
        "title": title[:300],
        "cadastral_numbers": list(dict.fromkeys(CAD.findall(page_text)))[:10],
        "property_evidence_nodes": evidence,
        "body_bytes": len(response.content),
    }


def main() -> dict:
    return {
        "report": "bidexpert_detail_probe",
        "read_only": True,
        "database_writes": 0,
        "lots": [probe(lot_id) for lot_id in IDS],
    }


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=False, separators=(",", ":")))
    except Exception as exc:
        print(json.dumps({"report": "bidexpert_detail_probe", "error_type": type(exc).__name__}), file=sys.stderr)
        raise
