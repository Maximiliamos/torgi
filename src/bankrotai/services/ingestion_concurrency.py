"""Bounded orchestration for independent nationwide source runs."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from bankrotai.db import utc_now
from bankrotai.services.ingestion_models import SourceSyncResult, SourceSyncSpec

logger = logging.getLogger(__name__)

_RECONCILIATION_LOCK: ContextVar[asyncio.Lock | None] = ContextVar(
    "ingestion_reconciliation_lock", default=None,
)


def current_reconciliation_lock() -> asyncio.Lock | None:
    """Return the per-nationwide-run reconciliation lock for this task."""
    return _RECONCILIATION_LOCK.get()


async def run_serialized_reconciliation(action: Callable[[], int]) -> int:
    """Serialize fail-closed missing-lot archival within one nationwide run."""
    lock = current_reconciliation_lock()
    if lock is None:
        return action()
    async with lock:
        return action()


async def run_bounded_sources(
    run_id: str,
    specs: tuple[SourceSyncSpec, ...],
    *,
    max_concurrent_sources: int,
    sync_source: Callable[[str, SourceSyncSpec], Awaitable[SourceSyncResult]],
    checkpoint_writer: Callable[..., None],
) -> list[SourceSyncResult]:
    """Run source scans concurrently while preserving order and fault isolation."""
    source_ids = [spec.source_id for spec in specs]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("duplicate source_id in nationwide ingestion specs")

    semaphore = asyncio.Semaphore(max_concurrent_sources)
    reconciliation_lock = asyncio.Lock()

    async def execute(spec: SourceSyncSpec) -> SourceSyncResult:
        async with semaphore:
            try:
                return await sync_source(run_id, spec)
            except Exception as exc:
                # Setup/checkpoint failures outside _sync_source must not cancel
                # independent sibling sources.
                logger.exception("Uncaught ingestion failure for %s", spec.source_id)
                failed = SourceSyncResult(
                    source_system=spec.source_id,
                    status="failed",
                    complete_source_run=False,
                    phase="failed",
                    error=str(exc),
                )
                try:
                    checkpoint_writer(run_id, failed, finished_at=utc_now())
                except Exception:
                    logger.exception(
                        "Could not persist failed source checkpoint for %s",
                        spec.source_id,
                    )
                return failed

    token = _RECONCILIATION_LOCK.set(reconciliation_lock)
    try:
        return list(await asyncio.gather(*(execute(spec) for spec in specs)))
    finally:
        _RECONCILIATION_LOCK.reset(token)
