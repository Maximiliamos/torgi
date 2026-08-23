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
    fast_source_specs,
    regional_source_specs,
)


def lot(
    external_id: str = "lot-1",
    *,
    price: float = 500_000,
    region_code: str = "76",
) -> NormalizedLot:
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
        raw_data={"region_code": region_code},
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


def test_duplicate_external_id_across_pages_is_counted_once(sessions) -> None:
    service = NationwideIngestionService(sessions)
    _, result = run_with(service, FakeConnector([[lot()], [lot()]]))

    source = result["sources"][0]
    assert source["items_seen"] == 1
    assert source["items_inserted"] == 1
    assert source["items_duplicates"] == 1
    with sessions() as session:
        assert len(session.scalars(select(SourceLot)).all()) == 1


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


def test_fast_discovery_never_reconciles_missing_rows(sessions) -> None:
    service = NationwideIngestionService(sessions)
    run_with(service, FakeConnector([[lot("existing")]]))
    service.connector_factory = lambda _source: FakeConnector([[]])
    run_id = service.create_run(triggered_by="admin", trigger_type="manual_fast", total_sources=1)
    result = asyncio.run(service.run(
        run_id,
        (SourceSyncSpec("test-source", {}, reconcile_missing=False, max_batches=1),),
    ))

    assert result["sources"][0]["status"] == "success"
    assert result["sources"][0]["complete_source_run"] is False
    assert result["sources"][0]["items_archived"] == 0
    with sessions() as session:
        row = session.scalar(select(SourceLot))
        assert row is not None and row.missing_successful_runs == 0 and row.is_archived is False


def test_fast_source_specs_are_bounded_and_gis_uses_overlap_date() -> None:
    specs = fast_source_specs(gis_publish_date_from="2026-08-22")

    assert len(specs) == 4
    assert all(spec.reconcile_missing is False and spec.max_batches == 1 for spec in specs)
    assert specs[0].filters.publish_date_from == "2026-08-22"


def test_regional_run_does_not_reconcile_lots_outside_its_scope(sessions) -> None:
    service = NationwideIngestionService(sessions)
    run_with(service, FakeConnector([[lot("yaroslavl"), lot("moscow", region_code="77")]]))
    service.connector_factory = lambda _source: FakeConnector([[]])
    for _ in range(2):
        run_id = service.create_run(triggered_by="admin", trigger_type="pilot", total_sources=1)
        asyncio.run(service.run(
            run_id,
            (SourceSyncSpec("test-source", {}, archive_region_code="76"),),
        ))

    with sessions() as session:
        rows = {row.external_id: row for row in session.scalars(select(SourceLot)).all()}
        assert rows["yaroslavl"].is_archived is True
        assert rows["moscow"].is_archived is False
        assert rows["moscow"].missing_successful_runs == 0


def test_cardinality_collapse_is_not_treated_as_complete_source_run(sessions) -> None:
    service = NationwideIngestionService(sessions)
    baseline = [lot(f"lot-{index}") for index in range(20)]
    run_with(service, FakeConnector([baseline]))

    _, result = run_with(service, FakeConnector([[baseline[0]]]))

    source = result["sources"][0]
    assert source["status"] == "failed"
    assert source["complete_source_run"] is False
    assert "coverage guard" in source["error"]
    with sessions() as session:
        rows = session.scalars(select(SourceLot)).all()
        assert all(row.is_archived is False for row in rows)
        assert all(row.missing_successful_runs == 0 for row in rows)


def test_yaroslavl_pilot_builds_three_region_scoped_sources() -> None:
    specs = regional_source_specs(region_code="76", region_name="Ярославская область")

    assert tuple(spec.source_id for spec in specs) == (
        "torgi.gov.ru",
        "tbankrot.ru",
        "lot-online.ru",
    )
    assert all(spec.archive_region_code == "76" for spec in specs)
    assert specs[0].filters.subject_rf == "76"
    assert specs[1].filters.region is not None
    assert specs[2].filters.region_feature == "Ярославская область"


def test_active_database_lease_prevents_duplicate_full_sync(sessions) -> None:
    service = NationwideIngestionService(sessions)
    first = service.create_run(triggered_by="admin", trigger_type="manual", total_sources=1)
    with pytest.raises(SyncAlreadyRunningError) as exc_info:
        service.create_run(triggered_by="admin", trigger_type="manual", total_sources=1)
    assert exc_info.value.run_id == first
    with sessions() as session:
        assert session.get(LotSyncRun, first).status == "queued"
