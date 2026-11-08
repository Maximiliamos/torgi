from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.connectors.registry import cdtrf_sync, mets_sync
from bankrotai.db import Base, LotSyncRun, LotSyncSourceRun, utc_now
from bankrotai.services.ingestion_incremental import (
    filter_signature,
    restore_safe_checkpoint,
)
from bankrotai.services.ingestion_models import SourceSyncResult, SourceSyncSpec


def _sessions():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class _Restorable:
    def __init__(self, accepted: bool = True):
        self.accepted = accepted
        self.calls = []

    async def restore_checkpoint(self, filters, state):
        self.calls.append((filters, state))
        return self.accepted


def _previous_source(
    sessions,
    *,
    spec,
    error: str,
    signature: str | None = None,
):
    now = utc_now()
    checkpoint = {
        "filter_signature": signature or filter_signature(spec),
        "resume_cursor": "12:" + "a" * 64,
        "connector_checkpoint": {
            "version": 1,
            "source_system": spec.source_id,
            "expected_page": 12,
            "prior_digest": "a" * 64,
            "seen_ids": ["1", "2"],
        },
        "items_rejected": 3,
        "catalogue_candidates": 1100,
        "detail_attempts": 55,
        "detail_requests_saved": 1045,
        "retry_count": 2,
        "progress_current": 11,
        "progress_total": 100,
        "category_pages": {"scope": 11},
    }
    with sessions.begin() as session:
        session.add(
            LotSyncRun(
                id="previous-run",
                trigger_type="scheduled_incr",
                status="partial",
                total_sources=7,
                started_at=now - timedelta(minutes=20),
                finished_at=now - timedelta(minutes=1),
            )
        )
        session.add(
            LotSyncSourceRun(
                sync_run_id="previous-run",
                source_system=spec.source_id,
                status="failed",
                complete_source_run=False,
                pages_scanned=11,
                items_seen=1000,
                items_inserted=4,
                items_updated=6,
                items_unchanged=990,
                items_failed=0,
                error_message=error,
                checkpoint_json=checkpoint,
                started_at=now - timedelta(minutes=20),
                finished_at=now - timedelta(minutes=1),
            )
        )


def test_retryable_transport_failure_resumes_matching_checkpoint() -> None:
    sessions = _sessions()
    spec = SourceSyncSpec(
        "m-ets.ru",
        mets_sync.MetsSyncFilters(),
        reconcile_missing=False,
        require_source_end=True,
        allow_checkpoint_resume=True,
    )
    _previous_source(sessions, spec=spec, error="ReadTimeout: read timed out")
    result = SourceSyncResult(source_system=spec.source_id)
    result.filter_signature = filter_signature(spec)
    connector = _Restorable()

    cursor = asyncio.run(
        restore_safe_checkpoint(
            sessions, "new-run", result, spec, connector,
        )
    )

    assert cursor == "12:" + "a" * 64
    assert result.resumed_from_run_id == "previous-run"
    assert result.pages_scanned == 11
    assert result.items_seen == 1000
    assert result.detail_requests_saved == 1045
    assert result.retry_count == 3
    assert len(connector.calls) == 1


def test_parser_failure_never_resumes_checkpoint() -> None:
    sessions = _sessions()
    spec = SourceSyncSpec(
        "m-ets.ru",
        mets_sync.MetsSyncFilters(),
        allow_checkpoint_resume=True,
    )
    _previous_source(
        sessions,
        spec=spec,
        error="M-ETS systemic detail verification failure: zero verified property lots",
    )
    result = SourceSyncResult(source_system=spec.source_id)
    result.filter_signature = filter_signature(spec)
    connector = _Restorable()

    cursor = asyncio.run(
        restore_safe_checkpoint(
            sessions, "new-run", result, spec, connector,
        )
    )

    assert cursor is None
    assert connector.calls == []


def test_filter_change_invalidates_checkpoint() -> None:
    sessions = _sessions()
    old_spec = SourceSyncSpec(
        "cdtrf.ru",
        cdtrf_sync.CdtrfSyncFilters(page_size=100),
        allow_checkpoint_resume=True,
    )
    new_spec = SourceSyncSpec(
        "cdtrf.ru",
        cdtrf_sync.CdtrfSyncFilters(page_size=50),
        allow_checkpoint_resume=True,
    )
    _previous_source(
        sessions,
        spec=old_spec,
        error="ConnectionError: failed to connect",
    )
    result = SourceSyncResult(source_system=new_spec.source_id)
    result.filter_signature = filter_signature(new_spec)
    connector = _Restorable()

    assert asyncio.run(
        restore_safe_checkpoint(
            sessions, "new-run", result, new_spec, connector,
        )
    ) is None
    assert connector.calls == []


