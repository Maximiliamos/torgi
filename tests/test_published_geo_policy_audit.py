from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bankrotai.db import AuditLog, Base, LotGeoSnapshot, MapDataset, MapTile, ProcessedLot
from bankrotai.services.published_geo_policy_audit import audit_published_geo_points


@pytest.fixture
def context():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        p = ProcessedLot(source_system="example", external_id="one", title="Land", category="land",
            review_status="approved", rating=78, current_geo_lat=58.1, current_geo_lon=40.2)
        dataset = MapDataset(version="old-r11", is_current=True, published_at=datetime(2026, 11, 18, 10))
        s.add_all([p, dataset]); s.flush()
        g = LotGeoSnapshot(lot_id=p.id, observed_at=datetime(2026, 11, 17), geo_source="photon", geo_method="address",
            centroid_lat=57.6, centroid_lon=39.8, metadata_json={"query": "г. Ярославль", "address": "г. Ярославль"})
        tile = MapTile(dataset_id=dataset.id, z=12, x=1, y=1, etag="old", feature_count=1,
            payload_json={"features": [{"kind": "lot", "id": p.id, "lat": 57.6, "lon": 39.8}]})
        s.add_all([g, tile]); s.commit()
        yield s, p, dataset, g, tile
    engine.dispose()


def test_trial_and_commit_only_append_evidence_for_the_published_historical_point(context):
    s, p, dataset, g, _ = context
    kwargs = dict(dataset_id=dataset.id, snapshot_ids=[g.id])
    preview = audit_published_geo_points(s, **kwargs)
    assert preview['certifiable'] == 1 and not s.new and not s.dirty
    audit_published_geo_points(s, **kwargs, apply=True, expected_plan_sha256=preview['plan_sha256'])
    s.rollback()
    assert s.scalar(select(AuditLog)) is None
    audit_published_geo_points(s, **kwargs, apply=True, expected_plan_sha256=preview['plan_sha256'])
    s.commit()
    event = s.scalar(select(AuditLog))
    assert event.action == 'published_geo_projection_policy_audit'
    assert event.metadata_json['snapshot_id'] == g.id
    assert (p.current_geo_lat, p.current_geo_lon, p.review_status, p.rating) == (58.1, 40.2, 'approved', 78)
    assert s.scalar(select(LotGeoSnapshot)) == g


@pytest.mark.parametrize('change', ['snapshot', 'tile', 'dataset', 'later_selected_snapshot'])
def test_any_changed_evidence_rejects_the_entire_batch_before_any_event(context, change):
    s, _, dataset, g, tile = context
    kwargs = dict(dataset_id=dataset.id, snapshot_ids=[g.id])
    preview = audit_published_geo_points(s, **kwargs)
    if change == 'snapshot':
        g.metadata_json = {'query': 'г. Ярославль, ул. Победы, д. 12', 'address': 'г. Ярославль, ул. Победы, д. 12'}
    elif change == 'tile':
        tile.payload_json = {'features': [{'kind':'lot','id':g.lot_id,'lat':58.6,'lon':39.8}]}
    elif change == 'dataset':
        dataset.point_count = 100
    else:
        g.observed_at = datetime(2026, 11, 19)
    s.flush()
    with pytest.raises(ValueError, match='stale'):
        audit_published_geo_points(s, **kwargs, apply=True, expected_plan_sha256=preview['plan_sha256'])
    assert s.scalar(select(AuditLog)) is None


@pytest.mark.parametrize('change', ['precise', 'cadastral', 'foreign_coordinates', 'later_time'])
def test_valid_or_unrelated_points_cannot_receive_a_certificate(context, change):
    s, _, dataset, g, _ = context
    if change == 'precise':
        g.metadata_json = {'query':'г. Ярославль, ул. Победы, д. 12','address':'г. Ярославль, ул. Победы, д. 12'}
    elif change == 'cadastral':
        g.geo_source = 'nspd'
    elif change == 'foreign_coordinates':
        g.centroid_lat = 58.6
    else:
        g.observed_at = datetime(2026, 11, 19)
    s.flush()
    assert audit_published_geo_points(s, dataset_id=dataset.id, snapshot_ids=[g.id])['certifiable'] == 0
