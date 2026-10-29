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

from bankrotai.connectors.registry import connector_registry
from bankrotai.connectors.registry.etp_incremental import EtpIncrementalFilters

APPROVED = ("m-ets.ru", "cdtrf.ru")


def _assert_apply_allowed() -> None:
    if (os.environ.get("ETP_OPERATOR_CANARY_IMPORT_ENABLED") != "1"
            or os.environ.get("ETP_CANARY_BACKUP_VERIFIED") != "1"):
        raise PermissionError("Refusing import: operator and verified-backup gates are required")


async def collect_lots(source: str, limit: int):
    if source not in APPROVED or not 1 <= limit <= 3:
        raise ValueError("Unsupported source or overly broad canary limit")
    page = await connector_registry.create(source).search(
        EtpIncrementalFilters(source, max_detail_requests=limit)
    )
    return page.items


def run_import(source: str, *, limit: int, apply: bool) -> dict:
    if apply:
        _assert_apply_allowed()  # Must happen before any network/DB access.
    lots = asyncio.run(collect_lots(source, limit))
    report = {"source": source, "verified_detail_count": len(lots),
              "persisted_count": 0, "duplicate_review_count": 0,
              "write_authorized": apply, "missing_lot_archival": False,
              "complete_source_inventory": False}
    if not apply:
        return report
    from bankrotai.db import session_scope
    from bankrotai.services.etp_canary_persistence import (
        CanaryDuplicateReviewRequired, persist_canary_lot,
    )
    for lot in lots:
        try:
            with session_scope() as session:
                persist_canary_lot(session, lot, "github-actions-etp-canary")
            report["persisted_count"] += 1
        except CanaryDuplicateReviewRequired:
            report["duplicate_review_count"] += 1
    return report


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--source", choices=APPROVED, required=True)
    cli.add_argument("--limit", type=int, default=2)
    cli.add_argument("--apply", action="store_true")
    args = cli.parse_args()
    report = run_import(args.source, limit=args.limit, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