def test_mets_checkpoint_requires_previous_page_digest(monkeypatch) -> None:
    previous_ids = ["244381-1", "244382-1"]
    digest = hashlib.sha256(
        "\n".join(sorted(previous_ids)).encode("ascii")
    ).hexdigest()
    monkeypatch.setattr(
        mets_sync,
        "_request_page",
        lambda session, page, sleep, **kwargs: SimpleNamespace(text="previous"),
    )
    monkeypatch.setattr(
        mets_sync,
        "_parse_page",
        lambda text, page: {
            "total": 4,
            "observations": [
                (external_id, "100", "Объявленные торги", "Квартира")
                for external_id in previous_ids
            ],
            "next_page": 3,
        },
    )
    crawler = mets_sync.MetsSourceCrawler(
        mets_sync.MetsPublicPilot(session=object())
    )
    state = {
        "version": 1,
        "source_system": "m-ets.ru",
        "expected_page": 3,
        "prior_digest": digest,
        "seen_ids": previous_ids,
        "total": 4,
        "minimum_total": 4,
        "maximum_total": 4,
        "page_size": 2,
        "overlap_count": 0,
        "retry_count": 0,
    }

    assert asyncio.run(
        crawler.restore_checkpoint(mets_sync.MetsSyncFilters(), state)
    ) is True

    bad = dict(state)
    bad["prior_digest"] = "b" * 64
    fresh = mets_sync.MetsSourceCrawler(
        mets_sync.MetsPublicPilot(session=object())
    )
    assert asyncio.run(
        fresh.restore_checkpoint(mets_sync.MetsSyncFilters(), bad)
    ) is False


def test_cdtrf_checkpoint_requires_previous_page_digest(monkeypatch) -> None:
    previous_ids = ["1", "2"]
    digest = hashlib.sha256(
        "\n".join(sorted(previous_ids)).encode("ascii")
    ).hexdigest()
    crawler = cdtrf_sync.CdtrfSourceCrawler(SimpleNamespace())
    monkeypatch.setattr(
        crawler,
        "_fetch_catalogue_page",
        lambda filters, page: SimpleNamespace(
            status_code=200,
            content=b"{}",
            headers={"Content-Type": "application/json"},
            json=lambda: {},
        ),
    )
    monkeypatch.setattr(
        cdtrf_sync,
        "parse_cdtrf_api_page",
        lambda payload, page_num, page_size: {
            "observed_lot_ids": previous_ids,
        },
    )
    state = {
        "version": 1,
        "source_system": "cdtrf.ru",
        "expected_page": 3,
        "prior_digest": digest,
        "seen_ids": ["1", "2"],
        "total": 200,
        "pages_total": 2,
        "total_changes": 0,
        "retry_count": 0,
    }

    assert asyncio.run(
        crawler.restore_checkpoint(cdtrf_sync.CdtrfSyncFilters(), state)
    ) is True

    bad = dict(state)
    bad["prior_digest"] = "c" * 64
    fresh = cdtrf_sync.CdtrfSourceCrawler(SimpleNamespace())
    monkeypatch.setattr(
        fresh,
        "_fetch_catalogue_page",
        crawler._fetch_catalogue_page,
    )
    assert asyncio.run(
        fresh.restore_checkpoint(cdtrf_sync.CdtrfSyncFilters(), bad)
    ) is False


def test_worker_interruption_can_resume_stale_running_source_checkpoint() -> None:
    sessions = _sessions()
    spec = SourceSyncSpec(
        "cdtrf.ru",
        cdtrf_sync.CdtrfSyncFilters(page_size=100),
        allow_checkpoint_resume=True,
    )
    now = utc_now()
    checkpoint = {
        "filter_signature": filter_signature(spec),
        "resume_cursor": "8:" + "d" * 64,
        "connector_checkpoint": {
            "version": 1,
            "source_system": "cdtrf.ru",
            "expected_page": 8,
            "prior_digest": "d" * 64,
            "seen_ids": ["1", "2"],
        },
    }
    with sessions.begin() as session:
        session.add(
            LotSyncRun(
                id="interrupted-run",
                trigger_type="scheduled_incremental",
                status="failed",
                total_sources=7,
                started_at=now - timedelta(minutes=15),
                finished_at=now - timedelta(minutes=1),
                error_message="Synchronization lease expired before a new run was created",
            )
        )
        session.add(
            LotSyncSourceRun(
                sync_run_id="interrupted-run",
                source_system="cdtrf.ru",
                status="running",
                pages_scanned=7,
                items_seen=500,
                checkpoint_json=checkpoint,
                started_at=now - timedelta(minutes=15),
                progress_at=now - timedelta(minutes=2),
            )
        )

    result = SourceSyncResult(source_system=spec.source_id)
    result.filter_signature = filter_signature(spec)
    connector = _Restorable()

    cursor = asyncio.run(
        restore_safe_checkpoint(
            sessions, "new-run", result, spec, connector,
        )
    )

    assert cursor == "8:" + "d" * 64
    assert result.resumed_from_run_id == "interrupted-run"
    assert result.pages_scanned == 7
    assert result.items_seen == 500
