from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import perf_counter

from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.db import Base, CanonicalLot, LotPriceEvent, LotStatusHistory, LotSyncRun, ProcessedLot, SourceLot
from bankrotai.domain import NormalizedLot
from bankrotai.logic import persist_lot
from bankrotai.services.batch_persistence import persist_changed_lots_batch


def make_lot(external_id: str, source_system: str = "torgi.gov.ru") -> NormalizedLot:
    return NormalizedLot(
        external_id=external_id,
        source="torgi_gov" if source_system == "torgi.gov.ru" else "other",
        source_system=source_system,
        title="Земельный участок со зданием",
        description="Ярославская область",
        category="commercial_building_with_land",
        region_slug="76",
        region_name="Ярославская область",
        address="Ярославская область, г. Ярославль",
        cadastral_number="76:23:010101:1",
        vin=None,
        area=100,
        start_price=500_000,
        current_price=500_000,
        auction_status="active",
        lot_url=f"https://example.test/{external_id}",
        source_url=f"https://example.test/{external_id}",
        detail_level="search",
        raw_data={"region_code": "76", "category_code": "903"},
    )


def sessions():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def add_run(session, run_id: str) -> None:
    session.execute(update(LotSyncRun).where(LotSyncRun.status.in_(("queued", "running"))).values(status="success"))
    session.add(LotSyncRun(id=run_id, status="running", trigger_type="benchmark", total_sources=1))
    session.flush()


def test_batch_persistence_is_idempotent_and_keeps_processed_link() -> None:
    factory = sessions()
    with factory() as session:
        add_run(session, "run-1")
        persist_changed_lots_batch(session, [make_lot("gis-1")], "run-1")
        session.commit()
        add_run(session, "run-2")
        persist_changed_lots_batch(session, [make_lot("gis-1")], "run-2")
        session.commit()

        source = session.scalar(select(SourceLot))
        assert source is not None
        assert source.processed_lot_id is not None
        assert source.last_sync_run_id == "run-2"
        assert source.missing_successful_runs == 0
        assert session.scalar(select(func.count()).select_from(SourceLot)) == 1
        assert session.scalar(select(func.count()).select_from(ProcessedLot)) == 1
        assert session.scalar(select(func.count()).select_from(CanonicalLot)) == 1


def test_batch_shared_cadastre_without_same_publication_keeps_sales_separate() -> None:
    factory = sessions()
    with factory() as session:
        persist_lot(session, make_lot("other-1", source_system="other.test"))
        add_run(session, "run-1")
        persist_changed_lots_batch(session, [make_lot("gis-1")], "run-1")
        session.commit()

        sources = session.scalars(select(SourceLot).order_by(SourceLot.source_system)).all()
        assert len(sources) == 2
        assert sources[0].canonical_lot_id != sources[1].canonical_lot_id
        assert all(source.processed_lot_id is not None for source in sources)
        assert session.scalar(select(func.count()).select_from(CanonicalLot)) == 2


def test_batch_update_preserves_manually_reviewed_processed_lot() -> None:
    factory = sessions()
    with factory() as session:
        add_run(session, "run-1")
        persist_changed_lots_batch(session, [make_lot("gis-1")], "run-1")
        processed = session.scalar(select(ProcessedLot))
        assert processed is not None
        processed.review_status = "approved"
        processed.title = "Проверенное название"
        session.commit()

        changed = make_lot("gis-1")
        changed.title = "Новое название источника"
        changed.current_price = 450_000
        changed.auction_status = "closed"
        add_run(session, "run-2")
        persist_changed_lots_batch(session, [changed], "run-2")
        session.commit()

        session.refresh(processed)
        source = session.scalar(select(SourceLot))
        assert processed.title == "Проверенное название"
        assert float(processed.current_price or 0) == 450_000
        assert processed.auction_status == "closed"
        assert source is not None and source.title == "Новое название источника"
        price_events = session.scalars(select(LotPriceEvent).order_by(LotPriceEvent.id)).all()
        assert [(event.price_kind, float(event.amount)) for event in price_events] == [
            ("current", 500_000),
            ("current", 450_000),
        ]


