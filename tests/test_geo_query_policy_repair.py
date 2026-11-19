from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, LotGeoSnapshot, LotNote, ProcessedLot, SourceLot
from bankrotai.services.geo_query_policy_repair import repair_current_geo_projections


@pytest.fixture
def context():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        point = ProcessedLot(source_system="tbankrot.ru", external_id="tbankrot:1", title="Building",
            category="commercial_building", address="обл. Ярославская", auction_status="active",
            current_geo_lat=57.6360203, current_geo_lon=39.875625, current_geo_source="photon",
            current_geo_observed_at=datetime(2026,11,18), review_status="approved", rating=78)
        session.add(point)
        session.flush()
        snap = LotGeoSnapshot(lot_id=point.id, geo_source="photon", geo_method="address",
            centroid_lat=point.current_geo_lat, centroid_lon=point.current_geo_lon,
            observed_at=point.current_geo_observed_at,
            metadata_json={"query":"область Ярославская, Ярославская область"})
        note = LotNote(lot_id=point.id, user_id="user", content="Preserve this note")
        session.add_all([snap,note])
        session.commit()
        yield session, point, snap, note
    engine.dispose()


def test_preview_trial_and_commit_preserve_history_and_user_state(context):
    session, point, snap, note = context
    preview = repair_current_geo_projections(session, lot_ids=[point.id])
    assert preview["repairable"] == 1 and point.current_geo_lat is not None
    kwargs = dict(lot_ids=[point.id],apply=True,
        expected_states={point.id:preview["items"][0]["expected_state"]},
        expected_selection_sha256=preview["selection_sha256"])
    repair_current_geo_projections(session, **kwargs)
    assert point.current_geo_lat is None and point.needs_geo_check
    session.rollback()
    assert point.current_geo_lat == 57.6360203
    repair_current_geo_projections(session, **kwargs)
    session.commit()
    assert point.current_geo_lat is None and point.current_geo_lon is None
    assert point.review_status == "approved" and point.rating == 78
    assert point.address == "обл. Ярославская" and not point.is_archived
    assert note.lot_id == point.id and note.content == "Preserve this note"
    assert list(session.scalars(select(LotGeoSnapshot))) == [snap]
    assert repair_current_geo_projections(session, lot_ids=[point.id])["repairable"] == 0


@pytest.mark.parametrize("change", ["source", "query", "time"])
def test_only_current_coarse_address_points_are_candidates(context, change):
    session, point, snap, _ = context
    if change == "source":
        snap.geo_source = "nspd"
    elif change == "query":
        snap.metadata_json = {"query":"г. Ярославль, улица Победы, дом 20А",
                              "address":"г. Ярославль, ул. Победы, д. 20А"}
    else:
        point.current_geo_observed_at = datetime(2026,11,19)
    session.commit()
    assert repair_current_geo_projections(session, lot_ids=[point.id])["repairable"] == 0


def test_changed_snapshot_invalidates_preview_before_mutation(context):
    session, point, snap, _ = context
    preview = repair_current_geo_projections(session, lot_ids=[point.id])
    snap.metadata_json = {"query":"г. Ярославль, улица Победы, дом 20А"}
    session.flush()
    with pytest.raises(ValueError, match="stale"):
        repair_current_geo_projections(session, lot_ids=[point.id], apply=True,
            expected_states={point.id:preview["items"][0]["expected_state"]},
            expected_selection_sha256=preview["selection_sha256"])
    assert point.current_geo_lat == 57.6360203


