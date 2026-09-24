from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.connectors.base import AuctionConnector, ConnectorPage, json_safe_value
from bankrotai.db import (
    Base,
    CanonicalLot,
    LotNote,
    LotPriceEvent,
    LotSyncRun,
    ProcessedLot,
    SourceLot,
    Watchlist,
)
from bankrotai.domain import NormalizedLot
from bankrotai.logic import persist_lot
from bankrotai.services.ingestion import (
    NationwideIngestionService,
    SourceSyncResult,
    SourceSyncSpec,
    SyncAlreadyRunningError,
    fast_source_specs,
    regional_source_specs,
    source_full_specs,
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


def test_connector_evidence_datetimes_are_json_safe() -> None:
    value = json_safe_value({"started": datetime(2026, 8, 30, 12, 30), "nested": [datetime(2026, 9, 1, 9, 0)]})

    assert value == {"started": "2026-08-30T12:30:00", "nested": ["2026-09-01T09:00:00"]}


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


def test_non_gis_source_uses_bounded_set_based_persistence(sessions) -> None:
    service = NationwideIngestionService(sessions, profile_timings=True)
    items = [lot(f"lot-{index}") for index in range(25)]

    _, result = run_with(service, FakeConnector([items]))

    source = result["sources"][0]
    assert source["items_inserted"] == 25
    assert source["profile"]["sql_statements"] < 20


def test_changed_geo_input_resets_hash_and_requeues_lot(sessions) -> None:
    with sessions.begin() as session:
        processed = persist_lot(session, lot())
        processed.geo_input_hash = "a" * 64
        processed.needs_geo_check = False

    changed = lot()
    changed.address = "Ярославль, улица Свободы, 1"
    with sessions.begin() as session:
        processed = persist_lot(session, changed)
        assert processed.needs_geo_check is True
        assert processed.geo_input_hash is None


def test_legacy_persistence_records_only_actual_current_price_changes(sessions) -> None:
    with sessions.begin() as session:
        persist_lot(session, lot(price=500_000))
        persist_lot(session, lot(price=500_000))
        persist_lot(session, lot(price=475_000))

    with sessions() as session:
        events = session.scalars(select(LotPriceEvent).order_by(LotPriceEvent.id)).all()
        assert [(event.price_kind, float(event.amount)) for event in events] == [
            ("current", 500_000),
            ("current", 475_000),
        ]


@pytest.mark.parametrize("source_id", ["lot-online.ru", "torgi-russia.ru", "future-detail-source"])
def test_detail_sources_enrich_only_new_or_changed_listings(sessions, source_id: str) -> None:
    class LotOnlineConnector(FakeConnector):
        capabilities = frozenset({"search", "detail_enrichment"})

        def __init__(self) -> None:
            super().__init__()
            self.source_id = source_id
            self.enrichment_calls = 0
            self.listing_fingerprint = "stable"

        async def search(self, filters, cursor: str | None = None) -> ConnectorPage:
            item = lot(f"{source_id}:1")
            item.source = source_id
            item.source_system = self.source_id
            item.description = item.title
            item.address = None
            item.cadastral_number = None
            item.raw_data = {"region_code": "76", "listing_fingerprint": self.listing_fingerprint}
            return ConnectorPage(items=[item])

        async def enrich_lot(self, item: NormalizedLot) -> NormalizedLot:
            self.enrichment_calls += 1
            item.address = "Ярославль, ул. Свободы, 1"
            item.cadastral_number = "76:23:010101:99"
            item.description = "Полное описание карточки"
            item.detail_level = "detail"
            item.raw_data["detail_enrichment_status"] = "success"
            return item

    connector = LotOnlineConnector()
    service = NationwideIngestionService(sessions, connector_factory=lambda _source: connector)
    for _ in range(2):
        run_id = service.create_run(triggered_by="admin", trigger_type="manual", total_sources=1)
        asyncio.run(service.run(run_id, (SourceSyncSpec(source_id, {}),)))

    assert connector.enrichment_calls == 1
    connector.detail_enrichment_version = 2
    run_id = service.create_run(triggered_by="admin", trigger_type="manual", total_sources=1)
    asyncio.run(service.run(run_id, (SourceSyncSpec(source_id, {}),)))
    assert connector.enrichment_calls == 2
    connector.listing_fingerprint = "changed"
    run_id = service.create_run(triggered_by="admin", trigger_type="manual", total_sources=1)
    asyncio.run(service.run(run_id, (SourceSyncSpec(source_id, {}),)))
    assert connector.enrichment_calls == 3
    with sessions() as session:
        row = session.scalar(select(SourceLot))
        assert row is not None
        assert row.address == "Ярославль, ул. Свободы, 1"
        assert row.cadastral_number == "76:23:010101:99"
        assert row.description == "Полное описание карточки"
        assert row.raw_data["detail_enrichment_status"] == "success"


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


def test_auction_start_does_not_archive_lot_and_explicit_closed_status_does(sessions) -> None:
    now = datetime(2026, 8, 24, 12, 0)
    with sessions() as session:
        old_processed = ProcessedLot(
            external_id="expiring",
            source="test",
            source_system="test",
            title="Лот",
            description="",
            category="land",
            auction_status="active",
        )
        current_processed = ProcessedLot(
            external_id="current",
            source="test",
            source_system="test",
            title="Лот",
            description="",
            category="land",
            auction_status="active",
        )
        session.add_all([old_processed, current_processed])
        session.flush()
        old_processed.review_status = "approved"
        session.add_all(
            [
                LotNote(lot_id=old_processed.id, user_id="operator", content="keep"),
                Watchlist(lot_id=old_processed.id, user_id="operator"),
            ]
        )
        # The active cross-source card was previously deduplicated under the
        # older primary.  Closing that primary must promote this sibling or
        # MapDataset would filter both rows out.
        current_processed.duplicate_of_id = old_processed.id
        canonical = CanonicalLot(
            canonical_key="expiration-test",
            legacy_processed_lot_id=old_processed.id,
            title="Лот",
            category="land",
        )
        session.add(canonical)
        session.flush()
        session.add_all(
            [
                SourceLot(
                    canonical_lot_id=canonical.id,
                    processed_lot_id=old_processed.id,
                    source_system="old-source",
                    external_id="old",
                    title="Лот",
                    category="land",
                    source_status="closed",
                    auction_at=now - timedelta(minutes=16),
                ),
                SourceLot(
                    canonical_lot_id=canonical.id,
                    processed_lot_id=current_processed.id,
                    source_system="current-source",
                    external_id="current",
                    title="Лот",
                    category="land",
                    source_status="active",
                    auction_at=now - timedelta(minutes=14),
                ),
            ]
        )
        session.commit()

    service = NationwideIngestionService(sessions)
    assert service._expire_elapsed_auctions(now=now) == 1
    with sessions() as session:
        rows = {row.external_id: row for row in session.scalars(select(SourceLot)).all()}
        processed = session.scalar(select(ProcessedLot).where(ProcessedLot.external_id == "current"))
        expired = session.scalar(select(ProcessedLot).where(ProcessedLot.external_id == "expiring"))
        assert rows["old"].archive_reason == "explicit_source_status_closed"
        assert rows["current"].is_active is True
        assert processed is not None and processed.is_archived is False and processed.duplicate_of_id is None
        assert processed.review_status == "approved"
        assert expired is not None and expired.is_archived is True and expired.duplicate_of_id == processed.id
        assert session.scalar(select(LotNote.lot_id)) == processed.id
        assert session.scalar(select(Watchlist.lot_id)) == processed.id

    assert service._expire_elapsed_auctions(now=now + timedelta(minutes=2)) == 0
    with sessions() as session:
        processed = session.scalar(select(ProcessedLot).where(ProcessedLot.external_id == "current"))
        assert processed is not None and processed.is_archived is False
        current = session.scalar(select(SourceLot).where(SourceLot.external_id == "current"))
        assert current is not None and current.is_active is True


def test_public_offer_is_not_archived_at_intermediate_stage_boundary(sessions) -> None:
    now = datetime(2026, 9, 21, 12, 0)
    with sessions() as session:
        processed = ProcessedLot(
            external_id="public-offer",
            source="test",
            source_system="test",
            title="Лот",
            description="",
            category="land",
            auction_status="active",
        )
        session.add(processed)
        session.flush()
        canonical = CanonicalLot(
            canonical_key="public-offer-test",
            legacy_processed_lot_id=processed.id,
            title="Лот",
            category="land",
        )
        session.add(canonical)
        session.flush()
        session.add(
            SourceLot(
                canonical_lot_id=canonical.id,
                processed_lot_id=processed.id,
                source_system="tbankrot.ru",
                external_id="tbankrot:7991236",
                title="Лот",
                category="land",
                source_status="active",
                auction_type="public_offer",
                next_price_reduction_at=now - timedelta(days=7),
                public_offer_schedule=[
                    {"starts_at": "2026-09-08T10:00:00", "ends_at": "2026-09-14T10:00:00", "price": 4_500_000},
                    {"starts_at": "2026-09-14T10:00:00", "ends_at": "2026-09-21T10:00:00", "price": 4_275_000},
                    {"starts_at": "2026-09-21T10:00:00", "ends_at": "2026-09-28T10:00:00", "price": 4_050_000},
                ],
            )
        )
        session.commit()

    service = NationwideIngestionService(sessions)

    assert service._expire_elapsed_auctions(now=now) == 0
    with sessions() as session:
        row = session.scalar(select(SourceLot))
        assert row is not None and row.is_active is True and row.is_archived is False


def test_public_offer_archives_only_after_final_schedule_period(sessions) -> None:
    now = datetime(2026, 9, 29, 12, 0)
    with sessions() as session:
        processed = ProcessedLot(
            external_id="ended-public-offer",
            source="test",
            source_system="test",
            title="Лот",
            description="",
            category="land",
            auction_status="active",
        )
        session.add(processed)
        session.flush()
        canonical = CanonicalLot(
            canonical_key="ended-public-offer-test",
            legacy_processed_lot_id=processed.id,
            title="Лот",
            category="land",
        )
        session.add(canonical)
        session.flush()
        session.add(
            SourceLot(
                canonical_lot_id=canonical.id,
                processed_lot_id=processed.id,
                source_system="test",
                external_id="ended",
                title="Лот",
                category="land",
                source_status="active",
                auction_type="public_offer",
                public_offer_schedule=[
                    {"starts_at": "2026-09-14T10:00:00", "ends_at": "2026-09-21T10:00:00", "price": 100},
                    {"starts_at": "2026-09-21T10:00:00", "ends_at": "2026-09-28T10:00:00", "price": 90},
                ],
            )
        )
        session.commit()

    service = NationwideIngestionService(sessions)

    assert service._expire_elapsed_auctions(now=now) == 1
    with sessions() as session:
        row = session.scalar(select(SourceLot))
        assert row is not None and row.is_archived is True
        assert row.archive_reason == "public_offer_schedule_ended"


def test_source_fingerprint_detects_schedule_and_gallery_changes() -> None:
    row = SourceLot(
        canonical_lot_id=1,
        source_system="test-source",
        external_id="one",
        title="Лот",
        public_offer_schedule=[{"price": 100}],
        next_interval_price=90,
        raw_data={"image_urls": ["https://example.test/one.jpg"]},
    )
    original = NationwideIngestionService._source_fingerprint(row)

    row.public_offer_schedule = [{"price": 90}]
    assert NationwideIngestionService._source_fingerprint(row) != original

    row.public_offer_schedule = [{"price": 100}]
    row.raw_data = {"image_urls": ["https://example.test/two.jpg"]}
    assert NationwideIngestionService._source_fingerprint(row) != original


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
    result = asyncio.run(
        service.run(
            run_id,
            (SourceSyncSpec("test-source", {}, reconcile_missing=False, max_batches=1),),
        )
    )

    assert result["sources"][0]["status"] == "success"
    assert result["sources"][0]["complete_source_run"] is False
    assert result["sources"][0]["items_archived"] == 0
    with sessions() as session:
        row = session.scalar(select(SourceLot))
        assert row is not None and row.missing_successful_runs == 0 and row.is_archived is False


def test_fast_source_specs_are_bounded_and_gis_uses_overlap_date() -> None:
    specs = fast_source_specs(gis_publish_date_from="2026-08-22")

    assert len(specs) == 5
    assert all(spec.reconcile_missing is False and spec.max_batches == 1 for spec in specs)
    assert specs[0].filters.publish_date_from == "2026-08-22"


def test_source_full_specs_preserve_complete_reconciliation() -> None:
    specs = source_full_specs("bidexpert.ru")
    assert len(specs) == 1
    assert specs[0].source_id == "bidexpert.ru"
    assert specs[0].reconcile_missing is True
    assert specs[0].max_batches is None


def test_source_full_specs_reject_unknown_source() -> None:
    with pytest.raises(ValueError, match="Unsupported source-only"):
        source_full_specs("unknown.example")


def test_fast_sources_execute_concurrently(sessions, monkeypatch) -> None:
    service = NationwideIngestionService(sessions)

    async def delayed(_run_id, spec):
        await asyncio.sleep(0.12)
        return SourceSyncResult(source_system=spec.source_id, status="success")

    monkeypatch.setattr(service, "_sync_source", delayed)
    specs = (
        SourceSyncSpec("source-a", {}, reconcile_missing=False, max_batches=1),
        SourceSyncSpec("source-b", {}, reconcile_missing=False, max_batches=1),
    )
    run_id = service.create_run(triggered_by="admin", trigger_type="manual_fast", total_sources=2)
    started = time.perf_counter()
    result = asyncio.run(service.run(run_id, specs))

    assert time.perf_counter() - started < 0.2
    assert result["status"] == "success"


def test_regional_run_does_not_reconcile_lots_outside_its_scope(sessions) -> None:
    service = NationwideIngestionService(sessions)
    run_with(service, FakeConnector([[lot("yaroslavl"), lot("moscow", region_code="77")]]))
    service.connector_factory = lambda _source: FakeConnector([[]])
    for _ in range(2):
        run_id = service.create_run(triggered_by="admin", trigger_type="pilot", total_sources=1)
        asyncio.run(
            service.run(
                run_id,
                (SourceSyncSpec("test-source", {}, archive_region_code="76"),),
            )
        )

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


def test_database_constraint_prevents_two_active_sync_runs(sessions) -> None:
    now = datetime.now()
    with sessions() as session:
        session.add(
            LotSyncRun(
                id="active-1",
                trigger_type="manual",
                status="queued",
                total_sources=1,
                lease_expires_at=now + timedelta(minutes=5),
            )
        )
        session.commit()

    with sessions() as session:
        session.add(
            LotSyncRun(
                id="active-2",
                trigger_type="scheduled",
                status="running",
                total_sources=1,
                lease_expires_at=now + timedelta(minutes=5),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_expired_sync_run_is_failed_before_replacement(sessions) -> None:
    with sessions() as session:
        session.add(
            LotSyncRun(
                id="expired",
                trigger_type="manual",
                status="running",
                total_sources=1,
                lease_expires_at=datetime(2000, 1, 1),
            )
        )
        session.commit()

    replacement = NationwideIngestionService(sessions).create_run(
        triggered_by="admin", trigger_type="manual", total_sources=1
    )

    with sessions() as session:
        expired = session.get(LotSyncRun, "expired")
        assert expired.status == "failed"
        assert expired.finished_at is not None
        assert session.get(LotSyncRun, replacement).status == "queued"
