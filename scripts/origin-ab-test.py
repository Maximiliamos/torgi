from __future__ import annotations

import json
import os
import sys
import time
import uuid
from collections import Counter
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener


BASE_URL = os.environ["AB_BASE_URL"].rstrip("/")
PATH = os.getenv("AB_PATH", "/api/lots?city_slug=yaroslavl&page=1&per_page=1")
COUNT = int(os.getenv("AB_COUNT", "50"))
TIMEOUT = float(os.getenv("AB_TIMEOUT", "20"))
USERNAME = os.getenv("E2E_USERNAME", "admin")
PASSWORD = os.environ["E2E_PASSWORD"]
API_KEY = os.getenv("AB_API_KEY")
LABEL = os.getenv("AB_LABEL", BASE_URL)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)]


def main() -> int:
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    login_headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if API_KEY:
        login_headers["X-API-Key"] = API_KEY
    login = Request(
        f"{BASE_URL}/api/auth/login",
        data=json.dumps({"username": USERNAME, "password": PASSWORD}).encode(),
        headers=login_headers,
        method="POST",
    )
    with opener.open(login, timeout=TIMEOUT) as response:
        response.read()
        if response.status != 200:
            raise RuntimeError(f"{LABEL}: login HTTP {response.status}")

    samples: list[dict[str, object]] = []
    for index in range(1, COUNT + 1):
        request_id = f"ab-{LABEL}-{uuid.uuid4()}"
        headers = {"Accept": "application/json", "X-Request-ID": request_id}
        if API_KEY:
            headers["X-API-Key"] = API_KEY
        request = Request(f"{BASE_URL}{PATH}", headers=headers)
        started = time.perf_counter()
        status = 0
        returned_id = ""
        error = ""
        try:
            with opener.open(request, timeout=TIMEOUT) as response:
                response.read()
                status = response.status
                returned_id = response.headers.get("X-Request-ID", "")
        except HTTPError as exc:
            exc.read()
            status = exc.code
            returned_id = exc.headers.get("X-Request-ID", "")
        except (TimeoutError, URLError) as exc:
            error = type(exc).__name__
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        sample = {
            "index": index,
            "request_id": request_id,
            "returned_request_id": returned_id,
            "status": status,
            "duration_ms": duration_ms,
            "error": error,
        }
        samples.append(sample)
        if status != 200 or returned_id != request_id:
            print(json.dumps({"label": LABEL, "failure": sample}, separators=(",", ":")))

    durations = [float(sample["duration_ms"]) for sample in samples]
    statuses = Counter(str(sample["status"]) for sample in samples)
    summary = {
        "label": LABEL,
        "requests": COUNT,
        "success": statuses["200"],
        "timeouts_or_transport": statuses["0"],
        "statuses": dict(sorted(statuses.items())),
        "request_id_mismatches": sum(
            sample["returned_request_id"] != sample["request_id"] for sample in samples
        ),
        "p50_ms": percentile(durations, 0.50),
        "p95_ms": percentile(durations, 0.95),
        "max_ms": max(durations),
    }
    print(json.dumps(summary, separators=(",", ":")))
    return 0 if statuses == Counter({"200": COUNT}) else 1


if __name__ == "__main__":
    sys.exit(main())
