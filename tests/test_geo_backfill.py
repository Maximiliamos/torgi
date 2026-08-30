from __future__ import annotations

from contextlib import contextmanager

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
        session.add(ProcessedLot(
            external_id="geo-backfill-archived",
            source="test",
            source_system="test",
            title="Архивный участок",
            description="",
            category="land",
            address="Ярославль, улица Свободы, 2",
            auction_status="expired",
            is_archived=True,
        ))
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
        session.add(GeoFailure(
            lot_id=retry_lot.id,
            status="queued",
            attempt_count=2,
            error_message="previous failure",
            last_failed_at=utc_now(),
            next_retry_at=utc_now(),
        ))
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

    assert result == {"queued": 1, "geocoded": 1, "failed": 0}
    with scope() as session:
        snapshot = session.scalar(select(LotGeoSnapshot).where(LotGeoSnapshot.lot_id == lot_id))
        assert snapshot is not None
        assert snapshot.centroid_lat == 57.6261
        lot = session.get(ProcessedLot, lot_id)
        assert lot is not None
        assert lot.geo_input_hash is not None
        assert len(lot.geo_input_hash) == 64
