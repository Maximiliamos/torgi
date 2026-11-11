"""Bounded read-only probe of the official torgi.gov lot detail endpoint.

No DB access. No writes. Emits only public lot identity and property-address/cadastral
fields needed to prove the detail JSON contract.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

import requests

BASE = "https://torgi.gov.ru/new/api/public/lotcards"
DEFAULT_IDS = (
    "21000004710000027207_1",
    "21000012700000001786_1",
)
SAFE_KEY = re.compile(r"(?:address|location|estate|cadastr|cadastre|lotname|bidnumber|lotnumber|notice)", re.I)


def _safe_fields(value: Any, *, path: str = "", depth: int = 0) -> dict[str, Any]:
    if depth > 5:
        return {}
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            if SAFE_KEY.search(str(key)) and isinstance(item, (str, int, float, bool, type(None))):
                text = str(item) if item is not None else None
                result[child] = text[:500] if isinstance(text, str) else text
            if isinstance(item, (dict, list)):
                result.update(_safe_fields(item, path=child, depth=depth + 1))
    elif isinstance(value, list):
        for index, item in enumerate(value[:20]):
            result.update(_safe_fields(item, path=f"{path}[{index}]", depth=depth + 1))
    return result


def probe(lot_id: str) -> dict[str, Any]:
    url = f"{BASE}/{lot_id}"
    response = requests.get(
        url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "BankrotAI-readonly-detail-probe/1.0",
            "Referer": "https://torgi.gov.ru/new/public/lots/reg",
        },
        timeout=(10, 30),
    )
    response.raise_for_status()
    payload = response.json()
    return {
        "lot_id": lot_id,
        "http_status": response.status_code,
        "top_level_type": type(payload).__name__,
        "top_level_keys": sorted(str(k) for k in payload.keys())[:100] if isinstance(payload, dict) else [],
        "safe_property_fields": _safe_fields(payload),
    }


def main() -> dict[str, Any]:
    raw = os.getenv("TORGI_GOV_PROBE_IDS", "")
    ids = tuple(part.strip() for part in raw.split(",") if part.strip()) or DEFAULT_IDS
    if not 1 <= len(ids) <= 5:
        raise ValueError("probe is bounded to 1..5 lot ids")
    if any(not re.fullmatch(r"[0-9_]{5,80}", value) for value in ids):
        raise ValueError("invalid public lot id")
    return {
        "report": "torgi_gov_detail_probe",
        "read_only": True,
        "database_writes": 0,
        "lots": [probe(value) for value in ids],
    }


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=False, separators=(",", ":")))
    except Exception as exc:
        print(json.dumps({"report": "torgi_gov_detail_probe", "error_type": type(exc).__name__}), file=sys.stderr)
        raise
