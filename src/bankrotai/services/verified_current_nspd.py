"""Replay exact primary receipts into explicitly selected empty GEO projections.

This operator path preserves the conservative automatic stale-no-match policy.
It also handles projections deliberately reset after source/policy repair.
"""
from datetime import datetime, timezone
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select

from bankrotai.db import GeoFailure, LotGeoSnapshot, ProcessedLot, SourceLot
from bankrotai.geo import apply_lot_geo_result, validate_geocoding_result
from bankrotai.logic import log_action
from bankrotai.services.geo_backfill import geo_input_hash
from bankrotai.services.map_property_eligibility import map_property_eligible
from bankrotai.services.nspd_evidence import nspd_result_from_receipt
from bankrotai.services.quality import resolve_geo_failure
from bankrotai.services.reported_lot_repair import _state


def replay_current_nspd(session, *, receipts, apply=False, expected=None):
    ids = [int(i) for receipt in receipts for i in receipt['lot_ids']]
    if not receipts or not 1 <= len(ids) <= 100 or len(set(ids)) != len(ids) or min(ids) < 1:
        raise ValueError('Expected 1..100 distinct exact projection IDs')
    proof_sha = hashlib.sha256(json.dumps(receipts, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if apply and (not expected or expected.get('evidence_sha256') != proof_sha):
        raise ValueError('Exact current-GEO preview is required')
    prior = {item['lot_id']:item['expected_state'] for item in expected['items']} if expected else {}
    decoded = {}
    by_id = {}
    for receipt in receipts:
        url = urlsplit(receipt.get('source_url', ''))
        query = parse_qs(url.query)
        if (url.scheme != 'https' or url.hostname != 'nspd.gov.ru' or url.username or url.password
                or url.port not in (None,443) or url.fragment
                or url.path != '/api/geoportal/v2/search/geoportal'
                or query.get('query') != [receipt['cadastral_number']]
                or query.get('thematicSearchId') != ['1']):
            raise ValueError('Receipt is not from the exact primary NSPD query')
        result = nspd_result_from_receipt(receipt)
        decoded[id(receipt)] = result
        for lot_id in receipt['lot_ids']:
            by_id[int(lot_id)] = receipt
    items = []
    accepted = []
    for lot_id in sorted(ids):
        row_query = select(ProcessedLot).where(ProcessedLot.id == lot_id)
        row = session.scalar(row_query.with_for_update() if apply else row_query)
        source = None
        if row:
            source_query = select(SourceLot).where(SourceLot.processed_lot_id == row.id,
                SourceLot.source_system == row.source_system, SourceLot.external_id == row.external_id)
            source = session.scalar(source_query.with_for_update() if apply else source_query)
        failure_query = select(GeoFailure).where(GeoFailure.lot_id == lot_id)
        failure = session.scalar(failure_query.with_for_update() if apply else failure_query)
        state = hashlib.sha256(json.dumps([_state(row,source),_state(failure,None)]).encode()).hexdigest()
        if apply and prior.get(lot_id) != state:
            raise ValueError(f'Current-GEO preview is stale: {lot_id}')
        receipt = by_id[lot_id]
        result = decoded[id(receipt)]
        reason = None
        if row is None or source is None:
            reason = 'missing_exact_source_binding'
        elif (row.is_archived or row.duplicate_of_id is not None
              or row.auction_status not in {'active','scheduled'}):
            reason = 'inactive_projection'
        elif row.current_geo_lat is not None or row.current_geo_lon is not None:
            reason = 'current_point_already_exists'
        elif row.cadastral_number != receipt['cadastral_number']:
            reason = 'current_primary_cadastre_differs'
        else:
            raw = source.raw_data or {}
            original_value = raw.get('raw')
            original = original_value if isinstance(original_value,dict) else {}
            if not map_property_eligible({'id':row.id,'title':row.title,'description':row.description,
                'category':row.category,'address':row.address,'cadastral_number':row.cadastral_number,
                'auction_type':source.auction_type,'source_transaction':raw.get('typeTransaction'),
                'official_transaction':original.get('typeTransaction'),
                'source_sale_eligible':raw.get('source_sale_eligibility')}):
                reason = 'not_property_sale'
            else:
                valid, why = validate_geocoding_result(result, cadastral_number=row.cadastral_number,
                    address=row.address,region_name=row.region_name,region_code=row.region_code)
                if not valid:
                    reason = 'validation:'+why
        items.append({'lot_id':lot_id,'expected_state':state,'accepted':reason is None,'reason':reason})
        if reason is None:
            accepted.append((row,receipt,result))
    # All selected identities, inputs and user states are validated first.
    if apply:
        for row, receipt, result in accepted:
            if not apply_lot_geo_result(session,row,result):
                raise ValueError('Normal GEO persistence rejected the validated result')
            snapshot = next(s for s in session.new if isinstance(s,LotGeoSnapshot) and s.lot_id == row.id)
            snapshot.source_checked_at = datetime.fromisoformat(receipt['checked_at_utc']).astimezone(timezone.utc).replace(tzinfo=None)
            snapshot.metadata_json = {**(snapshot.metadata_json or {}),
                'verified_response_sha256':receipt['response_sha256'],
                'primary_source_url':receipt['source_url'],'primary_checked_at_utc':receipt['checked_at_utc']}
            row.geo_input_hash = geo_input_hash(row)
            resolve_geo_failure(session,row.id)
            log_action(session,'operator','verified_current_nspd_replay','lot',str(row.id),
                {'response_sha256':receipt['response_sha256'],'source_checked_at':receipt['checked_at_utc'],
                 'review_status_preserved':row.review_status,'source_lot_changes':0})
        session.flush()
    return {'selected':len(ids),'accepted':len(accepted),'rejected':len(ids)-len(accepted),
            'updated':len(accepted) if apply else 0,'items':items,'evidence_sha256':proof_sha,
            'network_requests':0,'deleted_lots':0,'historical_snapshots_deleted':0,'source_lot_changes':0}
