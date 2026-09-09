from __future__ import annotations

from contextlib import contextmanager
import threading
import time

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from bankrotai.core import utc_now
from bankrotai.db import Base, GeoFailure, LotGeoSnapshot, ProcessedLot
from bankrotai.geo import CadastralObjectResult
from bankrotai.services import geo_backfill


def test_geocode_pending_lots_persists_snapshot(monkeypatch) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    with scope() as session:
        lot = ProcessedLot(
            external_id="geo-backfill-1",
            source="test",
            source_system="test",
            title="Земельный участок",
            description="",
            category="land",
            region_name="Ярославская область",
            address="Ярославль, улица Свободы, 1",
            auction_status="active",
        )
        session.add(lot)
        session.add(
            ProcessedLot(
                external_id="geo-backfill-archived",
                source="test",
                source_system="test",
                title="Архивный участок",
                description="",
                category="land",
                address="Ярославль, улица Свободы, 2",
                auction_status="expired",
                is_archived=True,
            )
        )
        retry_lot = ProcessedLot(
            external_id="geo-backfill-retry",
            source="test",
            source_system="test",
            title="Старый неуспешный адрес",
            description="",
            category="land",
            address="Ярославль, неизвестный адрес",
            auction_status="active",
        )
        session.add(retry_lot)
        session.flush()
        session.add(
            GeoFailure(
                lot_id=retry_lot.id,
                status="queued",
                attempt_count=2,
                error_message="previous failure",
                last_failed_at=utc_now(),
                next_retry_at=utc_now(),
            )
        )
        lot_id = lot.id

    monkeypatch.setattr(
        geo_backfill,
        "resolve_lot_geo",
        lambda *_args, **_kwargs: CadastralObjectResult(
            query="Ярославль, улица Свободы, 1",
            lat=57.6261,
            lon=39.8845,
            source="fixture",
            confidence="high",
        ),
    )

    result = geo_backfill.geocode_pending_lots(scope, limit=1)

    assert result["queued"] == result["processed"] == result["geocoded"] == 1
    assert result["failed"] == 0
    assert result["percent"] == 100.0
    with scope() as session:
        snapshot = session.scalar(select(LotGeoSnapshot).where(LotGeoSnapshot.lot_id == lot_id))
        assert snapshot is not None
        assert snapshot.centroid_lat == 57.6261
        lot = session.get(ProcessedLot, lot_id)
        assert lot is not None
        assert lot.geo_input_hash is not None
        assert len(lot.geo_input_hash) == 64


def test_low_confidence_result_is_not_retried_until_geo_input_changes(monkeypatch) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    with scope() as session:
        lot = ProcessedLot(
            external_id="geo-backfill-low-confidence",
            source="test",
            source_system="test",
            title="Участок с неточным адресом",
            description="",
            category="land",
            address="Ярославская область",
            auction_status="active",
        )
        session.add(lot)
        session.flush()
        lot_id = lot.id

    calls = 0

    def resolve(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return CadastralObjectResult(
            query="Ярославская область",
            lat=57.6261,
            lon=39.8845,
            source="fixture",
            confidence="low",
        )

    monkeypatch.setattr(geo_backfill, "resolve_lot_geo", resolve)

    first = geo_backfill.geocode_pending_lots(scope, limit=1)
    second = geo_backfill.geocode_pending_lots(scope, limit=1)

    assert first["queued"] == first["processed"] == first["geocoded"] == 1
    assert first["failed"] == 0
    assert second["queued"] == second["processed"] == second["geocoded"] == 0
    assert second["failed"] == 0
    assert calls == 1
    with scope() as session:
        lot = session.get(ProcessedLot, lot_id)
        assert lot is not None
        assert lot.needs_geo_check is True
        assert lot.geo_input_hash is not None
        assert len(session.scalars(select(LotGeoSnapshot).where(LotGeoSnapshot.lot_id == lot_id)).all()) == 1


def test_identical_inputs_share_one_bulk_provider_call(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine) as session:
            yield session
            session.commit()

    with scope() as session:
        for external_id in ("same-1", "same-2", "same-3"):
            session.add(
                ProcessedLot(
                    external_id=external_id,
                    source="test",
                    source_system="test",
                    title="Склад",
                    description="",
                    category="commercial",
                    region_name="Москва",
                    address="Москва, улица Тверская, дом 1",
                    auction_status="active",
                )
            )

    calls = 0

    def resolve(*_args, **kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["bulk"] is True
        return CadastralObjectResult(
            query="Москва, улица Тверская, дом 1",
            lat=55.7558,
            lon=37.6176,
            source="fixture",
            confidence="high",
            address="Москва, улица Тверская, дом 1",
        )

    monkeypatch.setattr(geo_backfill, "resolve_lot_geo", resolve)
    result = geo_backfill.geocode_pending_lots(scope, limit=2, progress_task_id="geo-test")
    cached_result = geo_backfill.geocode_pending_lots(scope, limit=1, progress_task_id="geo-test-cached")

    assert calls == 1
    assert result["queued"] == result["geocoded"] == 2
    assert result["unique_queries"] == 1
    assert result["deduplicated"] == 1
    assert cached_result["cache_hits"] == 1
    assert cached_result["geocoded"] == 1
    with scope() as session:
        task = session.scalar(
            select(geo_backfill.BackgroundTaskState).where(geo_backfill.BackgroundTaskState.task_id == "geo-test")
        )
        assert task is not None
        assert task.status == "completed"
        assert task.progress_json["percent"] == 100.0


def test_distinct_bulk_queries_run_with_bounded_parallelism(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine) as session:
            yield session
            session.commit()

    with scope() as session:
        for index in range(4):
            session.add(
                ProcessedLot(
                    external_id=f"parallel-{index}",
                    source="test",
                    source_system="test",
                    title="Склад",
                    description="",
                    category="commercial",
                    region_name="Москва",
                    address=f"Москва, улица Тверская, дом {index + 1}",
                    auction_status="active",
                )
            )

    guard = threading.Lock()
    active = 0
    maximum = 0

    def resolve(_cad, address, **_kwargs):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        with guard:
            active -= 1
        return CadastralObjectResult(
            query=address,
            address=address,
            lat=55.7558,
            lon=37.6176,
            source="fixture",
            confidence="high",
        )

    monkeypatch.setattr(geo_backfill, "resolve_lot_geo", resolve)
    result = geo_backfill.geocode_pending_lots(scope, limit=4)

    assert result["geocoded"] == 4
    assert maximum >= 2
