"""Offline benchmark for bounded detail transport; no network/database I/O.

Usage: python benchmarks/etp_detail_throughput.py
Per-host rate limiting applies to request starts (not successful completions).
"""
from __future__ import annotations

import json
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from bankrotai.connectors.registry.detail_transport import HostRateLimiter


def measure(*, requests_count: int = 12, concurrency: int = 2,
            rate: float = 2.0, latency: float = 0.65) -> dict:
    limiter = HostRateLimiter(rate)
    lock = threading.Lock()
    starts: list[float] = []
    failures: list[str] = []
    active = peak = 0

    def fetch(i: int):
        nonlocal active, peak
        limiter.acquire("https://torgi.cdtrf.ru/trades/123")
        start = time.monotonic()
        with lock:
            starts.append(start)
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(latency)
            # Deterministic synthetic 429, 503 and timeout-like errors.
            if i % 13 == 12:
                with lock:
                    failures.append("429")
            elif i % 11 == 10:
                with lock:
                    failures.append("503")
            elif i % 7 == 6:
                with lock:
                    failures.append("timeout")
        finally:
            with lock:
                active -= 1

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        list(executor.map(fetch, range(requests_count)))
    duration = time.monotonic() - started
    sorted_starts = sorted(starts)
    gaps = [b - a for a, b in zip(sorted_starts, sorted_starts[1:])]
    return {
        "mode": "synthetic_offline",
        "requests": requests_count,
        "concurrency": concurrency,
        "per_host_max_starts_per_second": rate,
        "synthetic_latency_seconds": latency,
        "elapsed_seconds": round(duration, 3),
        "requests_per_second": round(requests_count / duration, 3),
        "peak_in_flight": peak,
        "minimum_start_spacing_seconds": round(min(gaps), 3) if gaps else None,
        "errors": {kind: failures.count(kind) for kind in ("429", "503", "timeout")},
        "successes": requests_count - len(failures),
    }


if __name__ == "__main__":
    print(json.dumps({
        "baseline_sequential": measure(concurrency=1),
        "bounded_parallel": measure(concurrency=2),
    }, indent=2, sort_keys=True))
