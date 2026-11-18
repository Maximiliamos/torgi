"""One revision transition for exact, audited removal of unproven region points."""
from sqlalchemy import select

from bankrotai.db import AuditLog, LotGeoSnapshot
from bankrotai.services.geo_address_candidates import is_region_only_address
from bankrotai.services.map_dataset_version import dataset_pipeline_revision


def region_point_quality_transition_ids(session, *, previous, candidate, old_ids):
    # Do not turn a data-quality correction into a permanent source exemption.
    if (previous is None or candidate is None or previous.published_at is None
            or candidate.created_at is None
            or dataset_pipeline_revision(previous.version) != 'r10'
            or dataset_pipeline_revision(candidate.version) != 'r11'):
        return set()
    events = session.scalars(select(AuditLog).where(
        AuditLog.action == 'geo_projection_policy_repair',AuditLog.user_id == 'operator',
        AuditLog.entity_type == 'lot',AuditLog.created_at > previous.published_at,
        AuditLog.created_at <= candidate.created_at))
    accepted = set()
    for event in events:
        data = event.metadata_json or {}
        if (data.get('reason') != 'region_only_address' or data.get('source_fact_changes') != 0
                or data.get('historical_snapshots_deleted') != 0
                or not str(event.entity_id or '').isascii()
                or not str(event.entity_id or '').isdigit()):
            continue
        lot_id = int(event.entity_id)
        if lot_id not in old_ids:
            continue
        snapshot_id = data.get('snapshot_id')
        if not isinstance(snapshot_id,int) or isinstance(snapshot_id,bool):
            continue
        snapshot = session.get(LotGeoSnapshot,snapshot_id)
        if (snapshot is None or snapshot.lot_id != lot_id
                or snapshot.geo_source not in {'photon','nominatim'}
                or snapshot.observed_at > event.created_at):
            continue
        metadata = snapshot.metadata_json or {}
        if not metadata.get('cadastral_number') and is_region_only_address(metadata.get('query')):
            accepted.add(lot_id)
    return accepted