@pytest.mark.parametrize(('query', 'matched', 'reason'), [
    ('г. Ярославль, ул. Победы, д. 20А', 'г. Ярославль, ул. Победы, д. 20', 'property_number_mismatch'),
    ('г. Ярославль, ул. Победы, д. 20А', 'г. Ярославль', 'matched_property_number_missing'),
    ('г. Ярославль', 'г. Ярославль, ул. Победы, д. 20А', 'property_locator_missing'),
    ('г. Ярославль, ул. Победы, д. 20А', 'г. Ярославль, ул. Свободы, д. 20А', 'property_street_mismatch'),
    ('г. Ярославль, ул. Победы, д. 20к1', 'г. Ярославль, ул. Победы, д. 20к2', 'property_number_mismatch'),
])
def test_existing_imprecise_address_points_are_reset_with_a_verifiable_audit(context, query, matched, reason):
    from bankrotai.db import AuditLog
    from bankrotai.services.reported_lot_repair import _state

    session, point, snap, note = context
    snap.metadata_json = {'query': query, 'address': matched}
    session.commit()
    snapshot_sha = _state(snap, None)
    preview = repair_current_geo_projections(session, lot_ids=[point.id])
    assert preview['items'][0]['reason'] == reason and preview['repairable'] == 1
    repair_current_geo_projections(session, lot_ids=[point.id], apply=True,
        expected_states={point.id: preview['items'][0]['expected_state']},
        expected_selection_sha256=preview['selection_sha256'])
    session.commit()
    event = session.scalar(select(AuditLog).where(AuditLog.action == 'geo_projection_policy_repair'))
    assert event.metadata_json['snapshot_state_sha256'] == snapshot_sha == _state(snap, None)
    assert point.current_geo_lat is None and point.review_status == 'approved' and point.rating == 78
    assert note.content == 'Preserve this note' and not point.is_archived


def test_new_geo_failure_invalidates_preview_and_is_not_silently_removed(context):
    from bankrotai.db import GeoFailure

    session, point, _, _ = context
    preview = repair_current_geo_projections(session, lot_ids=[point.id])
    failure = GeoFailure(lot_id=point.id, error_message='New concurrent diagnostic')
    session.add(failure)
    session.flush()
    with pytest.raises(ValueError, match='stale'):
        repair_current_geo_projections(session, lot_ids=[point.id], apply=True,
            expected_states={point.id: preview['items'][0]['expected_state']},
            expected_selection_sha256=preview['selection_sha256'])
    assert point.current_geo_lat is not None and failure in session


@pytest.mark.parametrize("secondary_matches", [False, True])
def test_cadastral_snapshot_requires_current_own_primary_or_secondary_identity(context, secondary_matches):
    session, point, snap, _ = context
    point.cadastral_number = "76:07:101701:72"
    point.cadastral_numbers = ["76:07:101701:72", "76:07:101701:73"] if secondary_matches else ["76:07:101701:72"]
    snap.geo_source = "nspd"
    snap.metadata_json = {"query":"76:07:101701:73", "cadastral_number":"76:07:101701:73"}
    session.commit()
    preview = repair_current_geo_projections(session, lot_ids=[point.id])
    assert preview["repairable"] == (0 if secondary_matches else 1)
    if not secondary_matches:
        assert preview["items"][0]["reason"] == "snapshot_cadastre_not_in_current_source_identity"


def test_exact_own_source_secondary_cadastre_is_preserved_and_guarded(context):
    session, point, snap, _ = context
    point.cadastral_number = "76:07:101701:72"
    snap.geo_source = "nspd"
    snap.metadata_json = {"cadastral_number":"76:07:101701:73"}
    canonical = CanonicalLot(canonical_key="geo-test", title="Building")
    session.add(canonical)
    session.flush()
    source = SourceLot(canonical_lot_id=canonical.id, processed_lot_id=point.id,
        source_system=point.source_system, external_id=point.external_id,
        raw_data={"cadastral_numbers":["76:07:101701:72","76:07:101701:73"]})
    session.add(source)
    session.commit()
    preview = repair_current_geo_projections(session, lot_ids=[point.id])
    assert preview["repairable"] == 0
    source.raw_data = {"cadastral_numbers":["76:07:101701:72"]}
    session.flush()
    with pytest.raises(ValueError, match="stale"):
        repair_current_geo_projections(session, lot_ids=[point.id], apply=True,
            expected_states={point.id:preview["items"][0]["expected_state"]},
            expected_selection_sha256=preview["selection_sha256"])
    assert point.current_geo_lat == 57.6360203
