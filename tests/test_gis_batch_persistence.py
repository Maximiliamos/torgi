from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.db import Base, CanonicalLot, LotStatusHistory, LotSyncRun, ProcessedLot, SourceLot
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


def test_batch_exact_cadastral_match_reuses_cross_source_canonical() -> None:
    factory = sessions()
    with factory() as session:
        persist_lot(session, make_lot("other-1", source_system="other.test"))
        add_run(session, "run-1")
        persist_changed_lots_batch(session, [make_lot("gis-1")], "run-1")
        session.commit()

        sources = session.scalars(select(SourceLot).order_by(SourceLot.source_system)).all()
        assert len(sources) == 2
        assert sources[0].canonical_lot_id == sources[1].canonical_lot_id
        assert all(source.processed_lot_id is not None for source in sources)
        assert session.scalar(select(func.count()).select_from(CanonicalLot)) == 1


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
        add_run(session, "run-2")
        persist_changed_lots_batch(session, [changed], "run-2")
        session.commit()

        session.refresh(processed)
        source = session.scalar(select(SourceLot))
        assert processed.title == "Проверенное название"
        assert source is not None and source.title == "Новое название источника"


def test_unknown_update_preserves_last_known_status_and_source_state() -> None:
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

        source = session.scalar(select(SourceLot))
        processed = session.scalar(select(ProcessedLot))
        assert source is not None and source.is_active is True and source.is_archived is False
        assert processed is not None and processed.auction_status == "active"
        assert session.scalar(select(func.count()).select_from(LotStatusHistory)) == 1


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
