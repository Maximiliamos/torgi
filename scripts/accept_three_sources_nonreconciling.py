"""Two serial production acceptance passes, without global dedupe or expiry.

This operator command writes source observations through normal persistence.
It never archives missing identities and does not change scheduler settings.
Run only after exact-SHA deployment/backup and production acceptance gates.
"""
import asyncio
import hashlib
import json

from sqlalchemy import select

from bankrotai.connectors.etp_sources.common import EtpSearchFilters
from bankrotai.connectors.registry import connector_registry
from bankrotai.connectors.registry.mets_sync import MetsSyncFilters
from bankrotai.db import LotNote, ProcessedLot, SessionLocal
from bankrotai.services.ingestion import NationwideIngestionService
from bankrotai.services.ingestion_models import SourceSyncSpec


class SerialAcceptance(NationwideIngestionService):
    async def _run_active(self, run_id, specs):
        results = []
        for spec in specs:
            if spec.reconcile_missing or not spec.require_source_end:
                raise ValueError("Only terminal, non-reconciling acceptance is allowed")
            result = await self._sync_source(run_id, spec)
            results.append(self._result_payload(result))
            print(json.dumps({"event": "source_finished", "run_id": run_id, "source": results[-1]}, default=str), flush=True)
            if result.status != "success" or result.items_archived:
                return {"status": "failed", "sources": results, "expired_after_auction": 0}
        return {"status": "success", "sources": results, "expired_after_auction": 0,
                "global_deduplication": False, "missing_lot_reconciliation": False}


def traced_connector(source_id):
    connector = connector_registry.create(source_id)
    search = connector.search

    async def traced_search(filters, cursor=None):
        page = await search(filters, cursor)
        print(json.dumps({
            "event": "source_page", "source_system": source_id, "cursor": cursor,
            "accepted_ids": [lot.external_id for lot in page.items],
            "reused_ids": page.metadata.get("reused_existing_ids", []),
            "detail_rejection_samples": page.metadata.get("detail_rejection_samples", []),
            "next_cursor": page.next_cursor,
        }), flush=True)
        return page

    connector.search = traced_search
    return connector


def reviews_digest():
    with SessionLocal() as session:
        reviews = [tuple(r) for r in session.execute(select(
            ProcessedLot.id, ProcessedLot.review_status, ProcessedLot.rating,
        ).where(ProcessedLot.review_status.isnot(None)).order_by(ProcessedLot.id))]
        notes = [tuple(r) for r in session.execute(select(
            LotNote.id, LotNote.lot_id, LotNote.user_id, LotNote.content,
        ).order_by(LotNote.id))]
    return hashlib.sha256(json.dumps([reviews, notes], default=str).encode()).hexdigest()


async def main():
    specs = (
        SourceSyncSpec("sberbank-ast.ru", EtpSearchFilters(), reconcile_missing=False,
                       max_batches=500, require_source_end=True),
        SourceSyncSpec("vertrades.ru", EtpSearchFilters(), reconcile_missing=False,
                       max_batches=500, require_source_end=True),
        SourceSyncSpec("m-ets.ru", MetsSyncFilters(), reconcile_missing=False,
                       max_batches=500, require_source_end=True, reuse_unchanged_details=True),
    )
    baseline = reviews_digest()
    service = SerialAcceptance(SessionLocal, connector_factory=traced_connector,
                               max_concurrent_sources=1, profile_timings=True)
    for iteration in (1, 2):
        run_id = service.create_run(triggered_by="systemic_completion", trigger_type="manual", total_sources=3)
        print(json.dumps({"event": "pass_started", "iteration": iteration, "run_id": run_id}), flush=True)
        result = await service.run(run_id, specs)
        preserved = baseline == reviews_digest()
        print(json.dumps({"event": "pass_finished", "iteration": iteration, "run_id": run_id,
                          "reviews_and_notes_preserved": preserved, "result": result}, default=str), flush=True)
        if result["status"] != "success" or not preserved:
            raise SystemExit("Acceptance failed; do not restore automatic scheduler")


if __name__ == "__main__":
    asyncio.run(main())
