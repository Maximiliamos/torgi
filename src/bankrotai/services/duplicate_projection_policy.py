"""A shared property alone does not prove two publications are one sale."""
from bankrotai.logic import _same_processed_cross_source_lot


def valid_duplicate_target(lot, parent) -> bool:
    return bool(parent is not None and parent.id != lot.id and parent.duplicate_of_id is None
                and _same_processed_cross_source_lot(lot, parent))
