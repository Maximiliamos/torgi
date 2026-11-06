"""Low-rate, read-only baseline for local TORGI API latency (no external HTTP)."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise URLError('redirects are disabled for local-only diagnostics')


LOCAL_OPENER = build_opener(_NoRedirect())


def local_url(value: str) -> str:
    url = urlsplit(value)
    if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"}
            or url.username is not None or url.password is not None
            or url.query or url.fragment):
        raise ValueError("Only local HTTP URLs without credentials or query strings are permitted")
    return value


def measure(endpoint: str, *, count: int, timeout: float, interval: float) -> dict:
    local_url(endpoint)
    if not 1 <= count <= 50 or timeout <= 0 or interval < 0:
        raise ValueError("Unsafe measurement count, timeout, or interval")
    durations: list[float] = []
    statuses: dict[str, int] = {}
    for sample in range(count):
        before = time.perf_counter()
        try:
            with LOCAL_OPENER.open(endpoint, timeout=timeout) as response:
                status = str(response.status)
                response.read(2048)
        except HTTPError as exc:
            status = str(exc.code)
        except (URLError, TimeoutError, OSError):
            status = "network_error"
        elapsed_ms = (time.perf_counter() - before) * 1000
        durations.append(round(elapsed_ms, 3))
        statuses[status] = statuses.get(status, 0) + 1
        if sample != count - 1 and interval:
            time.sleep(interval)
    ordered = sorted(durations)
    return {
        "url": endpoint,
        "requests": count,
        "statuses": statuses,
        "median_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[max(0, math.ceil(0.95 * count) - 1)], 2),
        "max_ms": round(max(ordered), 2),
        "raw_ms": durations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint", action="append",
        default=None, help="Local URL; repeat for more endpoints",
    )
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    endpoints = args.endpoint or [
        "http://127.0.0.1:18000/health/live",
        "http://127.0.0.1:18000/health/ready",
        "http://127.0.0.1:18000/api/map/datasets/current",
    ]
    result = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read-only local HTTP, low request rate",
        "endpoints": [
            measure(local_url(endpoint), count=args.count, timeout=args.timeout, interval=args.interval)
            for endpoint in endpoints
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
