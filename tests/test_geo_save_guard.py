from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from bankrotai.db import Base, GeoFailure, LotGeoSnapshot, ProcessedLot
from bankrotai.geo import CadastralObjectResult
from bankrotai.services import geo_backfill


@pytest.mark.parametrize("isolated", [False, True])
@pytest.mark.parametrize("change", ["address", "cadastres", "archived", "price"])
def test_source_changes_during_lookup_cannot_attach_stale_coordinates(monkeypatch, isolated, change):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine) as session:
            yield session

    with scope() as session:
        lot = ProcessedLot(
            external_id="geo-race", source="test", source_system="test",
            title="Земельный участок", description="", category="land",
            address="Ярославль, улица Свободы, 1", region_name="Ярославская область",
            auction_status="active", needs_geo_check=True,
        )
        session.add(lot)
        session.commit()
        lot_id = lot.id

    def resolve(*args, **kwargs):
        with scope() as session:
            lot = session.get(ProcessedLot, lot_id)
            if change == "address":
                lot.address = "Ярославль, улица Свободы, 99"
            elif change == "cadastres":
                lot.cadastral_numbers = ["76:13:031301:92"]
            elif change == "archived":
                lot.is_archived = True
            else:
                lot.start_price = 100000
            session.commit()
        return CadastralObjectResult(
            query="Ярославль, улица Свободы, 1", lat=57.6261, lon=39.8845,
            source="fixture", confidence="high",
        )

    monkeypatch.setattr(geo_backfill, "resolve_lot_geo", resolve)
    if isolated:
        def fail_chunk(*args, **kwargs):
            raise RuntimeError("Exercise per-item fallback")
        monkeypatch.setattr(geo_backfill, "_save_geo_chunk", fail_chunk)
    result = geo_backfill.geocode_pending_lots(scope, limit=1)
    with scope() as session:
        lot = session.get(ProcessedLot, lot_id)
        snapshots = list(session.scalars(select(LotGeoSnapshot)))
        failures = list(session.scalars(select(GeoFailure)))
        assert not failures
        if change == "price":
            assert result["geocoded"] == 1
            assert len(snapshots) == 1
            assert lot.current_geo_lat == 57.6261
        else:
            assert result["geocoded"] == 0
            assert result["failure_reasons"] == {"geo_input_changed": 1}
            assert not snapshots
            assert lot.current_geo_lat is None
            assert lot.geo_input_hash is None
            assert lot.needs_geo_check
