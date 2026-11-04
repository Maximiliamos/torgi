"""Fail-closed source-specific proof of true terminal catalogue page."""
from __future__ import annotations

from typing import Any

from bankrotai.connectors.etp_sources.profiles import PROFILES

_DIRECT_IDS = frozenset(p.source_id for p in PROFILES)


def reached_verified_terminal(source_id: str, metadata: dict[str, Any]) -> bool:
    """Only direct ETP sources require independent last-page evidence.

    Established stock sources have independent native cursor contracts
    (including the Torgi Russia multi-region navigator). Their behaviour
    must not be overridden by the generic direct-ETP evidence contract.
    """
    if source_id not in _DIRECT_IDS:
        return True
    if metadata.get("source_end_verified") is True:
        return True
    total = metadata.get("total_pages")
    current = metadata.get("progress_current")
    return (type(total) is int and type(current) is int
            and total > 0 and current == total)
