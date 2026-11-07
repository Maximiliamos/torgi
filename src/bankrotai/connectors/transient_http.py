from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

import requests


DEFAULT_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def request_with_retries(
    session: requests.Session,
    method: str,
    url: str,
    *,
    delays: Sequence[float] = (1.0, 2.0, 4.0),
    retry_statuses: frozenset[int] = DEFAULT_RETRY_STATUSES,
    sleep: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> requests.Response:
    """Execute a read-only HTTP request with bounded transient retries."""
    caller = getattr(session, method)
    attempts = len(delays) + 1
    for attempt in range(attempts):
        try:
            response = caller(url, **kwargs)
        except (
            requests.Timeout,
            requests.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ):
            if attempt == attempts - 1:
                raise
            sleep(float(delays[attempt]))
            continue
        if int(getattr(response, "status_code", 0) or 0) in retry_statuses:
            if attempt == attempts - 1:
                return response
            sleep(float(delays[attempt]))
            continue
        return response
    raise RuntimeError("transient HTTP retry loop exhausted")
