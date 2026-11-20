"""A successful primary observation may retire a map member on any revision."""
from datetime import timedelta

from sqlalchemy import select

from bankrotai.db import LotSyncSourceRun, ProcessedLot, SourceLot
from bankrotai.services.individual_lot_identity import unscoped_procedure_identity


def confirmed_source_closure_ids(session, *, lot_ids, previous_published_at, candidate_created_at):
    from bankrotai.logic import normalize_status

    accepted = set()
    ids = sorted(lot_ids)
    for offset in range(0, len(ids), 1000):
        rows = session.execute(select(
            ProcessedLot.id, ProcessedLot.source_system, ProcessedLot.external_id,
            ProcessedLot.auction_status, SourceLot.source_status,
            SourceLot.raw_data["source_sale_eligibility"].as_boolean().label("sale_eligible"),
        ).join(SourceLot, (SourceLot.processed_lot_id == ProcessedLot.id)
               & (SourceLot.source_system == ProcessedLot.source_system)
               & (SourceLot.external_id == ProcessedLot.external_id))
          .join(LotSyncSourceRun, (LotSyncSourceRun.sync_run_id == SourceLot.last_sync_run_id)
                & (LotSyncSourceRun.source_system == SourceLot.source_system))
          .where(ProcessedLot.id.in_(ids[offset:offset+1000]),
                 ProcessedLot.is_archived.is_(True), SourceLot.is_archived.is_(True),
                 SourceLot.is_active.is_(False), SourceLot.archive_reason == "source_status",
                 SourceLot.missing_successful_runs == 0,
                 SourceLot.source_system != "tbankrot.ru", LotSyncSourceRun.status == "success",
                 LotSyncSourceRun.started_at > previous_published_at,
                 LotSyncSourceRun.finished_at <= candidate_created_at,
                 SourceLot.last_seen_at >= LotSyncSourceRun.started_at,
                 SourceLot.last_seen_at >= candidate_created_at-timedelta(hours=24),
                 SourceLot.last_seen_at <= LotSyncSourceRun.finished_at)).mappings()
        for row in rows:
            if (row["sale_eligible"] is False
                    or unscoped_procedure_identity(row["source_system"], row["external_id"])
                    or normalize_status(row["auction_status"] or "") != "closed"
                    or normalize_status(row["source_status"] or "") != "closed"):
                continue
            accepted.add(row["id"])
    return accepted
