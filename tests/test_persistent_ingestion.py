from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.connectors.base import AuctionConnector, ConnectorPage
from bankrotai.db import Base, LotSyncRun, SourceLot
from bankrotai.domain import NormalizedLot
from bankrotai.services.ingestion import (
    NationwideIngestionService,
    SourceSyncSpec,
    SyncAlreadyRunningError,
)


def lot(external_id: str = "lot-1", *, price: float = 500_000) -> NormalizedLot:
    return NormalizedLot(
        external_id=external_id,
        source="test",
        source_system="test-source",
        title="Квартира",
        description="Жилая недвижимость",
        category="apartment",
        region_slug="yaroslavl",
        region_name="Ярославская область",
        address="Ярославская область, г. Ярославль",
        cadastral_number="76:23:010101:1",
        vin=None,
        area=40,
        start_price=price,
        current_price=price,
        auction_status="active",
        lot_url="https://example.test/lot-1",
        source_url="https://example.test/lot-1",
        detail_level="search",
        raw_data={"region_code": "76"},
    )


class FakeConnector(AuctionConnector):
    source_id = "test-source"

    def __init__(self, pages: list[list[NormalizedLot]] | None = None, error: Exception | None = None) -> None:
        self.pages = pages or []
        self.error = error

    async def search(self, filters, cursor: str | None = None) -> ConnectorPage:
        if self.error is not None:
            raise self.error
        index = int(cursor or "1") - 1
        items = self.pages[index] if index < len(self.pages) else []
        next_cursor = str(index + 2) if index + 1 < len(self.pages) else None
        return ConnectorPage(items=items, next_cursor=next_cursor)


@pytest.fixture
def sessions():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def run_with(service: NationwideIngestionService, connector: AuctionConnector) -> tuple[str, dict]:
    service.connector_factory = lambda _source: connector
    run_id = service.create_run(triggered_by="admin", trigger_type="manual", total_sources=1)
    result = asyncio.run(service.run(run_id, (SourceSyncSpec("test-source", {}),)))
    return run_id, result


def test_streaming_sync_is_idempotent_and_persists_region_and_price(sessions) -> None:
    service = NationwideIngestionService(sessions)
    _, first = run_with(service, FakeConnector([[lot()]]))
    _, second = run_with(service, FakeConnector([[lot()]]))

    assert first["sources"][0]["items_inserted"] == 1
    assert second["sources"][0]["items_inserted"] == 0
    assert second["sources"][0]["items_updated"] == 0
    assert second["sources"][0]["items_unchanged"] == 1
    with sessions() as session:
        rows = session.scalars(select(SourceLot)).all()
        assert len(rows) == 1
        assert rows[0].region_code == "76"
        assert float(rows[0].start_price or 0) == 500_000


def test_missing_lot_archives_only_after_two_complete_successful_runs(sessions) -> None:
    service = NationwideIngestionService(sessions)
    run_with(service, FakeConnector([[lot()]]))
    run_with(service, FakeConnector([[]]))
    with sessions() as session:
        row = session.scalar(select(SourceLot))
        assert row is not None and row.is_archived is False
        assert row.missing_successful_runs == 1

    _, third = run_with(service, FakeConnector([[]]))
    assert third["sources"][0]["items_archived"] == 1
    with sessions() as session:
        row = session.scalar(select(SourceLot))
        assert row is not None and row.is_archived is True
        assert row.archive_reason == "missing_after_two_complete_syncs"


def test_failed_source_never_increments_missing_or_archives(sessions) -> None:
    service = NationwideIngestionService(sessions)
    run_with(service, FakeConnector([[lot()]]))
    _, result = run_with(service, FakeConnector(error=TimeoutError("source unavailable")))

    assert result["status"] == "failed"
    assert result["sources"][0]["complete_source_run"] is False
    with sessions() as session:
        row = session.scalar(select(SourceLot))
        assert row is not None and row.is_archived is False
        assert row.missing_successful_runs == 0


def test_active_database_lease_prevents_duplicate_full_sync(sessions) -> None:
    service = NationwideIngestionService(sessions)
    first = service.create_run(triggered_by="admin", trigger_type="manual", total_sources=1)
    with pytest.raises(SyncAlreadyRunningError) as exc_info:
        service.create_run(triggered_by="admin", trigger_type="manual", total_sources=1)
    assert exc_info.value.run_id == first
    with sessions() as session:
        assert session.get(LotSyncRun, first).status == "queued"
