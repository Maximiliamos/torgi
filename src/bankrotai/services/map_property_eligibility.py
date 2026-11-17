"""Apply ingestion's sale/property contract to legacy map projections too."""
from collections.abc import Mapping
from typing import Any

from bankrotai.domain import NormalizedLot
from bankrotai.services.real_estate_filter import is_sale_real_estate_lot


def map_property_eligible(row: Mapping[str, Any]) -> bool:
    evidence = NormalizedLot(
        external_id=str(row.get("id", "")), source="map_projection", source_system="map_projection",
        title=row.get("title") or "", description=row.get("description") or "",
        category=row.get("category") or "other", address=row.get("address"),
        region_slug=None, region_name=None, cadastral_number=row.get("cadastral_number"),
        vin=None, area=None, start_price=None, current_price=None,
        auction_status="unknown", lot_url=None, source_url=None,
        detail_level="projection", raw_data={},
    )
    return is_sale_real_estate_lot(evidence)
