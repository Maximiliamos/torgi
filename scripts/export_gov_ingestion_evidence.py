"""Export preserved primary API JSON from a successful GIS ingestion stage.

No new HTTP or DB writes. Checked-at is the actual ingestion observation time,
not the time of this export. Only exact source/processed identities are joined.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json

from sqlalchemy import select, text

from bankrotai.db import LotSyncSourceRun, ProcessedLot, SessionLocal, SourceLot
from bankrotai.scrapers import TorgiGovClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    client = TorgiGovClient()
    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        stage = session.scalar(select(LotSyncSourceRun).where(
            LotSyncSourceRun.sync_run_id == args.run_id,
            LotSyncSourceRun.source_system == "torgi.gov.ru",
        ))
        if stage is None or stage.status != "success":
            raise ValueError("Require a successfully completed GIS source stage")
        rows = session.execute(select(
            ProcessedLot.id, SourceLot.external_id, SourceLot.raw_data, SourceLot.last_seen_at,
        ).join(SourceLot, (SourceLot.source_system == ProcessedLot.source_system)
               & (SourceLot.external_id == ProcessedLot.external_id)).where(
            SourceLot.source_system == "torgi.gov.ru", SourceLot.last_sync_run_id == args.run_id,
        ).order_by(ProcessedLot.id).execution_options(stream_results=True, yield_per=100))
        count = 0
        for lot_id, external_id, raw, observed in rows:
            payload = (raw or {}).get("raw")
            if not isinstance(payload, dict):
                raise ValueError("Preserved primary API response is absent")
            if "torgi_gov:" + str(client._external_base_from_payload(payload)) != external_id:
                raise ValueError("Preserved primary API identity differs from database identity")
            observed = observed.replace(tzinfo=timezone.utc) if observed.tzinfo is None else observed
            if not timedelta(0) <= datetime.now(timezone.utc) - observed <= timedelta(hours=24):
                raise ValueError("Ingestion observation is stale")
            body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
            print(json.dumps({"lot_id": lot_id, "external_id": external_id,
                              "checked_at_utc": observed.isoformat(), "ingestion_run_id": args.run_id,
                              "snapshot_base64": base64.b64encode(body).decode(),
                              "sha256": hashlib.sha256(body).hexdigest(),
                              "database_writes": 0}), flush=True)
            count += 1
        session.rollback()
    print(json.dumps({"event": "ingestion_export_finished", "selected": count, "database_writes": 0}), flush=True)


if __name__ == "__main__":
    main()
