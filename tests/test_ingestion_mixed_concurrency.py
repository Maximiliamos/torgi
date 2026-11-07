"""Offline orchestration regressions; no real connectors or production DB."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bankrotai.services import ingestion
from bankrotai.services.ingestion import NationwideIngestionService
from bankrotai.services.ingestion_models import SourceSyncResult, SourceSyncSpec


class _FakeSession:
    def commit(self):
        pass


@contextmanager
def _session_factory():
    yield _FakeSession()


def _service(*, parallelism=3):
    return NationwideIngestionService(_session_factory, max_concurrent_sources=parallelism)


def _spec(name, reconcile):
    return SourceSyncSpec(name, filters=None, reconcile_missing=reconcile)


def _run(service, specs):
    with (
        patch.object(ingestion, "reconcile_cross_source_duplicates", return_value=0),
        patch.object(service, "_expire_elapsed_auctions", return_value=0),
    ):
        return asyncio.run(service._run_active("offline-test-run", tuple(specs)))


def test_mixed_reconcile_sources_run_concurrently_but_bounded():
    service = _service(parallelism=2)
    active = 0
    peak = 0
    started = []

    async def sync(_run_id, spec):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.append(spec.source_id)
        await asyncio.sleep(0.015)
        active -= 1
        return SourceSyncResult(source_system=spec.source_id, status="success")

    service._sync_source = sync
    specs = [_spec(f"s{i}", i % 2 == 0) for i in range(7)]
    result = _run(service, specs)
    assert peak == 2
    assert len(started) == 7
    assert result["status"] == "success"
    assert [item["source_system"] for item in result["sources"]] == [
        spec.source_id for spec in specs
    ]


def test_one_failed_source_does_not_cancel_others():
    service = _service(parallelism=3)
    visited = []
    persisted_failures = []

    async def sync(_run_id, spec):
        visited.append(spec.source_id)
        if spec.source_id == "broken":
            raise RuntimeError("simulated connector setup failure")
        await asyncio.sleep(0)
        return SourceSyncResult(source_system=spec.source_id, status="success")

    service._sync_source = sync
    service._upsert_source_run = lambda _run, result, **kwargs: persisted_failures.append(result)
    result = _run(service, [_spec("ok1", True), _spec("broken", True), _spec("ok2", False)])
    assert sorted(visited) == ["broken", "ok1", "ok2"]
    assert result["status"] == "partial"
    failed = result["sources"][1]
    assert failed["status"] == "failed"
    assert failed["complete_source_run"] is False
    assert failed["error"] == "simulated connector setup failure"
    assert len(persisted_failures) == 1


def test_duplicate_source_rejected_before_any_source_starts():
    service = _service()
    called = []

    async def sync(_run_id, spec):
        called.append(spec.source_id)

    service._sync_source = sync
    with pytest.raises(ValueError, match="duplicate source_id"):
        _run(service, [_spec("same", True), _spec("same", False)])
    assert called == []


def test_reconciliation_critical_section_is_serial():
    service = _service(parallelism=7)
    in_reconciliation = 0
    maximum = 0

    async def sync(_run_id, spec):
        nonlocal in_reconciliation, maximum
        await asyncio.sleep(0)
        lock = ingestion._RECONCILIATION_LOCK.get()
        assert lock is not None
        if spec.reconcile_missing:
            async with lock:
                in_reconciliation += 1
                maximum = max(maximum, in_reconciliation)
                await asyncio.sleep(0.005)
                in_reconciliation -= 1
        return SourceSyncResult(source_system=spec.source_id, status="success")

    service._sync_source = sync
    result = _run(service, [_spec(f"s{i}", True) for i in range(7)])
    assert maximum == 1
    assert result["status"] == "success"


def test_repeated_offline_runs_do_not_leak_reconciliation_context():
    service = _service(parallelism=2)
    async def sync(_run_id, spec):
        assert ingestion._RECONCILIATION_LOCK.get() is not None
        return SourceSyncResult(source_system=spec.source_id, status="success")
    service._sync_source = sync
    for _ in range(2):
        assert _run(service, [_spec("source", True)])["status"] == "success"
    assert ingestion._RECONCILIATION_LOCK.get() is None


@pytest.mark.parametrize("parallelism", [0, -1, 8])
def test_invalid_concurrency_rejected(parallelism):
    with pytest.raises(ValueError, match="max_concurrent_sources"):
        _service(parallelism=parallelism)
