"""Apply ingestion's sale/property contract to legacy map projections too."""
from collections.abc import Mapping
from typing import Any
import json

from bankrotai.domain import NormalizedLot
from bankrotai.services.real_estate_filter import is_sale_real_estate_lot


def map_property_evidence_columns() -> tuple:
    from bankrotai.db import ProcessedLot, SourceLot

    return (
        ProcessedLot.title, ProcessedLot.category, ProcessedLot.description, ProcessedLot.address,
        SourceLot.auction_type,
        SourceLot.raw_data["typeTransaction"].as_string().label("source_transaction"),
        SourceLot.raw_data["raw"]["typeTransaction"].as_string().label("official_transaction"),
        SourceLot.raw_data["source_sale_eligibility"].as_boolean().label("source_sale_eligible"),
    )


def map_source_join_condition():
    from sqlalchemy import and_
    from bankrotai.db import ProcessedLot, SourceLot

    return and_(SourceLot.source_system == ProcessedLot.source_system,
                SourceLot.external_id == ProcessedLot.external_id)


def map_property_eligible(row: Mapping[str, Any]) -> bool:
    if row.get("source_sale_eligible") is False:
        return False
    transaction = row.get("official_transaction") or row.get("source_transaction")
    if isinstance(transaction, str) and transaction.startswith("{"):
        try:
            transaction = json.loads(transaction)
        except ValueError:
            pass
    evidence = NormalizedLot(
        external_id=str(row.get("id", "")), source="map_projection", source_system="map_projection",
        title=row.get("title") or "", description=row.get("description") or "",
        category=row.get("category") or "other", address=row.get("address"),
        region_slug=None, region_name=None, cadastral_number=row.get("cadastral_number"),
        vin=None, area=None, start_price=None, current_price=None,
        auction_status="unknown", lot_url=None, source_url=None,
        detail_level="projection", auction_type=row.get("auction_type"),
        raw_data={"typeTransaction": transaction},
    )
    return is_sale_real_estate_lot(evidence)
