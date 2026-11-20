"""Audit obsolete published address points without changing any projection."""
import hashlib
import json

from sqlalchemy import select

from bankrotai.db import LotGeoSnapshot, MapDataset
from bankrotai.logic import log_action
from bankrotai.services.geo_address_candidates import is_region_only_address
from bankrotai.services.geo_feature_precision import address_precision_rejection
from bankrotai.services.map_dataset_version import dataset_pipeline_revision
from bankrotai.services.reported_lot_repair import _state


def audit_published_geo_points(session, *, dataset_id, snapshot_ids, apply=False,
                              expected_plan_sha256=None):
    """Preview all evidence, then append bounded audit events after an exact recheck.

    A historical snapshot is evidence only for its own published coordinates.
    Valid points, cadastral results and snapshots observed after publication are
    never certified by this operation. Source facts and current GEO stay intact.
    """
    from bankrotai.services.map_builder import _dataset_lot_points

    ids = list(snapshot_ids)
    if (not ids or len(ids) > 1000 or len(set(ids)) != len(ids)
            or any(not isinstance(i, int) or isinstance(i, bool) or i < 1 for i in ids)):
        raise ValueError("Expected 1..1000 distinct snapshot IDs")
    query = select(MapDataset).where(MapDataset.id == dataset_id)
    dataset = session.scalar(query.with_for_update() if apply else query)
    if (dataset is None or not dataset.is_current or dataset.published_at is None
            or dataset_pipeline_revision(dataset.version) != "r11"):
        raise ValueError("Expected the current published r11 dataset")
    points = _dataset_lot_points(session, dataset.id)
    query = select(LotGeoSnapshot).where(LotGeoSnapshot.id.in_(ids)).order_by(LotGeoSnapshot.id)
    snapshots = list(session.scalars(query.with_for_update() if apply else query))
    if len(snapshots) != len(ids) or len({g.lot_id for g in snapshots}) != len(ids):
        raise ValueError("Expected one existing historical snapshot per lot")
    items = []
    for snapshot in snapshots:
        point = points.get(snapshot.lot_id) or {}
        metadata = snapshot.metadata_json or {}
        reason = None
        if (snapshot.geo_source in {"photon", "nominatim"}
                and snapshot.observed_at <= dataset.published_at
                and snapshot.centroid_lat is not None and snapshot.centroid_lon is not None
                and snapshot.centroid_lat == point.get("lat")
                and snapshot.centroid_lon == point.get("lon")
                and not metadata.get("cadastral_number")):
            reason = ("region_only_address" if is_region_only_address(metadata.get("query"))
                      else address_precision_rejection(metadata.get("query"), metadata.get("address")))
        items.append({"lot_id": snapshot.lot_id, "snapshot_id": snapshot.id,
                      "snapshot_state_sha256": _state(snapshot, None),
                      "reason": reason, "certifiable": bool(reason),
                      "dataset_id": dataset.id, "dataset_version": dataset.version,
                      "source_fact_changes": 0, "historical_snapshots_deleted": 0})
    plan_sha = hashlib.sha256(json.dumps([_state(dataset, None), items], sort_keys=True).encode()).hexdigest()
    if apply and expected_plan_sha256 != plan_sha:
        raise ValueError("Published geometry audit preview is stale")
    # No mutation takes place until every snapshot and the dataset match preview.
    if apply:
        for item in items:
            if item["certifiable"]:
                log_action(session, "operator", "published_geo_projection_policy_audit",
                           "lot", str(item["lot_id"]), item)
        session.flush()
    return {"selected": len(items), "certifiable": sum(i["certifiable"] for i in items),
            "plan_sha256": plan_sha, "items": items, "projection_changes": 0,
            "source_fact_changes": 0, "deleted_lots": 0, "historical_snapshots_deleted": 0}
