from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import AuditLog, Base, CanonicalLot, LotGeoSnapshot, MapDataset, ProcessedLot, SourceLot
from bankrotai.services.map_audited_transition import audited_transition_ids
from bankrotai.services.reported_lot_repair import _state


def certificate(invalid=None, terminal=False):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        previous = MapDataset(version="old-r11", published_at=datetime(2026, 11, 18, 10))
        candidate = MapDataset(version="new-r12", created_at=datetime(2026, 11, 19, 10))
        p = ProcessedLot(source_system="example", external_id="one", title="Land", category="land",
                         auction_status="closed" if terminal else "active", is_archived=terminal)
        s.add_all([previous, candidate, p]); s.flush()
        g = LotGeoSnapshot(lot_id=p.id, observed_at=datetime(2026, 11, 17), geo_source="photon", geo_method="address",
                           centroid_lat=57.6, centroid_lon=39.8,
                           metadata_json={"query": "Ярославская область, г. Ярославль, улица Победы, дом 12",
                                          "address": "Ярославская область, г. Ярославль, улица Победы"})
        s.add(g); s.flush()
        data = {"snapshot_id": g.id, "snapshot_state_sha256": _state(g, None),
                "reason": "matched_property_number_missing", "source_fact_changes": 0,
                "historical_snapshots_deleted": 0}
        event = AuditLog(user_id="operator", entity_type="lot", entity_id=str(p.id),
                         action="geo_projection_policy_repair", created_at=datetime(2026, 11, 19, 9),
                         metadata_json=data)
        if terminal:
            c = CanonicalLot(canonical_key="example:one", title="Land", category="land")
            s.add(c); s.flush()
            proof = {"checked_at_utc": "2026-11-19T08:00:00+00:00", "sha256": "a"*64, "value": "completed"}
            source = SourceLot(canonical_lot_id=c.id, processed_lot_id=p.id, source_system="example",
                               external_id="one", source_status="completed", is_active=False, is_archived=True,
                               raw_data={"verified_source_fields": {"auction_status": proof}})
            s.add(source)
            event.action = "exact_verified_source_source_repair"
            event.metadata_json = {"source_system": "example", "external_id": "one",
                                   "changes": {"auction_status": {"before": "active", "after": "completed"}}}
            if invalid == "expired":
                proof["checked_at_utc"] = "2026-11-17T08:00:00+00:00"
            elif invalid == "proof_value":
                proof["value"] = "active"
            elif invalid == "bad_sha":
                proof["sha256"] = "unverified"
            elif invalid == "source_active":
                source.is_active = True
            elif invalid == "binding":
                source.external_id = "another-sale"
        points = {p.id: {"lat": 57.6, "lon": 39.8}}
        new_ids = set()
        if invalid == "snapshot_sha":
            g.metadata_json = {**g.metadata_json, "query": "different"}
        elif invalid == "old_geometry":
            points[p.id]["lat"] = 58.6
        elif invalid == "missing_geometry":
            points[p.id] = {}
        elif invalid == "audit_time":
            event.created_at = datetime(2026, 11, 18, 9)
        elif invalid == "future_audit":
            event.created_at = datetime(2026, 11, 19, 11)
        elif invalid == "actor":
            event.user_id = "someone"
        elif invalid == "revision":
            previous.version = "old-r12"
        elif invalid == "repeat_revision":
            candidate.version = "new-r13"
        elif invalid == "retained":
            new_ids.add(p.id)
        elif invalid == "reason":
            event.metadata_json = {**data, "reason": "wrong_reason"}
        s.add(event); s.commit()
        result = audited_transition_ids(s, previous=previous, candidate=candidate,
                                        old_points=points, new_ids=new_ids)
    engine.dispose()
    return result


def test_only_exact_published_geometry_and_unchanged_snapshot_certify_removal():
    assert certificate() == {1}


@pytest.mark.parametrize("invalid", ["snapshot_sha", "old_geometry", "missing_geometry", "audit_time",
                                   "future_audit", "actor", "revision", "repeat_revision", "retained", "reason"])
def test_uncertified_or_repeated_geo_loss_still_counts_against_coverage(invalid):
    assert certificate(invalid) == set()


def test_owned_literal_terminal_status_with_post_publication_audit_certifies_removal():
    assert certificate(terminal=True) == {1}


@pytest.mark.parametrize("invalid", ["expired", "proof_value", "bad_sha", "source_active", "binding", "audit_time"])
def test_unverified_terminal_status_cannot_discount_baseline(invalid):
    assert certificate(invalid, terminal=True) == set()


@pytest.mark.parametrize("surviving,tamper,expected", [(10, False, "published"), (3, False, "rejected"), (10, True, "rejected")])
def test_same_global_and_dimensional_ratios_still_protect_uncertified_population(monkeypatch, surviving, tamper, expected):
    from types import SimpleNamespace
    from sqlalchemy.orm import sessionmaker
    from bankrotai.db import MapTile
    from bankrotai.services import map_builder
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(map_builder, "get_settings", lambda: SimpleNamespace(min_map_points=0, min_map_coverage_ratio=0.5))
    with factory() as s:
        old = MapDataset(version="old-r11", status="ready", is_current=True, point_count=40,
                         published_at=datetime(2026, 11, 18, 10))
        new = MapDataset(version="new-r12", status="building", point_count=surviving, tile_count=1,
                         created_at=datetime(2026, 11, 19, 10))
        s.add_all([old, new]); s.flush()
        lots = [ProcessedLot(source_system="affected", external_id=f"one:{i}", title="Land", category="land",
                             region_code="76", auction_status="active") for i in range(40)]
        s.add_all(lots); s.flush()
        for lot in lots[:30]:
            snapshot = LotGeoSnapshot(lot_id=lot.id, observed_at=datetime(2026, 11, 17), geo_source="photon", geo_method="address",
                                      centroid_lat=57.6, centroid_lon=39.8,
                                      metadata_json={"query": "Ярославская область"})
            s.add(snapshot); s.flush()
            s.add(AuditLog(user_id="operator", entity_type="lot", entity_id=str(lot.id),
                           action="geo_projection_policy_repair", created_at=datetime(2026, 11, 19, 9),
                           metadata_json={"reason": "region_only_address", "snapshot_id": snapshot.id,
                                          "snapshot_state_sha256": "wrong" if tamper else _state(snapshot, None),
                                          "source_fact_changes": 0, "historical_snapshots_deleted": 0}))
        def features(rows):
            return {"features": [{"kind": "lot", "id": p.id, "lat": 57.6, "lon": 39.8} for p in rows]}
        s.add_all([MapTile(dataset_id=old.id, z=12, x=1, y=1, etag="old", feature_count=40, payload_json=features(lots)),
                   MapTile(dataset_id=new.id, z=12, x=1, y=1, etag="new", feature_count=surviving, payload_json=features(lots[30:30+surviving]))])
        s.commit(); old_id, new_id = old.id, new.id
        failures = map_builder._dimension_coverage_failures(s, current_dataset_id=old_id, new_dataset_id=new_id, minimum_ratio=0.5)
        assert bool(failures) == (expected == "rejected")
    result = map_builder._promote_map_dataset(factory, dataset_id=new_id, expected_current_id=old_id, dimension_failures=failures)
    assert result["status"] == expected
    if expected == "published":
        assert result["certified_obsolete_previous_members"] == 30
    with factory() as s:
        assert s.get(MapDataset, old_id).is_current == (expected != "published")
    engine.dispose()
