import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, LotGeoSnapshot, ProcessedLot, SourceLot
from bankrotai.geo import CadastralObjectResult, apply_lot_geo_result
from bankrotai.services.geographic_source_identity import geographic_source_identity_available, geographic_source_identity_condition


@pytest.mark.parametrize("proof", ["absent", "price_only", "cadastre", "address", "foreign_field"])
def test_legacy_tbankrot_requires_observed_inputs_not_just_a_synthetic_source_link(proof):
    engine=create_engine("sqlite:///:memory:");Base.metadata.create_all(engine)
    with Session(engine) as s:
        p=ProcessedLot(source_system="tbankrot.ru",external_id="tbankrot:123456",source="tbankrot",
            title="Land",category="land",cadastral_number="76:23:010101:1",address="Ярославль, улица Победы, дом 12",
            lot_url="https://tbankrot.ru/item?id=123456",review_status="approved",rating=8.5)
        s.add(p);s.flush()
        if proof!="absent":
            c=CanonicalLot(canonical_key="test",title="Land",category="land");s.add(c);s.flush()
            name="cadastral_number" if proof in {"cadastre","foreign_field"} else "address" if proof=="address" else "start_price"
            value=getattr(p,name)
            if proof=="foreign_field":value="77:01:010101:2"
            s.add(SourceLot(processed_lot_id=p.id,canonical_lot_id=c.id,source_system=p.source_system,external_id=p.external_id,
                cadastral_number=p.cadastral_number,address=p.address,
                raw_data={"verified_source_fields":{name:{"value":value,"sha256":"a"*64,"checked_at_utc":"2026-11-19T10:00:00+00:00"}}}))
        s.commit()
        expected=proof in {"cadastre","address"}
        assert geographic_source_identity_available(s,p)==expected
        assert (s.scalar(select(ProcessedLot.id).where(geographic_source_identity_condition())) is not None)==expected
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
    engine.dispose()
