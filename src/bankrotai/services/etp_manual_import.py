"""Controlled one-page ETP canary import for the trusted home worker.

Writes are disabled unless both independent operator/backup gates are set.
No source-wide archiving, no missing-lot reconciliation and no bulk scheduling.
Use only after the home backup-and-isolated-restore workflow has succeeded.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from bankrotai.connectors.registry import connector_registry
from bankrotai.connectors.registry.etp_incremental import EtpIncrementalFilters, _property_ready

APPROVED = ("m-ets.ru", "cdtrf.ru")


def _assert_apply_allowed() -> None:
    if (os.environ.get("ETP_OPERATOR_CANARY_IMPORT_ENABLED") != "1"
            or os.environ.get("ETP_CANARY_BACKUP_VERIFIED") != "1"):
        raise PermissionError("Refusing import: operator and verified-backup gates are required")


def verified_canary_items(page, *, source: str, limit: int) -> list:
    """Fail closed on an unexpected, overly broad or mixed-source connector page."""
    metadata = page.metadata
    if (not isinstance(metadata, dict)
            or metadata.get("complete_source_inventory") is not False
            or metadata.get("safe_for_archival") is not False):
        raise ValueError("ETP canary page lacks explicit partial/non-archival evidence")
    items = list(page.items)
    if len(items) > limit:
        raise ValueError("ETP canary connector exceeded the authorized detail cap")
    identities: set[str] = set()
    for lot in items:
        identity = str(lot.external_id or "").strip()
        if (lot.source_system != source or not identity or identity in identities
                or not _property_ready(lot)):
            raise ValueError("Unverified, duplicated or cross-source ETP canary lot")
        identities.add(identity)
    return items


async def collect_lots(source: str, limit: int):
    if source not in APPROVED or not 1 <= limit <= 2:
        raise ValueError("Unsupported source or overly broad canary limit")
    connector = connector_registry.create(source)
    if source == "m-ets.ru":
        page = await connector.search(
            EtpIncrementalFilters(source, max_detail_requests=limit)
        )
        return verified_canary_items(page, source=source, limit=limit)

    # CDTRF's first two catalogue entries can be unsupported property rights.
    # Inspect at most five public details per page; promote no more than
    # "limit" verified sale identities across at most five pages (25 reads).
    # The persisted batch cap and atomic collision guards stay unchanged.
    detail_probe_cap = 5
    filters = EtpIncrementalFilters(
        source, max_detail_requests=detail_probe_cap, max_pages=5,
    )
    selected: list = []
    cursor: str | None = None
    seen_fingerprints: set[str] = set()
    seen_ids: set[str] = set()
    for page_number in range(1, 6):
        page = await connector.search(filters, cursor)
        items = verified_canary_items(page, source=source, limit=detail_probe_cap)
        metadata = page.metadata
        fingerprints = metadata.get("catalogue_identity_fingerprints")
        observed = metadata.get("catalogue_lots")
        if (
            metadata.get("progress_current") != page_number
            or metadata.get("progress_total") != 5
            or type(observed) is not int or observed < len(items)
            or not isinstance(fingerprints, list)
            or len(fingerprints) != observed
            or len(fingerprints) != len(set(fingerprints))
            or not all(isinstance(value, str)
                       and len(value) == 64
                       and all(c in "0123456789abcdef" for c in value)
                       for value in fingerprints)
            or any(value in seen_fingerprints for value in fingerprints)
        ):
            raise ValueError("CDTRF bounded catalogue identity evidence is incomplete")
        seen_fingerprints.update(fingerprints)
        for lot in items:
            external_id = str(lot.external_id)
            if external_id in seen_ids:
                raise ValueError("CDTRF repeated a verified sale identity")
            seen_ids.add(external_id)
            selected.append(lot)
            if len(selected) >= limit:
                return selected
        if page_number < 5:
            if not isinstance(page.next_cursor, str) or not page.next_cursor.startswith(
                f"{page_number + 1}:"
            ):
                raise ValueError("CDTRF bounded pagination ended before five pages")
            cursor = page.next_cursor
        elif page.next_cursor is not None:
            raise ValueError("CDTRF page 5 returned an unbounded cursor")
    # No verified sale is still a failure at the apply boundary, not an
    # opportunity to persist unverified catalogue or vehicle candidates.
    return selected


def run_import(
    source: str, *, limit: int, apply: bool, audit_collisions: bool = False,
) -> dict:
    if audit_collisions and apply:
        raise ValueError("Collision preview cannot be combined with --apply")
    if apply:
        _assert_apply_allowed()  # Must happen before any network/DB access.
    lots = asyncio.run(collect_lots(source, limit))
    report: dict[str, Any] = {"source": source, "verified_detail_count": len(lots),
              "persisted_count": 0, "persisted_external_ids": [],
              "duplicate_review_count": 0,
              "write_authorized": apply, "missing_lot_archival": False,
              "complete_source_inventory": False}
    if not apply:
        if audit_collisions:
            if not lots:
                raise RuntimeError("No verified property details to preflight")
            from bankrotai.db import SessionLocal
            from bankrotai.services.etp_preimport_collision_review import (
                review_canary_collisions,
            )
            # Session never commits and is rolled back even if SELECT fails.
            with SessionLocal() as session:
                try:
                    report["preimport_collision_review"] = review_canary_collisions(
                        session, lots, source=source
                    )
                finally:
                    session.rollback()
        return report
    if not lots:
        raise RuntimeError("No verified real-estate lots found; refusing empty import")
    if len(lots) > limit or len(lots) > 2:
        raise ValueError("Refusing unbounded ETP canary persistence")
    # Candidate pages can change between preflight and apply. Recheck the
    # entire write batch before opening a transaction: no two different sale
    # IDs may reuse one cadastral number in this tiny operator canary.
    if len({str(lot.cadastral_number or "").strip() for lot in lots}) != len(lots):
        raise ValueError("Canary batch repeats a cadastral number; manual review required")
    from bankrotai.db import session_scope
    from bankrotai.services.etp_canary_persistence import persist_canary_lot
    # Commit at most two verified lots together. A cross-source collision or
    # failure on the second lot must roll back the first, including its journal.
    with session_scope() as session:
        for lot in lots:
            persist_canary_lot(session, lot, "github-actions-etp-canary")
    report["persisted_count"] = len(lots)
    report["persisted_external_ids"] = [lot.external_id for lot in lots]
    return report


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--source", choices=APPROVED, required=True)
    cli.add_argument("--limit", type=int, default=2)
    cli.add_argument("--apply", action="store_true")
    cli.add_argument("--audit-collisions", action="store_true")
    args = cli.parse_args()
    report = run_import(
        args.source, limit=args.limit, apply=args.apply,
        audit_collisions=args.audit_collisions,
    )
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
