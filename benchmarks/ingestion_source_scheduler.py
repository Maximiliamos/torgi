"""Deterministic offline *scheduler-only* comparison; never invokes source connectors.

Run: PYTHONPATH=src python benchmarks/ingestion_source_scheduler.py
Measurements are synthetic elapsed time, NOT production source-run timings.
"""
from __future__ import annotations

import asyncio
import statistics
import time


SOURCE_LATENCIES_S = (0.12, 0.09, 0.11, 0.08, 0.13, 0.10, 0.07)


async def emulate_source(delay):
    await asyncio.sleep(delay)


async def serial_baseline():
    for latency in SOURCE_LATENCIES_S:
        await emulate_source(latency)


async def bounded_parallel(limit):
    semaphore = asyncio.Semaphore(limit)
    async def worker(delay):
        async with semaphore:
            await emulate_source(delay)
    await asyncio.gather(*(worker(delay) for delay in SOURCE_LATENCIES_S))


def measure(coro_factory, samples=5):
    runs = []
    for _ in range(samples):
        started = time.perf_counter()
        asyncio.run(coro_factory())
        runs.append(round(time.perf_counter() - started, 4))
    return {"median_s": statistics.median(runs), "samples_s": runs}


if __name__ == "__main__":
    baseline = measure(serial_baseline)
    candidate = measure(lambda: bounded_parallel(3))
    print({
        "type": "synthetic_scheduler_only",
        "serial": baseline,
        "bounded_3": candidate,
        "median_speedup": round(baseline["median_s"] / candidate["median_s"], 2),
        "note": "No DB, network, GEO or map work is included.",
    })
