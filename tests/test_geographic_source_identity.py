import pytest
from datetime import datetime
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, LotGeoSnapshot, MapDataset, ProcessedLot, SourceLot
from bankrotai.geo import CadastralObjectResult, apply_lot_geo_result
from bankrotai.services.geographic_source_identity import geographic_source_identity_available, geographic_source_identity_condition


@pytest.mark.parametrize("proof", ["absent", "price_only", "cadastre", "address", "foreign_field", "unbound_source"])
def test_legacy_tbankrot_requires_observed_inputs_not_just_a_synthetic_source_link(proof):
    engine=create_engine("sqlite:///:memory:");Base.metadata.create_all(engine)
    with Session(engine) as s:
        p=ProcessedLot(source_system="tbankrot.ru",external_id="tbankrot:123456",source="tbankrot",
            title="Land",category="land",cadastral_number="76:23:010101:1",address="Ярославль, улица Победы, дом 12",
            lot_url="https://tbankrot.ru/item?id=123456",review_status="approved",rating=8.5)
        s.add(p);s.flush()
        if proof!="absent":
            c=CanonicalLot(canonical_key="test",title="Land",category="land");s.add(c);s.flush()
            name="cadastral_number" if proof in {"cadastre","foreign_field","unbound_source"} else "address" if proof=="address" else "start_price"
            value=getattr(p,name)
            if proof=="foreign_field":value="77:01:010101:2"
            s.add(SourceLot(processed_lot_id=None if proof=="unbound_source" else p.id,canonical_lot_id=c.id,source_system=p.source_system,external_id=p.external_id,
                cadastral_number=p.cadastral_number,address=p.address,
                raw_data={"verified_source_fields":{name:{"value":value,"sha256":"a"*64,"checked_at_utc":"2026-11-19T10:00:00+00:00"}}}))
        s.commit()
        expected=proof in {"cadastre","address"}
        assert geographic_source_identity_available(s,p)==expected
        assert (s.scalar(select(ProcessedLot.id).where(geographic_source_identity_condition())) is not None)==expected
        from bankrotai.services.map_property_eligibility import map_property_evidence_columns, map_property_eligible, map_source_join_condition
        from bankrotai.services.map_audited_transition import audited_transition_ids
        values=s.execute(select(*map_property_evidence_columns()).select_from(ProcessedLot).outerjoin(SourceLot,map_source_join_condition())).one()._mapping
        assert map_property_eligible(values)==expected
        previous=MapDataset(version="old-r11",published_at=datetime(2026,11,18))
        candidate=MapDataset(version="new-r12",created_at=datetime(2026,11,20))
        s.add_all([previous,candidate]);s.flush()
        retired=audited_transition_ids(s,previous=previous,candidate=candidate,
            old_points={p.id:{"lat":57.6,"lon":39.8}},new_ids=set())
        assert (p.id in retired)==(not expected)
        previous.version="old-r12"
        assert not audited_transition_ids(s,previous=previous,candidate=candidate,
            old_points={p.id:{"lat":57.6,"lon":39.8}},new_ids=set())
        result=CadastralObjectResult(query=p.cadastral_number,cadastral_number=p.cadastral_number,
            lat=57.6,lon=39.8,source="nspd",confidence="high")
        saved=apply_lot_geo_result(s,p,result)
        assert saved==(proof=="cadastre")
        assert s.scalar(select(func.count()).select_from(LotGeoSnapshot))==(1 if saved else 0)
        assert p.review_status=="approved" and p.rating==8.5
    engine.dispose()


@pytest.mark.parametrize("source,external,expected", [("manual","manual:one",True), ("sistematorg.com","sistematorg.com:12345",False), ("sistematorg.com","sistematorg.com:12345:2",True)])
def test_null_safe_manual_and_individual_inputs_keep_their_existing_contract(source,external,expected):
    engine=create_engine("sqlite:///:memory:");Base.metadata.create_all(engine)
    with Session(engine) as s:
        p=ProcessedLot(source_system=source,external_id=external,title="Land",category="land")
        s.add(p);s.commit()
        assert geographic_source_identity_available(s,p)==expected
        assert (s.scalar(select(ProcessedLot.id).where(geographic_source_identity_condition())) is not None)==expected
    engine.dispose()



def test_explicit_manual_point_remains_available_for_an_unbound_card():
    engine=create_engine("sqlite:///:memory:");Base.metadata.create_all(engine)
    with Session(engine) as s:
        p=ProcessedLot(source_system="tbankrot.ru",external_id="tbankrot:123456",title="Land",category="land",lot_url="https://tbankrot.ru/item?id=123456")
        s.add(p);s.commit()
        result=CadastralObjectResult(query="manual",lat=57.6,lon=39.8,source="manual",confidence="high")
        assert geographic_source_identity_available(s,p,result)
        assert apply_lot_geo_result(s,p,result)
        from bankrotai.services.map_property_eligibility import map_property_evidence_columns, map_property_eligible, map_source_join_condition
        values=s.execute(select(*map_property_evidence_columns()).select_from(ProcessedLot).outerjoin(SourceLot,map_source_join_condition())).one()._mapping
        assert map_property_eligible(values)
    engine.dispose()


@pytest.mark.parametrize('manual', [False, True])
def test_unverified_old_card_keeps_its_facts_without_exposing_unbound_coordinates(manual):
    from datetime import datetime
    from bankrotai.services.map_view import build_map_lot_detail
    engine=create_engine('sqlite:///:memory:');Base.metadata.create_all(engine)
    with Session(engine) as s:
        p=ProcessedLot(source_system='tbankrot.ru',external_id='tbankrot:123456',title='Apartment',category='apartment',
            lot_url='https://tbankrot.ru/item?id=123456',current_geo_lat=57.6,current_geo_lon=39.8,
            current_geo_source='manual' if manual else 'photon',current_geo_observed_at=datetime(2026,11,18),
            review_status='approved',rating=78)
        s.add(p);s.commit()
        payload=build_map_lot_detail(s,p.id)
        assert payload is not None and payload['title']=='Apartment' and payload['review_status']=='approved'
        assert payload['lat']==(57.6 if manual else None)
        assert payload['lon']==(39.8 if manual else None)
        assert p.current_geo_lat==57.6 and p.review_status=='approved' and p.rating==78
    engine.dispose()
