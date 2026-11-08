#!/usr/bin/env python3
"""Synthetic, read-only benchmark for semantic batch persistence.

Usage:
    PYTHONPATH=src python benchmarks/detail_freshness/benchmark.py

No network or production database is accessed. The benchmark creates an
in-memory SQLite database, seeds 1,585 synthetic rows, and measures the
actual batch_persistence SQL path for a metadata-only refresh versus a real
price change.
"""
from __future__ import annotations

import json
from time import perf_counter

from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.db import Base, LotSyncRun
from bankrotai.domain import NormalizedLot
from bankrotai.services.batch_persistence import persist_changed_lots_batch


ROWS = 1585
BATCH_SIZE = 500


def make_lot(index: int, *, price: float = 500_000, fingerprint: str | None = None) -> NormalizedLot:
    raw = {"region_code": "76", "category_code": "903"}
    if fingerprint is not None:
        raw["listing_fingerprint"] = fingerprint
    return NormalizedLot(
        external_id=f"benchmark-{index}",
        source="benchmark",
        source_system="benchmark.test",
        title=f"Объект {index}",
        description="Стабильное описание",
        category="land",
        region_slug="76",
        region_name="Ярославская область",
        address=f"Ярославль, участок {index}",
        cadastral_number=None,
        vin=None,
        area=100.0,
        start_price=500_000,
        current_price=price,
        auction_status="active",
        lot_url=f"https://example.test/{index}",
        source_url=f"https://example.test/{index}",
        detail_level="detail",
        raw_data=raw,
    )


def add_run(session, run_id: str) -> None:
    session.execute(
        update(LotSyncRun)
        .where(LotSyncRun.status.in_(("queued", "running")))
        .values(status="success")
    )
    session.add(LotSyncRun(id=run_id, status="running", trigger_type="benchmark", total_sources=1))
    session.flush()


def measured_call(engine, session, lots, run_id: str) -> dict:
    statements = 0

    def count_statement(*_args, **_kwargs) -> None:
        nonlocal statements
        statements += 1

    event.listen(engine, "before_cursor_execute", count_statement)
    started = perf_counter()
    try:
        stats = persist_changed_lots_batch(
            session,
            lots,
            run_id,
            batch_size=BATCH_SIZE,
        )
        session.commit()
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)
    return {
        "rows": len(lots),
        "chunks": stats.chunks,
        "semantic_skipped": stats.semantic_skipped,
        "persisted": stats.persisted,
        "sql_statements": statements,
        "commits": 1,
        "elapsed_ms": round((perf_counter() - started) * 1000, 3),
    }


def run() -> dict:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        add_run(session, "benchmark-seed")
        persist_changed_lots_batch(
            session,
            [make_lot(i, fingerprint="transport-1") for i in range(ROWS)],
            "benchmark-seed",
            batch_size=BATCH_SIZE,
        )
        session.commit()

        add_run(session, "benchmark-skip")
        metadata_only = measured_call(
            engine,
            session,
            [make_lot(i, fingerprint="transport-2") for i in range(ROWS)],
            "benchmark-skip",
        )

        add_run(session, "benchmark-price")
        price_change = measured_call(
            engine,
            session,
            [
                make_lot(i, price=499_999, fingerprint="transport-3")
                for i in range(ROWS)
            ],
            "benchmark-price",
        )

    engine.dispose()
    result = {
        "kind": "synthetic_sqlite_batch_persistence",
        "production_access": False,
        "rows": ROWS,
        "batch_size": BATCH_SIZE,
        "metadata_only_refresh": metadata_only,
        "real_price_change": price_change,
    }
    assert metadata_only["semantic_skipped"] == ROWS
    assert metadata_only["persisted"] == 0
    assert metadata_only["sql_statements"] == 12
    assert price_change["semantic_skipped"] == 0
    assert price_change["persisted"] == ROWS
    assert price_change["sql_statements"] == 24
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
