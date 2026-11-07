#!/usr/bin/env python3
"""Read-only microbenchmark of source comparison (synthetic, no production DB).

Usage: PYTHONPATH=src python benchmarks/detail_freshness/benchmark.py
This intentionally reports only CPU comparison performance. SQL transaction
and query counts must be obtained via SQLAlchemy event profiling on a
disposable database and are NOT inferred from this microbenchmark.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from statistics import median
from time import perf_counter
from types import SimpleNamespace

from bankrotai.services.detail_freshness import (
    SOURCE_BUSINESS_FIELDS, compare_source_values,
)


def make_record(index: int) -> dict:
    record = {key: None for key in SOURCE_BUSINESS_FIELDS}
    record.update({
        "canonical_lot_id": index + 1,
        "processed_lot_id": index + 1,
        "source_system": "cdtrf.ru",
        "external_id": f"synthetic-{index}",
        "title": f"Объект {index}",
        "current_price": Decimal("1000000.00"),
        "start_price": Decimal("1200000.00"),
        "source_status": "active",
        "is_active": True,
        "is_archived": False,
        "application_deadline": datetime(2026, 12, 15),
        "raw_data": {"listing_fingerprint": "transport-a"},
    })
    return record


def run(n=1585, repetitions=7):
    records = [make_record(i) for i in range(n)]
    changed = [dict(row) for row in records]
    changed[0]["current_price"] = Decimal("999999.00")
    changed[1]["application_deadline"] = datetime(2026, 12, 16)
    changed[2]["source_status"] = "closed"
    timings = []
    counts = []
    for _ in range(repetitions):
        started = perf_counter()
        count = sum(
            compare_source_values(SimpleNamespace(**original), candidate).changed
            for original, candidate in zip(records, changed)
        )
        timings.append((perf_counter() - started) * 1000)
        counts.append(count)
    return {
        "kind": "synthetic_cpu_only",
        "records": n,
        "repetitions": repetitions,
        "expected_changed": 3,
        "observed_changed": counts,
        "median_ms": round(median(timings), 3),
        "all_ms": [round(x, 3) for x in timings],
        "sql_statements": "not_measured",
        "transactions": "not_measured",
        "production_access": False,
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert all(count == 3 for count in result["observed_changed"])