def test_unknown_update_revokes_stale_active_source_state() -> None:
    factory = sessions()
    with factory() as session:
        add_run(session, "run-1")
        persist_changed_lots_batch(session, [make_lot("gis-1")], "run-1")
        session.commit()
        existing = session.scalar(select(SourceLot))
        assert existing is not None

        unknown = make_lot("gis-1")
        unknown.auction_status = "unknown"
        add_run(session, "run-2")
        persist_changed_lots_batch(
            session,
            [unknown],
            "run-2",
            existing_sources={"gis-1": existing},
        )
        session.commit()
        # Set-based upsert does not refresh already-loaded ORM objects when
        # expire_on_commit=False; assert persisted DB state, not identity cache.
        session.expire_all()
        source = session.scalar(select(SourceLot))
        processed = session.scalar(select(ProcessedLot))
        assert source is not None and source.is_active is False and source.is_archived is False
        assert source.source_status == "unknown"
        assert processed is not None and processed.auction_status == "unknown"
        assert session.scalar(select(func.count()).select_from(LotStatusHistory)) == 2


def test_status_history_records_only_real_status_changes() -> None:
    factory = sessions()
    with factory() as session:
        add_run(session, "run-1")
        persist_changed_lots_batch(session, [make_lot("gis-1")], "run-1")
        session.commit()
        existing = session.scalar(select(SourceLot))
        assert existing is not None

        closed = make_lot("gis-1")
        closed.auction_status = "closed"
        add_run(session, "run-2")
        persist_changed_lots_batch(
            session,
            [closed],
            "run-2",
            existing_sources={"gis-1": existing},
        )
        session.commit()

        histories = session.scalars(select(LotStatusHistory).order_by(LotStatusHistory.id)).all()
        assert [(row.old_status, row.new_status) for row in histories] == [
            (None, "active"),
            ("active", "closed"),
        ]
        source = session.scalar(select(SourceLot))
        session.refresh(source)
        assert source is not None and source.is_active is False and source.is_archived is True


def test_batch_update_requeues_geocoding_when_geo_input_changes() -> None:
    factory = sessions()
    with factory() as session:
        add_run(session, "run-1")
        persist_changed_lots_batch(session, [make_lot("gis-1")], "run-1")
        processed = session.scalar(select(ProcessedLot))
        assert processed is not None
        processed.geo_input_hash = "a" * 64
        processed.needs_geo_check = False
        session.commit()

        changed = make_lot("gis-1")
        changed.address = "Ярославль, улица Свободы, 1"
        add_run(session, "run-2")
        persist_changed_lots_batch(session, [changed], "run-2")
        session.commit()

        session.refresh(processed)
        assert processed.needs_geo_check is True
        assert processed.geo_input_hash is None


def test_batch_refresh_keeps_existing_canonical_even_if_legacy_key_is_cadastral() -> None:
    factory = sessions()
    with factory() as session:
        persist_lot(session, make_lot("gis-old"))
        existing = session.scalar(select(SourceLot))
        assert existing is not None
        canonical = session.get(CanonicalLot, existing.canonical_lot_id)
        assert canonical is not None
        canonical.canonical_key = "cadastral:76:23:010101:1"
        old_canonical_id = canonical.id
        session.commit()

        add_run(session, "run-legacy-1")
        changed = make_lot("gis-old")
        changed.current_price = 400_000
        persist_changed_lots_batch(session, [changed], "run-legacy-1")
        session.commit()
        session.expire_all()

        again = session.scalar(select(SourceLot))
        assert again is not None
        assert again.canonical_lot_id == old_canonical_id
        assert session.scalar(select(func.count()).select_from(CanonicalLot)) == 1
        assert float(again.current_price or 0) == 400_000


def test_cdtrf_equivalent_timezone_deadline_skips_but_real_deadline_change_persists() -> None:
    factory = sessions()
    moscow = timezone(timedelta(hours=3))
    with factory() as session:
        add_run(session, "run-deadline-1")
        first = make_lot("cdtrf-deadline", source_system="cdtrf.ru")
        first.application_deadline = datetime(2026, 12, 20, 18, 0, tzinfo=moscow)
        persist_changed_lots_batch(session, [first], "run-deadline-1")
        session.commit()

        add_run(session, "run-deadline-2")
        equivalent = make_lot("cdtrf-deadline", source_system="cdtrf.ru")
        equivalent.application_deadline = datetime(2026, 12, 20, 18, 0, tzinfo=moscow)
        same_stats = persist_changed_lots_batch(session, [equivalent], "run-deadline-2")
        session.commit()
        assert same_stats.semantic_skipped == 1
        assert same_stats.persisted == 0

        add_run(session, "run-deadline-3")
        changed = make_lot("cdtrf-deadline", source_system="cdtrf.ru")
        changed.application_deadline = datetime(2026, 12, 20, 18, 1, tzinfo=moscow)
        changed_stats = persist_changed_lots_batch(session, [changed], "run-deadline-3")
        session.commit()
        session.expire_all()

        source = session.scalar(
            select(SourceLot).where(
                SourceLot.source_system == "cdtrf.ru",
                SourceLot.external_id == "cdtrf-deadline",
            )
        )
        assert changed_stats.semantic_skipped == 0
        assert changed_stats.persisted == 1
        assert source is not None
        assert source.application_deadline == datetime(2026, 12, 20, 15, 1)


