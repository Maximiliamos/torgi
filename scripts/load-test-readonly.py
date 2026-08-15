from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from statistics import median
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = os.getenv("LOAD_TEST_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
USERNAME = os.getenv("E2E_USERNAME", "reader")
PASSWORD = os.environ.get("E2E_PASSWORD") or os.environ.get("AUTH_BOOTSTRAP_PASSWORD")
BASIC_USER = os.environ.get("WEB_BASIC_AUTH_USER")
BASIC_PASSWORD = os.environ.get("WEB_BASIC_AUTH_PASSWORD")
CONCURRENCY_LEVELS = (1, 5, 10, 25, 50)
PATHS = ("/api/lots?limit=20", "/api/stats", "/api/map/lots?limit=100")


def _basic_header() -> str | None:
    if BASIC_USER is None or BASIC_PASSWORD is None:
        return None
    raw = f"{BASIC_USER}:{BASIC_PASSWORD}".encode()
    return f"Basic {base64.b64encode(raw).decode()}"


def _request(path: str, *, method: str = "GET", body: bytes | None = None, cookie: str | None = None) -> tuple[int, float]:
    headers = {"Accept": "application/json"}
    basic = _basic_header()
    if basic:
        headers["Authorization"] = basic
    if body is not None:
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    request = Request(f"{BASE_URL}{path}", data=body, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=20) as response:
            response.read()
            return response.status, (time.perf_counter() - started) * 1000
    except HTTPError as exc:
        exc.read()
        return exc.code, (time.perf_counter() - started) * 1000
    except (TimeoutError, URLError):
        return 0, (time.perf_counter() - started) * 1000


def _login() -> str:
    if not PASSWORD:
        raise RuntimeError("E2E_PASSWORD or AUTH_BOOTSTRAP_PASSWORD is required")
    payload = json.dumps({"username": USERNAME, "password": PASSWORD}).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    basic = _basic_header()
    if basic:
        headers["Authorization"] = basic
    request = Request(f"{BASE_URL}/api/auth/login", data=payload, headers=headers, method="POST")
    with urlopen(request, timeout=20) as response:
        response.read()
        if response.status != 200:
            raise RuntimeError(f"Login failed with HTTP {response.status}")
        cookie = response.headers.get("Set-Cookie", "").split(";", 1)[0]
    if not cookie:
        raise RuntimeError("Login did not return a session cookie")
    return cookie


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(round((len(ordered) - 1) * percentile), len(ordered) - 1)
    return ordered[index]


def main() -> int:
    cookie = _login()
    failed = False
    for concurrency in CONCURRENCY_LEVELS:
        jobs = [PATHS[index % len(PATHS)] for index in range(concurrency)]
        results: list[tuple[int, float]] = []
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(_request, path, cookie=cookie) for path in jobs]
            for future in as_completed(futures):
                results.append(future.result())
        durations = [duration for _, duration in results]
        errors = [status for status, _ in results if status != 200]
        print(json.dumps({
            "concurrency": concurrency,
            "requests": len(results),
            "errors": len(errors),
            "p50_ms": round(median(durations), 1),
            "p95_ms": round(_percentile(durations, 0.95), 1),
            "max_ms": round(max(durations), 1),
        }))
        failed = failed or bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
