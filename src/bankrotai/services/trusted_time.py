from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from statistics import median

import httpx

from bankrotai.core import get_logger

logger = get_logger(__name__)

_SOURCES = ("https://time100.ru/", "https://www.cloudflare.com/")
_lock = threading.Lock()
_offset_seconds = 0.0
_source = "system_utc"
_checked_monotonic = 0.0
_CACHE_SECONDS = 300.0
_MAX_ACCEPTED_SKEW_SECONDS = 300.0


def _probe() -> tuple[float, str]:
    samples: list[tuple[float, str]] = []
    with httpx.Client(timeout=2.5, follow_redirects=True) as client:
        for url in _SOURCES:
            started = datetime.now(timezone.utc)
            try:
                response = client.head(url)
                received = datetime.now(timezone.utc)
                remote = parsedate_to_datetime(response.headers["date"]).astimezone(timezone.utc)
                midpoint = started + (received - started) / 2
                skew = (remote - midpoint).total_seconds()
                if response.status_code < 500 and abs(skew) <= _MAX_ACCEPTED_SKEW_SECONDS:
                    samples.append((skew, url))
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                continue
    if not samples:
        raise RuntimeError("no trusted HTTPS time source responded")
    offset = median(sample[0] for sample in samples)
    return offset, ",".join(sample[1] for sample in samples)


def trusted_utc_now(*, refresh: bool = False) -> datetime:
    global _checked_monotonic, _offset_seconds, _source
    monotonic_now = time.monotonic()
    if refresh or monotonic_now - _checked_monotonic >= _CACHE_SECONDS:
        with _lock:
            monotonic_now = time.monotonic()
            if refresh or monotonic_now - _checked_monotonic >= _CACHE_SECONDS:
                try:
                    _offset_seconds, _source = _probe()
                except RuntimeError as exc:
                    logger.warning("Online UTC check failed; using system UTC: %s", exc)
                    _offset_seconds, _source = 0.0, "system_utc"
                _checked_monotonic = monotonic_now
    return datetime.now(timezone.utc) + timedelta(seconds=_offset_seconds)


def trusted_time_status() -> dict[str, object]:
    current = trusted_utc_now()
    return {
        "utc": current.isoformat(),
        "timezone": "Europe/Moscow",
        "moscow": current.astimezone(timezone(timedelta(hours=3))).isoformat(),
        "source": _source,
        "synchronized": _source != "system_utc",
        "offset_seconds": round(_offset_seconds, 3),
    }

