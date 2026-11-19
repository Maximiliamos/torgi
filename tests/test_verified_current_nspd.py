import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, GeoFailure, LotGeoSnapshot, LotNote, ProcessedLot, SourceLot
from bankrotai.services.verified_current_nspd import replay_current_nspd


def receipt(lot_id):
    cad = '76:13:031301:92'
    body = json.dumps({'features':[{'type':'Feature','properties':{'options':{'cad_num':cad}},
        'geometry':{'type':'Point','coordinates':[39.4,57.1]}}]}).encode()
    return {'lot_ids':[lot_id],'provider':'nspd','http_status':200,
        'checked_at_utc':datetime.now(timezone.utc).isoformat(),'cadastral_number':cad,
        'source_url':'https://nspd.gov.ru/api/geoportal/v2/search/geoportal?thematicSearchId=1&query='+cad,
        'response_sha256':hashlib.sha256(body).hexdigest(),'response_base64':base64.b64encode(body).decode()}


@pytest.fixture
def context():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        canonical = CanonicalLot(canonical_key='nspd-replay',title='Земельный участок')
        session.add(canonical)
        session.flush()
        lot = ProcessedLot(source_system='torgi.gov.ru',external_id='torgi_gov:1_1',
            title='Земельный участок',category='land',address='Ярославская область',
            region_code='76',region_name='Ярославская область',cadastral_number='76:13:031301:92',
            auction_status='active',review_status='approved',rating=78,needs_geo_check=True)
        session.add(lot)
        session.flush()
        source = SourceLot(canonical_lot_id=canonical.id,processed_lot_id=lot.id,
            source_system=lot.source_system,external_id=lot.external_id,start_price=100,raw_data={})
        note = LotNote(lot_id=lot.id,user_id='operator',content='Keep this note and review ID')
        old = LotGeoSnapshot(lot_id=lot.id,geo_source='photon',geo_method='address',
            centroid_lat=57.636,centroid_lon=39.875,metadata_json={'query':'Ярославская область'})
        session.add_all([source,note,old])
        session.commit()
        yield session,lot,source,note,old
    engine.dispose()


def test_explicit_current_replay_can_restore_reset_reviewed_lot_without_no_match_failure(context):
    session,lot,source,note,old = context
    evidence = [receipt(lot.id)]
    preview = replay_current_nspd(session,receipts=evidence)
    assert preview['accepted']==1 and lot.current_geo_lat is None and not session.dirty
    replay_current_nspd(session,receipts=evidence,apply=True,expected=preview)
    session.rollback()
    assert lot.current_geo_lat is None
    replay_current_nspd(session,receipts=evidence,apply=True,expected=preview)
    session.commit()
    assert (lot.current_geo_lat,lot.current_geo_lon)==(57.1,39.4)
    assert not lot.needs_geo_check and lot.geo_input_hash
    assert lot.review_status=='approved' and lot.rating==78
    assert note.lot_id==lot.id and source.start_price==100
    snapshots=list(session.scalars(select(LotGeoSnapshot).order_by(LotGeoSnapshot.id)))
    assert len(snapshots)==2 and snapshots[0]==old
    assert snapshots[1].metadata_json['verified_response_sha256']==evidence[0]['response_sha256']
    assert snapshots[1].source_checked_at is not None
    assert replay_current_nspd(session,receipts=evidence)['accepted']==0


