"""Read-only NormalizedLot evidence checks for direct ETP sources.

This is a preliminary data-contract audit, NOT authorization for source
promotion, missing-lot archival or financial decisions. Unknown source fields
remain unknown, never guessed from a neighbouring lot or procedure.
"""
from __future__ import annotations

from collections import Counter
from math import isfinite
from typing import Iterable
from urllib.parse import urlsplit

from bankrotai.connectors.etp_sources import PROFILES

_PROFILE_HOSTS = {p.source_id: frozenset(p.allowed_hosts) for p in PROFILES}


def lot_contract_findings(lot) -> tuple[str, ...]:
    """Report only field-level issue codes; no personal data leaves this helper."""
    issues = []
    source = str(getattr(lot, "source_system", "") or "")
    external_id = str(getattr(lot, "external_id", "") or "").strip()
    if source not in _PROFILE_HOSTS:
        issues.append("unknown_source")
    if not external_id:
        issues.append("missing_external_id")

    uri = str(getattr(lot, "lot_url", "") or "").strip()
    parsed = urlsplit(uri)
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if not uri:
        issues.append("missing_lot_url")
    elif (parsed.scheme != "https" or parsed.hostname not in _PROFILE_HOSTS.get(source, ())
          or parsed.username is not None or parsed.password is not None
          or port not in (None, 443)):
        issues.append("unverified_lot_url")

    if not str(getattr(lot, "title", "") or "").strip():
        issues.append("missing_title")

    for field in ("start_price", "current_price"):
        value = getattr(lot, field, None)
        if value is None:
            continue
        try:
            n = float(value)
            if not isfinite(n) or n <= 0 or n >= 1e15:
                issues.append("invalid_" + field)
        except (TypeError, ValueError, OverflowError):
            issues.append("invalid_" + field)
    if getattr(lot, "start_price", None) is None and getattr(lot, "current_price", None) is None:
        issues.append("missing_price")

    if getattr(lot, "auction_status", None) in (None, "", "unknown"):
        issues.append("unverified_status")
    raw = getattr(lot, "raw_data", None)
    if not isinstance(raw, dict):
        issues.append("missing_provenance")
    elif raw.get("reconcile_missing") is True or raw.get("safe_for_archival") is True:
        issues.append("unsafe_new_etp_archival_claim")
    return tuple(issues)


def summarize_lot_contract(lots: Iterable) -> dict:
    """Aggregate QA coverage without emitting titles, lot IDs or addresses."""
    counts: Counter[str] = Counter()
    total = 0
    for lot in lots:
        total += 1
        counts.update(lot_contract_findings(lot))
    return {
        "lots_checked": total,
        "field_findings": dict(sorted(counts.items())),
        "production_database_writes": 0,
        "source_accepted": False,
        "archival_allowed": False,
    }
