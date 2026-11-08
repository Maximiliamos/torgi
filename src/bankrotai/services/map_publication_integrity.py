"""Exact map-input revision check for fail-closed dataset publication.

The previous count/id-sum/max-timestamp signature missed edits to a non-max
record and updates that did not advance last_update. Hash every field consumed
by tile generation, in stable primary-key order, without loading the full
result set into memory. All callers must use one transactionally consistent
snapshot; promotion additionally holds a PostgreSQL writer-conflicting lock.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.db import ProcessedLot

MAX_WEB_MERCATOR_LAT = 85.05112878


def exact_map_input_fingerprint(session: Session, *, revision: str, layout: str,
                                object_store_enabled: bool) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps(
        [revision, layout, bool(object_store_enabled)],
        separators=(",", ":"),
    ).encode("utf-8"))
    rows = session.execute(
        select(
            ProcessedLot.id,
            ProcessedLot.title,
            ProcessedLot.current_price,
            ProcessedLot.start_price,
            ProcessedLot.category,
            ProcessedLot.region_code,
            ProcessedLot.cadastral_number,
            ProcessedLot.auction_status,
            ProcessedLot.review_status,
            ProcessedLot.is_archived,
            ProcessedLot.current_geo_lat,
            ProcessedLot.current_geo_lon,
        )
        .where(
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.is_archived.is_(False),
            ProcessedLot.current_geo_lat.between(-MAX_WEB_MERCATOR_LAT, MAX_WEB_MERCATOR_LAT),
            ProcessedLot.current_geo_lon.between(-180.0, 180.0),
        )
        .order_by(ProcessedLot.id)
        .execution_options(yield_per=1000)
    )
    for row in rows:
        digest.update(b"\n")
        digest.update(json.dumps(tuple(row), default=str, ensure_ascii=False,
                                 separators=(",", ":")).encode("utf-8"))
    return digest.hexdigest()
