from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import AuditLog, Base, LotGeoSnapshot, MapDataset, MapTile, ProcessedLot
from bankrotai.services.map_builder import _dimension_coverage_failures


def coverage(*, surviving=10, invalid=None):
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        previous=MapDataset(version='20261118T100000Z-r10-bundle-s3',status='ready',
            is_current=True,published_at=datetime(2026,11,18,10))
        candidate=MapDataset(version='20261118T130000Z-r11-bundle-s3',created_at=datetime(2026,11,18,13))
        if invalid=='revision':
            previous.version='20261118T100000Z-r11-bundle-s3'
        session.add_all([previous,candidate])
        lots=[ProcessedLot(source_system='affected',external_id=f'lot:{i}',title='Land',
            category='land',region_code='76',auction_status='active') for i in range(40)]
        session.add_all(lots)
        session.flush()
        for lot in lots[:30]:
            snapshot=LotGeoSnapshot(lot_id=lot.id,geo_source='photon',geo_method='address',
                observed_at=datetime(2026,11,17),centroid_lat=57.63,centroid_lon=39.87,
                metadata_json={'query':'область Ярославская, Ярославская область'})
            if invalid=='snapshot':
                snapshot.metadata_json={'query':'г. Ярославль, улица Победы, дом 20А'}
            session.add(snapshot)
            session.flush()
            event=AuditLog(user_id='operator',action='geo_projection_policy_repair',entity_type='lot',
                entity_id=str(lot.id),created_at=datetime(2026,11,18,12),metadata_json={
                    'reason':'region_only_address','snapshot_id':snapshot.id,
                    'source_fact_changes':0,'historical_snapshots_deleted':0})
            if invalid=='old_event':
                event.created_at=datetime(2026,11,18,9)
            elif invalid=='late_event':
                event.created_at=datetime(2026,11,18,14)
            elif invalid=='actor':
                event.user_id='user'
            elif invalid=='reason':
                event.metadata_json={**event.metadata_json,'reason':'some_other_reason'}
            session.add(event)
        new=lots[30:30+surviving]
        session.add_all([MapTile(dataset_id=previous.id,z=12,x=1,y=1,etag='old',feature_count=40,
            payload_json={'features':[{'kind':'lot','id':p.id} for p in lots]}),
            MapTile(dataset_id=candidate.id,z=12,x=1,y=1,etag='new',feature_count=len(new),
            payload_json={'features':[{'kind':'lot','id':p.id} for p in new]})])
        session.commit()
        result=_dimension_coverage_failures(session,current_dataset_id=previous.id,
            new_dataset_id=candidate.id,minimum_ratio=0.5)
    engine.dispose()
    return result


def test_exact_audited_region_only_removals_are_not_a_source_outage():
    assert coverage()==[]


def test_losing_the_remaining_valid_population_still_fails_the_same_ratio():
    failures=coverage(surviving=3)
    assert any(f['dimension']=='source' and f['previous']==10 and f['required']==5
               and f['current']==3 for f in failures)


@pytest.mark.parametrize('invalid',['revision','snapshot','old_event','late_event','actor','reason'])
def test_other_losses_keep_the_original_coverage_baseline(invalid):
    failures=coverage(invalid=invalid)
    assert any(f['dimension']=='source' and f['previous']==40 and f['required']==20 for f in failures)