@pytest.mark.parametrize('change', ['none', 'source_title', 'proof', 'expired', 'other_cadastre', 'duplicate'])
def test_frozen_owned_title_proof_is_checked_before_using_primary_geometry(context, change):
    session, lot, source, _, _ = context
    source.cadastral_number = lot.cadastral_number
    source.title = 'Земельный участок ' + lot.cadastral_number
    proof = {'sha256': 'a'*64, 'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'value': source.title}
    if change == 'expired':
        proof['checked_at_utc'] = (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    source.raw_data = {'verified_source_fields': {'title': proof}}
    confirmation = {'lot_id': lot.id, 'source_system': source.source_system,
                    'field': 'title', 'value': source.title, 'proof': proof.copy()}
    evidence = receipt(lot.id)
    evidence['owned_source_confirmations'] = [confirmation]
    if change == 'source_title':
        source.title = 'Другая публикация'
    elif change == 'proof':
        source.raw_data = {'verified_source_fields': {'title': {**proof, 'sha256': 'b'*64}}}
    elif change == 'other_cadastre':
        source.title = 'Земельный участок 76:13:031301:99'
        proof['value'] = source.title
        source.raw_data = {'verified_source_fields': {'title': proof}}
        confirmation.update(value=source.title, proof=proof.copy())
    elif change == 'duplicate':
        evidence['owned_source_confirmations'].append(confirmation.copy())
    session.commit()
    result = replay_current_nspd(session, receipts=[evidence])
    assert result['accepted'] == (1 if change == 'none' else 0)
    assert lot.current_geo_lat is None and not session.dirty


@pytest.mark.parametrize('field',['cadastre','review','source'])
def test_current_inputs_and_user_changes_invalidate_preview(context,field):
    session,lot,source,_,_ = context
    evidence=[receipt(lot.id)]
    preview=replay_current_nspd(session,receipts=evidence)
    if field=='cadastre':
        lot.cadastral_number='76:13:031301:93'
    elif field=='review':
        lot.review_status='rejected'
    else:
        source.raw_data={'typeTransaction':'RENT'}
    session.flush()
    with pytest.raises(ValueError,match='stale'):
        replay_current_nspd(session,receipts=evidence,apply=True,expected=preview)
    assert lot.current_geo_lat is None


@pytest.mark.parametrize('change',['closed','archived','point','cadastre','lease','unbound'])
def test_ineligible_current_projection_is_not_overwritten(context,change):
    session,lot,source,_,_ = context
    if change=='closed':
        lot.auction_status='closed'
    elif change=='archived':
        lot.is_archived=True
    elif change=='point':
        lot.current_geo_lat=57.2
        lot.current_geo_lon=39.5
    elif change=='cadastre':
        lot.cadastral_number='76:13:031301:93'
    elif change=='lease':
        source.raw_data={'typeTransaction':'RENT'}
    else:
        source.processed_lot_id=None
    session.commit()
    assert replay_current_nspd(session,receipts=[receipt(lot.id)])['accepted']==0


@pytest.mark.parametrize('change',['host','http','query','sha','expired'])
def test_bad_receipt_is_rejected_without_writes(context,change):
    session,lot,_,_,_ = context
    r=receipt(lot.id)
    if change=='host':
        r['source_url']=r['source_url'].replace('nspd.gov.ru','example.com')
    elif change=='http':
        r['source_url']=r['source_url'].replace('https:','http:')
    elif change=='query':
        r['source_url']=r['source_url'].replace('031301:92','031301:93')
    elif change=='sha':
        r['response_sha256']='0'*64
    else:
        r['checked_at_utc']=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat()
    with pytest.raises(ValueError):
        replay_current_nspd(session,receipts=[r])
    assert lot.current_geo_lat is None and not session.new and not session.dirty


def test_failure_state_change_invalidates_the_whole_current_geo_preview(context):
    session,lot,_,_,_=context
    failure=GeoFailure(lot_id=lot.id,status='queued',attempt_count=0,error_message='source_repair_queue')
    session.add(failure)
    session.commit()
    evidence=[receipt(lot.id)]
    preview=replay_current_nspd(session,receipts=evidence)
    failure.attempt_count=1
    session.flush()
    with pytest.raises(ValueError,match='stale'):
        replay_current_nspd(session,receipts=evidence,apply=True,expected=preview)
    assert lot.current_geo_lat is None


def test_later_stale_row_prevents_first_valid_coordinate_update(context):
    session,lot,source,_,_=context
    other=ProcessedLot(source_system=lot.source_system,external_id='torgi_gov:2_1',
        title=lot.title,category=lot.category,cadastral_number=lot.cadastral_number,
        region_code=lot.region_code,auction_status='active')
    session.add(other)
    session.flush()
    other_source=SourceLot(canonical_lot_id=source.canonical_lot_id,processed_lot_id=other.id,
        source_system=other.source_system,external_id=other.external_id,raw_data={})
    session.add(other_source)
    session.commit()
    evidence=[receipt(lot.id),receipt(other.id)]
    preview=replay_current_nspd(session,receipts=evidence)
    assert preview['accepted']==2
    other.review_status='rejected'
    session.flush()
    with pytest.raises(ValueError,match='stale'):
        replay_current_nspd(session,receipts=evidence,apply=True,expected=preview)
    assert lot.current_geo_lat is None and other.current_geo_lat is None


def test_automatic_stale_recovery_still_excludes_reviewed_rows(context):
    from bankrotai.services.nspd_evidence import nspd_result_from_receipt
    from bankrotai.services.stale_cadastral_recovery import recover_stale_cadastral_live_results
    session,lot,_,_,_=context
    failure=GeoFailure(lot_id=lot.id,status='deferred_no_match',attempt_count=1,error_message='no_match')
    session.add(failure)
    session.commit()
    evidence=[receipt(lot.id)]
    result=recover_stale_cadastral_live_results(session,lot_ids=[lot.id],
        live_result_probe=lambda _: (nspd_result_from_receipt(evidence[0]),''))
    assert result['accepted']==0
    assert replay_current_nspd(session,receipts=evidence)['accepted']==1


def bind_imprecise_current_point(context):
    session,lot,source,_,snapshot=context
    lot.current_geo_lat=snapshot.centroid_lat
    lot.current_geo_lon=snapshot.centroid_lon
    lot.current_geo_observed_at=snapshot.observed_at
    lot.current_geo_source='photon'
    lot.current_geo_confidence='high'
    source.cadastral_number=lot.cadastral_number
    snapshot.metadata_json={'query':'Ярославская область, дом 2',
                            'address':'Ярославская область, дом 4'}
    session.commit()


def test_exact_primary_geometry_replaces_proven_address_precision_defect_preserving_history(context):
    bind_imprecise_current_point(context)
    session,lot,source,note,old=context
    evidence=[receipt(lot.id)]
    preview=replay_current_nspd(session,receipts=evidence,replace_address_precision=True)
    assert preview['accepted']==1 and lot.current_geo_lat==old.centroid_lat
    result=replay_current_nspd(session,receipts=evidence,replace_address_precision=True,apply=True,expected=preview)
    session.commit()
    assert result['updated']==1 and (lot.current_geo_lat,lot.current_geo_lon)==(57.1,39.4)
    assert lot.review_status=='approved' and lot.rating==78 and note.lot_id==lot.id
    assert source.start_price==100 and session.get(LotGeoSnapshot,old.id) is not None
    assert len(list(session.scalars(select(LotGeoSnapshot))))==2


@pytest.mark.parametrize('change',['valid_address','primary_point','missing_source_cadastre','no_current_snapshot'])
def test_address_replacement_requires_own_current_defective_snapshot_and_source_cadastre(context,change):
    bind_imprecise_current_point(context)
    session,lot,source,_,snapshot=context
    if change=='valid_address':snapshot.metadata_json={'query':'Ярославская область, дом 2','address':'Ярославская область, дом 2'}
    elif change=='primary_point':snapshot.geo_source='nspd'
    elif change=='missing_source_cadastre':source.cadastral_number=None
    else:lot.current_geo_observed_at=None
    session.commit()
    assert replay_current_nspd(session,receipts=[receipt(lot.id)],replace_address_precision=True)['accepted']==0
    assert lot.current_geo_lat==snapshot.centroid_lat


def test_address_snapshot_change_invalidates_exact_replacement_preview(context):
    bind_imprecise_current_point(context)
    session,lot,_,_,snapshot=context
    evidence=[receipt(lot.id)]
    preview=replay_current_nspd(session,receipts=evidence,replace_address_precision=True)
    snapshot.metadata_json={**snapshot.metadata_json,'address':'Ярославская область, дом 6'}
    session.flush()
    with pytest.raises(ValueError,match='stale'):
        replay_current_nspd(session,receipts=evidence,replace_address_precision=True,apply=True,expected=preview)
    assert lot.current_geo_lat==snapshot.centroid_lat