def test_semantic_skip_preserves_detail_metadata_and_advances_transport_fingerprint() -> None:
    factory = sessions()
    with factory() as session:
        add_run(session, "run-skip-1")
        first = make_lot("gis-skip")
        first.raw_data["listing_fingerprint"] = "transport-old"
        first.raw_data["detail_enrichment_status"] = "success"
        first.raw_data["detail_enrichment_version"] = 4
        persist_changed_lots_batch(session, [first], "run-skip-1")
        session.commit()

        source = session.scalar(select(SourceLot).where(SourceLot.external_id == "gis-skip"))
        assert source is not None
        source.source_updated_at = datetime(2026, 11, 8, 10, 0)
        session.commit()

        second = make_lot("gis-skip")
        second.raw_data["listing_fingerprint"] = "transport-new"
        second.raw_data["updated_at"] = "2026-11-08T09:00:00"
        add_run(session, "run-skip-2")
        stats = persist_changed_lots_batch(session, [second], "run-skip-2")
        session.commit()
        session.expire_all()

        source = session.scalar(select(SourceLot).where(SourceLot.external_id == "gis-skip"))
        assert source is not None
        assert stats.received == 1
        assert stats.semantic_skipped == 1
        assert stats.persisted == 0
        assert stats.chunks == 1
        assert source.last_sync_run_id == "run-skip-2"
        assert source.raw_data["listing_fingerprint"] == "transport-new"
        assert source.raw_data["detail_enrichment_status"] == "success"
        assert source.raw_data["detail_enrichment_version"] == 4
        assert source.source_updated_at == datetime(2026, 11, 8, 10, 0)
        assert session.scalar(select(func.count()).select_from(LotPriceEvent)) == 1
        assert session.scalar(select(func.count()).select_from(LotStatusHistory)) == 1


def test_batch_semantic_skip_sql_benchmark(capsys) -> None:
    factory = sessions()
    engine = factory.kw["bind"]
    lots = [make_lot(f"bench-{index}") for index in range(1585)]
    with factory() as session:
        add_run(session, "run-bench-1")
        persist_changed_lots_batch(session, lots, "run-bench-1")
        session.commit()

        unchanged = [make_lot(f"bench-{index}") for index in range(1585)]
        for lot in unchanged:
            lot.raw_data["listing_fingerprint"] = "transport-revision-2"
        add_run(session, "run-bench-2")

        skip_sql = 0

        def count_skip(*_args, **_kwargs) -> None:
            nonlocal skip_sql
            skip_sql += 1

        event.listen(engine, "before_cursor_execute", count_skip)
        started = perf_counter()
        skip_stats = persist_changed_lots_batch(session, unchanged, "run-bench-2")
        session.commit()
        skip_ms = (perf_counter() - started) * 1000
        event.remove(engine, "before_cursor_execute", count_skip)

        changed = [make_lot(f"bench-{index}") for index in range(1585)]
        for lot in changed:
            lot.current_price = 499_999
            lot.raw_data["listing_fingerprint"] = "transport-revision-3"
        add_run(session, "run-bench-3")

        changed_sql = 0

        def count_changed(*_args, **_kwargs) -> None:
            nonlocal changed_sql
            changed_sql += 1

        event.listen(engine, "before_cursor_execute", count_changed)
        started = perf_counter()
        changed_stats = persist_changed_lots_batch(session, changed, "run-bench-3")
        session.commit()
        changed_ms = (perf_counter() - started) * 1000
        event.remove(engine, "before_cursor_execute", count_changed)

    with capsys.disabled():
        print(
            "BATCH_PERSISTENCE_BENCHMARK "
            f"rows=1585 batch_size=500 "
            f"semantic_skip_sql={skip_sql} semantic_skip_commits=1 "
            f"semantic_skip_ms={skip_ms:.3f} "
            f"full_change_sql={changed_sql} full_change_commits=1 "
            f"full_change_ms={changed_ms:.3f}"
        )

    assert skip_stats.semantic_skipped == 1585
    assert skip_stats.persisted == 0
    assert skip_stats.chunks == 4
    assert changed_stats.semantic_skipped == 0
    assert changed_stats.persisted == 1585
    assert changed_stats.chunks == 4
    assert skip_sql == 12
    assert changed_sql == 24

