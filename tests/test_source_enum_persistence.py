from dataclasses import asdict
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bankrotai.db import Base, LotSyncRun, SourceLot
from bankrotai.domain import NormalizedLot
from bankrotai.logic import persist_lot
from bankrotai.services.batch_persistence import persist_changed_lots_batch
from bankrotai.services.source_enum import source_enum_text
from bankrotai.services.verified_source_repair import repair_verified_source_lots


def lot():
    return NormalizedLot(external_id='test:1',source='test',source_system='test.ru',
        title='Земельный участок',description='Земля',category='land',region_slug='76',
        region_name='Ярославская область',address='Ярославская область',
        cadastral_number='76:23:0000001:123',vin=None,area=100,start_price=100,
        current_price=90,auction_status='active',lot_url='https://test.ru/1',
        source_url='https://test.ru/1',detail_level='detail',raw_data={})


@pytest.mark.parametrize('writer',['single','batch'])
def test_labelled_trade_type_is_persisted_as_text_by_both_writers(writer):
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        item=lot()
        item.raw_data['trade_type']={'id':8,'title':'Аренда/безвозмездное пользование'}
        if writer=='single':
            persist_lot(session,item,isolate_source=True)
        else:
            session.add(LotSyncRun(id='enum-test',status='running',trigger_type='benchmark',total_sources=1))
            session.flush()
            persist_changed_lots_batch(session,[item],'enum-test')
        session.commit()
        assert session.scalar(select(SourceLot)).auction_type=='Аренда/безвозмездное пользование'
    engine.dispose()


def test_partial_repair_raw_enum_does_not_break_flush_or_change_unobserved_type():
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        item=lot()
        record=persist_lot(session,item,isolate_source=True)
        record.review_status='approved'
        session.commit()
        item.raw_data={'trade_type':{'id':8,'title':'Аренда/безвозмездное пользование'},
            'repair_observed_fields':['title','detail_level'],'repair_evidence_sha256':'a'*64}
        evidence=[{'lot_id':record.id,'normalized':asdict(item),'verification':{
            'identity_verified':True,'external_id':item.external_id,'sha256':'a'*64,
            'checked_at_utc':datetime.now(timezone.utc).isoformat(),'source_url':item.source_url,
            'observed_fields':['title'],'derived_fields':['detail_level']}}]
        preview=repair_verified_source_lots(session,evidence=evidence,allowed_lot_ids={record.id})
        repair_verified_source_lots(session,evidence=evidence,allowed_lot_ids={record.id},apply=True,
            expected_states={record.id:preview['items'][0]['expected_state']},
            expected_evidence_sha256=preview['evidence_sha256'])
        session.commit()
        assert session.scalar(select(SourceLot)).auction_type is None
        assert record.review_status=='approved'
    engine.dispose()


@pytest.mark.parametrize('value',[{'id':8},['Аукцион'],True,8,None])
def test_opaque_enum_does_not_become_a_fake_label(value):
    assert source_enum_text(value) is None
