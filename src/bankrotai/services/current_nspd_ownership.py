"""Pin a frozen primary-source ownership proof to the current exact source row."""
from datetime import datetime, timezone
import re


def frozen_ownership_rejection(row, source, receipt):
    confirmations = receipt.get('owned_source_confirmations')
    if confirmations is None:
        return None  # Earlier receipt campaigns retain their existing contract.
    matches = [item for item in confirmations if item.get('lot_id') == row.id]
    if len(matches) != 1:
        return 'owned_source_confirmation_not_unique'
    item = matches[0]
    field = item.get('field')
    proof = item.get('proof') or {}
    if (field not in {'title', 'cadastral_number'}
            or item.get('source_system') != source.source_system
            or source.cadastral_number != row.cadastral_number
            or item.get('value') != getattr(source, field)
            or ('value' in proof and proof['value'] != item.get('value'))
            or (source.raw_data or {}).get('verified_source_fields', {}).get(field) != proof
            or not re.fullmatch(r'[0-9a-fA-F]{64}', str(proof.get('sha256', '')))):
        return 'owned_source_confirmation_changed'
    try:
        checked = datetime.fromisoformat(proof['checked_at_utc'])
        if checked.tzinfo is None or not 0 <= (datetime.now(timezone.utc)-checked).total_seconds() <= 86400:
            return 'owned_source_confirmation_expired'
    except (ValueError, KeyError, TypeError):
        return 'owned_source_confirmation_expired'
    if field == 'title':
        cadastres = set(re.findall(r'(?<!\d)\d{2}:\d{1,2}:\d{5,9}:\d{1,12}(?!\d)', source.title or ''))
        if proof.get('value') != source.title or row.cadastral_number not in cadastres:
            return 'owned_source_title_does_not_confirm_cadastre'
    return None
