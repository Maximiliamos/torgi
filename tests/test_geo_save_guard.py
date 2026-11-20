from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from bankrotai.db import Base, GeoFailure, LotGeoSnapshot, ProcessedLot
from bankrotai.geo import CadastralObjectResult
from bankrotai.services import geo_backfill


@pytest.mark.parametrize('invalid', ['cached_locality', 'different_house', 'different_cadastre'])
def test_final_persistence_rejects_invalid_cached_or_fallback_result(invalid):
    from bankrotai.geo import apply_lot_geo_result
    engine=create_engine('sqlite:///:memory:');Base.metadata.create_all(engine)
    with Session(engine) as s:
        p=ProcessedLot(source_system='example',external_id='one',title='Building',category='commercial_building',
            address='г. Ярославль, ул. Победы, д. 12',region_code='76',review_status='approved',rating=78)
        s.add(p);s.commit()
        result=CadastralObjectResult(query='г. Ярославль, ул. Победы, д. 12',
            address='г. Ярославль, ул. Победы, д. 12',lat=57.6261,lon=39.8845,source='photon',confidence='high')
        if invalid=='cached_locality':
            result.query='г. Ярославль'
        elif invalid=='different_house':
            result.address='г. Ярославль, ул. Победы, д. 99'
        else:
            p.cadastral_number='76:23:010101:1';p.cadastral_numbers=[p.cadastral_number]
            result.source='nspd';result.cadastral_number='76:23:010101:99'
        assert not apply_lot_geo_result(s,p,result)
        s.flush()
        assert s.scalar(select(LotGeoSnapshot)) is None
        assert p.current_geo_lat is None and p.needs_geo_check
        assert p.review_status=='approved' and p.rating==78
        assert result.attempts[-1]['valid'] is False
    engine.dispose()


def test_a_confirmed_second_cadastre_in_a_multi_property_lot_remains_eligible():
    from bankrotai.geo import apply_lot_geo_result
    engine=create_engine('sqlite:///:memory:');Base.metadata.create_all(engine)
    with Session(engine) as s:
        p=ProcessedLot(source_system='example',external_id='one',title='Building and land',category='land',
            address='г. Ярославль, ул. Победы, д. 12',region_code='76',
            cadastral_number='76:23:010101:1',cadastral_numbers=['76:23:010101:1','76:23:010101:2'])
        s.add(p);s.commit()
        result=CadastralObjectResult(query='76:23:010101:2',cadastral_number='76:23:010101:2',
            address='г. Ярославль, ул. Победы, д. 12',lat=57.6261,lon=39.8845,source='nspd',confidence='high')
        assert apply_lot_geo_result(s,p,result)
        assert p.cadastral_number=='76:23:010101:1'
    engine.dispose()


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
