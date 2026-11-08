"""Bounded synchronous detail workers; never share a live requests.Session across threads."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from typing import TypeVar
from urllib.parse import urlsplit

import requests

_Item = TypeVar("_Item")
_Result = TypeVar("_Result")


class HostRateLimiter:
    """Thread-safe, start-to-start minimum interval for a single origin."""

    def __init__(self, requests_per_second: float):
        if not 0 < requests_per_second <= 10:
            raise ValueError("Unsafe per-host detail rate")
        self.requests_per_second = float(requests_per_second)
        self.interval = 1.0 / self.requests_per_second
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

    def __init__(
        self,
        source_session: requests.Session,
        concurrency: int,
        rate: float,
        *,
        limiter: HostRateLimiter | None = None,
    ):
        if type(concurrency) is not int or not 1 <= concurrency <= 4:
            raise ValueError("Unsafe detail concurrency")
        if limiter is not None and limiter.requests_per_second != float(rate):
            raise ValueError("Shared detail limiter rate mismatch")
        self.source_session = source_session
        self.concurrency = concurrency
        self.limiter = limiter or HostRateLimiter(rate)
        self._local = threading.local()
        self._sessions: list[requests.Session] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def acquire(self, url: str) -> None:
        if self._stop.is_set():
            raise RuntimeError("Detail batch cancelled after fatal peer failure")
        self.limiter.acquire(url)
        if self._stop.is_set():
            raise RuntimeError("Detail batch cancelled after fatal peer failure")

    def session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            if type(self.source_session) is requests.Session:
                session = requests.Session()
                session.headers.update(self.source_session.headers)
                session.cookies.update(self.source_session.cookies)
                session.proxies.update(self.source_session.proxies)
                session.params.update(self.source_session.params)
                session.auth = self.source_session.auth
                session.verify = self.source_session.verify
                session.cert = self.source_session.cert
                session.trust_env = self.source_session.trust_env
                with self._lock:
                    self._sessions.append(session)
            else:
                # Preserve custom/injected transports rather than silently
                # replacing subclass behaviour with a plain requests.Session.
                session = self.source_session
            self._local.session = session
        return session

    def map_ordered(
        self,
        executor: ThreadPoolExecutor,
        function: Callable[[_Item], _Result],
        items: Iterable[_Item],
    ) -> Iterator[_Result]:
        """Keep only N tasks submitted, fail fast, and emit source order."""
        source = iter(enumerate(items))
        pending: dict[Future[_Result], int] = {}
        buffered: dict[int, _Result] = {}
        next_output = 0

        def submit_one() -> bool:
            try:
                index, item = next(source)
            except StopIteration:
                return False
            pending[executor.submit(function, item)] = index
            return True

        for _ in range(self.concurrency):
            if not submit_one():
                break

        try:
            while pending:
                done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                try:
                    for future in done:
                        index = pending.pop(future)
                        buffered[index] = future.result()
                except BaseException:
                    self._stop.set()
                    for future in pending:
                        future.cancel()
                    raise

                while len(pending) < self.concurrency and submit_one():
                    pass

                while next_output in buffered:
                    yield buffered.pop(next_output)
                    next_output += 1
        finally:
            if pending:
                self._stop.set()
            for future in pending:
                future.cancel()

    @contextmanager
    def pool(self):
        try:
            with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
                yield executor
        finally:
            for session in self._sessions:
                session.close()
