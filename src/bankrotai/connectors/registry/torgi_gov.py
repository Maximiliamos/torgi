from __future__ import annotations

import asyncio
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any

from bankrotai.connectors.base import AuctionConnector, ConnectorPage
from bankrotai.scraper_contracts import TorgiGovSearchFilters


class TorgiGovConnector(AuctionConnector):
    source_id = "torgi.gov.ru"
    capabilities = frozenset({"search"})

    def __init__(self, *, concurrency: int = 4, requests_per_second: float = 3.0) -> None:
        from bankrotai.scrapers import TorgiGovClient

        self.client = TorgiGovClient()
        self.concurrency = max(1, min(concurrency, 4))
        self._limiter = _DeterministicRateLimiter(requests_per_second)
        self._batch_clients = [
            TorgiGovClient(rate_limit=(0, 0), allow_html_fallback=False)
            for _ in range(self.concurrency)
        ]
        for client in self._batch_clients:
            client._respect_rate_limit = self._limiter.wait
        self._client_pool: queue.Queue[Any] = queue.Queue()
        for client in self._batch_clients:
            self._client_pool.put(client)
        self._page_jobs: list[tuple[str, str, int]] | None = None
        self._total_pages = 0
        self._batch_number = 0

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        normalized = filters if isinstance(filters, TorgiGovSearchFilters) else TorgiGovSearchFilters(**filters)
        categories = self._category_sequence(normalized.category_code)
        if len(categories) > 1:
            return await asyncio.to_thread(self._search_batch, normalized, categories, cursor)
        category_index, page_number = self._cursor_position(cursor, len(categories), normalized.page)
        requested_group, source_category = categories[category_index]
        normalized = replace(normalized, category_code=source_category, page=page_number)
        lots, metadata = await asyncio.to_thread(self.client.search_lots, normalized)
        metadata = {
            **metadata,
            "requested_category_group": requested_group,
            "source_category_code": source_category,
            "category_index": category_index,
            "category_count": len(categories),
        }
        if metadata.get("has_more"):
            next_cursor = self._encode_cursor(category_index, page_number + 1, len(categories))
        elif category_index + 1 < len(categories):
            next_cursor = self._encode_cursor(category_index + 1, 1, len(categories))
        else:
            next_cursor = None
        return ConnectorPage(items=lots, next_cursor=next_cursor, metadata=metadata)

    def _search_batch(
        self,
        normalized: TorgiGovSearchFilters,
        categories: list[tuple[str, str]],
        cursor: str | None,
    ) -> ConnectorPage:
        if cursor is None:
            first_jobs = [(group, category, 1) for group, category in categories]
            first_results = self._run_jobs(normalized, first_jobs)
            remaining: list[tuple[str, str, int]] = []
            for job, (_lots, metadata) in zip(first_jobs, first_results, strict=True):
                total_pages = max(1, int(metadata.get("total_pages") or 1))
                remaining.extend((job[0], job[1], page) for page in range(2, total_pages + 1))
            self._page_jobs = remaining
            self._total_pages = len(first_jobs) + len(remaining)
            jobs, results = first_jobs, first_results
            self._batch_number = 1
        else:
            if self._page_jobs is None:
                raise ValueError("GIS batch cursor cannot be resumed by a different connector instance")
            jobs = self._page_jobs[: self.concurrency]
            self._page_jobs = self._page_jobs[self.concurrency :]
            results = self._run_jobs(normalized, jobs)
            self._batch_number += 1

        items = []
        seen = set()
        category_pages: dict[str, int] = {}
        request_ms = json_decode_ms = normalize_ms = 0.0
        response_bytes = http_requests = 0
        for (group, _category, _page), (lots, metadata) in zip(jobs, results, strict=True):
            category_pages[group] = category_pages.get(group, 0) + 1
            for lot in lots:
                if lot.external_id not in seen:
                    seen.add(lot.external_id)
                    items.append(lot)
            timings = metadata.get("timings", {})
            request_ms += float(timings.get("request_ms") or 0)
            json_decode_ms += float(timings.get("json_decode_ms") or 0)
            normalize_ms += float(timings.get("normalize_ms") or 0)
            response_bytes += int(timings.get("response_bytes") or 0)
            http_requests += int(timings.get("http_requests") or 0)
        next_cursor = f"batch:{self._batch_number + 1}" if self._page_jobs else None
        return ConnectorPage(items=items, next_cursor=next_cursor, metadata={
            "pages_fetched": len(jobs),
            "category_pages": category_pages,
            "current_category": jobs[-1][0] if jobs else None,
            "total_pages": self._total_pages,
            "timings": {
                "request_ms": request_ms,
                "json_decode_ms": json_decode_ms,
                "normalize_ms": normalize_ms,
                "response_bytes": response_bytes,
                "http_requests": http_requests,
            },
        })

    def _run_jobs(
        self,
        normalized: TorgiGovSearchFilters,
        jobs: list[tuple[str, str, int]],
    ) -> list[tuple[list[Any], dict[str, Any]]]:
        def run(job: tuple[str, str, int]) -> tuple[list[Any], dict[str, Any]]:
            _group, category, page = job
            client = self._client_pool.get()
            page_filters = replace(normalized, category_code=category, page=page, page_size=10)
            try:
                for attempt in range(3):
                    try:
                        return client.search_lots(page_filters)
                    except Exception as exc:
                        message = str(exc).lower()
                        transient = any(code in message for code in ("429", "500", "502", "503", "504"))
                        if not transient or attempt == 2:
                            raise
                        time.sleep(0.5 * (2 ** attempt))
                raise RuntimeError("unreachable GIS retry state")
            finally:
                self._client_pool.put(client)

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = [pool.submit(run, job) for job in jobs]
            return [future.result() for future in futures]

    def _category_sequence(self, category_code: str | None) -> list[tuple[str, str]]:
        requested = [item.strip() for item in (category_code or "").split(",") if item.strip()]
        if not requested:
            requested = self.client.REAL_ESTATE_ROOT_CATEGORY_CODES.split(",")
        categories: list[tuple[str, str]] = []
        for group in requested:
            expanded = self.client.CATEGORY_GROUP_CODE_MAP.get(group, group)
            categories.extend((group, child.strip()) for child in expanded.split(",") if child.strip())
        return categories

    @staticmethod
    def _cursor_position(cursor: str | None, category_count: int, initial_page: int) -> tuple[int, int]:
        if not cursor:
            return 0, max(1, initial_page)
        if ":" not in cursor:
            return 0, max(1, int(cursor))
        category_index_text, page_text = cursor.split(":", 1)
        category_index = int(category_index_text)
        if category_index < 0 or category_index >= category_count:
            raise ValueError("invalid GIS category cursor")
        return category_index, max(1, int(page_text))

    @staticmethod
    def _encode_cursor(category_index: int, page_number: int, category_count: int) -> str:
        if category_count == 1:
            return str(page_number)
        return f"{category_index}:{page_number}"


class _DeterministicRateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self.interval = 1.0 / requests_per_second
        self.lock = threading.Lock()
        self.next_request_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_request_at - now)
            self.next_request_at = max(now, self.next_request_at) + self.interval
        if delay:
            time.sleep(delay)
