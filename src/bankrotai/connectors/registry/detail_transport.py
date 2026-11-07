"""Bounded synchronous detail workers; never share a live requests.Session across threads."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from urllib.parse import urlsplit

import requests


class HostRateLimiter:
    """Thread-safe, start-to-start minimum interval for a single origin."""

    def __init__(self, requests_per_second: float):
        if not 0 < requests_per_second <= 10:
            raise ValueError("Unsafe per-host detail rate")
        self.interval = 1.0 / requests_per_second
        self._next_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def acquire(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Detail rate limiter requires HTTPS origin")
        host = parsed.hostname
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_at.get(host, now))
            self._next_at[host] = start + self.interval
        # This sleep is confined to a detail executor worker, never the event loop.
        remaining = start - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)


class DetailWorkers:
    """At most N in-flight details, with one owned HTTP session per worker thread."""

    def __init__(self, source_session: requests.Session, concurrency: int, rate: float):
        if type(concurrency) is not int or not 1 <= concurrency <= 4:
            raise ValueError("Unsafe detail concurrency")
        self.source_session = source_session
        self.concurrency = concurrency
        self.limiter = HostRateLimiter(rate)
        self._local = threading.local()
        self._sessions: list[requests.Session] = []
        self._lock = threading.Lock()

    def session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            if isinstance(self.source_session, requests.Session):
                session = requests.Session()
                session.headers.update(self.source_session.headers)
                session.cookies.update(self.source_session.cookies)
                session.proxies.update(self.source_session.proxies)
                session.auth = self.source_session.auth
                session.verify = self.source_session.verify
                session.cert = self.source_session.cert
                session.trust_env = self.source_session.trust_env
                with self._lock:
                    self._sessions.append(session)
            else:
                # Preserve explicit injected test transports without assuming they
                # are constructible as requests.Session subclasses.
                session = self.source_session
            self._local.session = session
        return session

    @contextmanager
    def pool(self):
        try:
            with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
                yield executor
        finally:
            for session in self._sessions:
                session.close()
