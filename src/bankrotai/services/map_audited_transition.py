"""One revision transition for independently certified obsolete map members."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from bankrotai.db import AuditLog, LotGeoSnapshot, ProcessedLot, SourceLot
from bankrotai.services.geo_address_candidates import is_region_only_address
from bankrotai.services.geo_feature_precision import address_precision_rejection
from bankrotai.services.map_dataset_version import dataset_pipeline_revision
from bankrotai.services.individual_lot_identity import unscoped_procedure_identity
from bankrotai.services.map_confirmed_closures import confirmed_source_closure_ids


def audited_transition_ids(session, *, previous, candidate, old_points, new_ids):
    """Discount proven closures and one bounded correction of obsolete geometry/IDs.

    Missing geometry, a stale status, another revision, a changed snapshot or
    a generic archive flag never certifies a geometry/status removal. Known
    legacy procedure IDs follow an explicit individual-identity scope change. Unexplained losses
    remain subject to the original global, source and regional coverage ratios.
    """
    if (previous is None or candidate is None or previous.published_at is None
            or candidate.created_at is None):
        return set()
    quality_transition = (dataset_pipeline_revision(previous.version) == "r11"
                          and dataset_pipeline_revision(candidate.version) == "r12")
    from bankrotai.services.reported_lot_repair import _state
    from bankrotai.logic import normalize_status

    missing = set(old_points) - set(new_ids)
    events = list(session.scalars(select(AuditLog).where(
        AuditLog.user_id == "operator", AuditLog.entity_type == "lot",
        AuditLog.action.in_(("geo_projection_policy_repair", "exact_verified_source_source_repair")),
        AuditLog.created_at > previous.published_at,
        AuditLog.created_at <= candidate.created_at,
    )))
    relevant = []
    for event in events:
        text_id = str(event.entity_id or "")
        if text_id.isascii() and text_id.isdigit() and int(text_id) in missing:
            if quality_transition or event.action != "geo_projection_policy_repair":
                relevant.append((int(text_id), event))
    raw_snapshot_ids = {(event.metadata_json or {}).get("snapshot_id") for _, event in relevant
                    if event.action == "geo_projection_policy_repair"}
    snapshot_ids = {i for i in raw_snapshot_ids if isinstance(i, int) and not isinstance(i, bool)}
    snapshots = {}
    ids = sorted(snapshot_ids)
    for start in range(0, len(ids), 1000):
        snapshots.update({g.id: g for g in session.scalars(select(LotGeoSnapshot).where(
            LotGeoSnapshot.id.in_(ids[start:start+1000])))})
    accepted = confirmed_source_closure_ids(session, lot_ids=missing,
        previous_published_at=previous.published_at, candidate_created_at=candidate.created_at)
    terminal_audits = {}
    for lot_id, event in relevant:
        data = event.metadata_json or {}
        if event.action == "exact_verified_source_source_repair":
            changes = data.get("changes") or {}
            status = changes.get("auction_status") or {}
            if (normalize_status(str(status.get("after") or "")) == "closed"
                    and status.get("before") != status.get("after")):
                terminal_audits[lot_id] = data
            continue
        snapshot_id = data.get("snapshot_id")
        if not isinstance(snapshot_id, int) or isinstance(snapshot_id, bool):
            continue
        snapshot = snapshots.get(snapshot_id)
        if (data.get("source_fact_changes") != 0 or data.get("historical_snapshots_deleted") != 0
                or snapshot is None or snapshot.lot_id != lot_id
                or snapshot.geo_source not in {"photon", "nominatim"}
                or snapshot.observed_at > event.created_at
                or data.get("snapshot_state_sha256") != _state(snapshot, None)):
            continue
        point = old_points[lot_id]
        if (snapshot.centroid_lat != point.get("lat") or snapshot.centroid_lon != point.get("lon")):
            continue
        metadata = snapshot.metadata_json or {}
        if metadata.get("cadastral_number"):
            continue
        reason = ("region_only_address" if is_region_only_address(metadata.get("query"))
                  else address_precision_rejection(metadata.get("query"), metadata.get("address")))
        if reason and data.get("reason") == reason:
            accepted.add(lot_id)
    terminal_ids = sorted(terminal_audits)
    candidate_time = candidate.created_at.replace(tzinfo=timezone.utc)
    for start in range(0, len(terminal_ids), 1000):
        for record, source in session.execute(select(ProcessedLot, SourceLot).join(
            SourceLot, (SourceLot.processed_lot_id == ProcessedLot.id)
            & (SourceLot.source_system == ProcessedLot.source_system)
            & (SourceLot.external_id == ProcessedLot.external_id),
        ).where(ProcessedLot.id.in_(terminal_ids[start:start+1000]))):
            proof = (source.raw_data or {}).get("verified_source_fields", {}).get("auction_status") or {}
            audit = terminal_audits[record.id]
            try:
                checked = datetime.fromisoformat(proof["checked_at_utc"])
            except (KeyError, TypeError, ValueError):
                continue
            sha = proof.get("sha256")
            if (checked.tzinfo is None or not timedelta(0) <= candidate_time-checked <= timedelta(hours=24)
                    or not isinstance(sha, str) or len(sha) != 64
                    or any(c not in "0123456789abcdef" for c in sha.lower())
                    or proof.get("value") != source.source_status
                    or normalize_status(source.source_status or "") != "closed"
                    or normalize_status(record.auction_status) != "closed"
                    or not record.is_archived or source.is_active or not source.is_archived
                    or audit.get("source_system") != source.source_system
                    or audit.get("external_id") != source.external_id
                    or audit["changes"]["auction_status"].get("after") != source.source_status):
                continue
            accepted.add(record.id)
    # These provider contracts require trade + lot. A procedure-only legacy
    # row remains in the database with its history; it cannot certify a public
    # individual property. This structural membership change applies once.
    remaining_ids = sorted(missing-accepted) if quality_transition else []
    for start in range(0, len(remaining_ids), 1000):
        for lot_id, source_system, external_id in session.execute(select(
            ProcessedLot.id, ProcessedLot.source_system, ProcessedLot.external_id,
        ).where(ProcessedLot.id.in_(remaining_ids[start:start+1000]))):
            if unscoped_procedure_identity(source_system, external_id):
                accepted.add(lot_id)
    return accepted
