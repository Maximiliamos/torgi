"""Revalidate stored address points without changing source facts or history."""
import hashlib
import json

from sqlalchemy import select

from bankrotai.db import GeoFailure, LotGeoSnapshot, ProcessedLot, SourceLot
from bankrotai.logic import log_action
from bankrotai.services.geo_address_candidates import is_region_only_address
from bankrotai.services.reported_lot_repair import _state


def repair_current_geo_projections(session, *, lot_ids, apply=False, expected_states=None,
                           expected_selection_sha256=None):
    ids = [int(value) for value in lot_ids]
    if not ids or len(ids) > 1000 or len(set(ids)) != len(ids) or min(ids) < 1:
        raise ValueError("Expected 1..1000 distinct projection IDs")
    selection_sha = hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()
    if apply and (expected_states is None or expected_selection_sha256 != selection_sha):
        raise ValueError("Exact preview is required")
    query = select(ProcessedLot).where(ProcessedLot.id.in_(ids)).order_by(ProcessedLot.id)
    rows = list(session.scalars(query.with_for_update() if apply else query))
    if len(rows) != len(ids):
        raise ValueError("A selected projection disappeared")
    items = []
    for row in rows:
        snapshot_query = select(LotGeoSnapshot).where(
            LotGeoSnapshot.lot_id == row.id,
            LotGeoSnapshot.centroid_lat == row.current_geo_lat,
            LotGeoSnapshot.centroid_lon == row.current_geo_lon,
        ).order_by(LotGeoSnapshot.observed_at.desc(), LotGeoSnapshot.id.desc()).limit(1)
        if row.current_geo_observed_at is not None:
            snapshot_query = snapshot_query.where(LotGeoSnapshot.observed_at == row.current_geo_observed_at)
        snapshot = session.scalar(snapshot_query.with_for_update() if apply else snapshot_query)
        source_query = select(SourceLot).where(SourceLot.processed_lot_id == row.id,
            SourceLot.source_system == row.source_system, SourceLot.external_id == row.external_id)
        source = session.scalar(source_query.with_for_update() if apply else source_query)
        metadata = (snapshot.metadata_json or {}) if snapshot else {}
        state = hashlib.sha256(json.dumps([_state(row, snapshot),
            _state(source, None) if source else None], sort_keys=True).encode()).hexdigest()
        if apply and expected_states.get(row.id) != state:
            raise ValueError(f"GEO policy preview is stale: {row.id}")
        cadastres = set(row.cadastral_numbers or [])
        cadastres.update(value for value in (row.cadastral_number, source.cadastral_number if source else None) if value)
        cadastres.update((source.raw_data or {}).get("cadastral_numbers") or [] if source else [])
        observed_cad = metadata.get("cadastral_number")
        reason = None
        if observed_cad and cadastres and observed_cad not in cadastres:
            reason = "snapshot_cadastre_not_in_current_source_identity"
        elif (snapshot and snapshot.geo_source in {"photon", "nominatim"}
                and not observed_cad and is_region_only_address(metadata.get("query"))):
            reason = "region_only_address"
        reset = bool(row.current_geo_lat is not None and row.current_geo_lon is not None and snapshot and reason)
        items.append({"lot_id": row.id, "snapshot_id": snapshot.id if snapshot else None,
                      "reset": reset, "reason": reason, "expected_state": state})
    # Every row and historical snapshot must still match before any write.
    if apply:
        for row, item in zip(rows, items):
            if not item["reset"]:
                continue
            row.current_geo_lat = row.current_geo_lon = None
            row.current_geo_source = row.current_geo_confidence = None
            row.current_geo_observed_at = row.geo_input_hash = None
            row.needs_geo_check = not row.is_archived and row.auction_status in {"active", "scheduled"}
            failure = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == row.id))
            if failure:
                session.delete(failure)
            log_action(session, "operator", "geo_projection_policy_repair", "lot", str(row.id),
                       {"snapshot_id": item["snapshot_id"], "reason": item["reason"],
                        "source_fact_changes": 0, "historical_snapshots_deleted": 0})
        session.flush()
    changed = sum(item["reset"] for item in items)
    return {"selected": len(ids), "repairable": changed, "unchanged": len(ids)-changed,
            "selection_sha256": selection_sha, "items": items, "deleted_lots": 0,
            "inserted_lots": 0, "source_fact_changes": 0, "canonical_changes": 0,
            "historical_snapshots_deleted": 0, "reconcile_missing": False}
