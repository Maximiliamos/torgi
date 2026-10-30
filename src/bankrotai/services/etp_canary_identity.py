"""Stable, source-scoped digest for the exact public identities approved by S5.

Only public source IDs are included. No cadastral numbers, prices,
addresses, debtor identifiers, or other private detail attributes.
"""
from __future__ import annotations

import hashlib


def canary_identity_digest(source: str, lots: list) -> str:
    if source not in {"m-ets.ru", "cdtrf.ru"}:
        raise ValueError("Unapproved ETP source")
    identities = [
        str(getattr(lot, "external_id", "") or "").strip()
        for lot in lots
    ]
    if not 1 <= len(identities) <= 2:
        raise ValueError("Canary requires one or two verified identities")
    if any(not value or "\n" in value or "\r" in value for value in identities):
        raise ValueError("Unusable public ETP identity")
    if len(identities) != len(set(identities)):
        raise ValueError("Repeated ETP identity")
    payload = "\n".join([source, *sorted(identities)]).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
