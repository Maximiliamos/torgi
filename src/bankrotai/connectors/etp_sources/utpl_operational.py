from __future__ import annotations

from typing import Any

from bankrotai.connectors.etp_sources.utpl_contract import (
    normalize_utpl_status,
    parse_utpl_catalogue_page,
    parse_utpl_detail,
)
from bankrotai.domain import NormalizedLot


def parse_listing(
    profile: Any,
    html: str | bytes,
    *,
    page: int,
) -> list[NormalizedLot]:
    rows = parse_utpl_catalogue_page(html, page=page)
    return [
        NormalizedLot(
            external_id=row["external_id"],
            source="utpl",
            source_system=profile.source_id,
            title=row["title"][:500],
            description=row["title"][:5000],
            category="other",
            region_slug=None,
            region_name=None,
            address=None,
            cadastral_number=None,
            vin=None,
            area=None,
            start_price=row["initial_price"],
            current_price=row["current_price"],
            auction_status=normalize_utpl_status(row["source_status_text"]),
            lot_url=row["detail_url"],
            source_url=row["detail_url"],
            detail_level="list",
            raw_data={
                "listing_page": page,
                "procedure_number": row["procedure_number"],
                "lot_number": row["lot_number"],
                "individual_lot_identity_verified": True,
                "property_classification_verified": False,
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
            },
            platform_name=profile.platform_name,
            platform_code=profile.platform_code,
            procedure_number=row["procedure_number"],
        )
        for row in rows
    ]


def parse_detail(
    profile: Any,
    html: str | bytes,
    *,
    detail_url: str,
    external_id: str | None,
) -> NormalizedLot:
    fields = parse_utpl_detail(
        detail_url,
        html,
        external_id=external_id,
    )
    return NormalizedLot(
        external_id=fields["external_id"],
        source="utpl",
        source_system=profile.source_id,
        title=fields["title"][:500],
        description=fields["title"][:5000],
        category=fields["category"],
        region_slug=None,
        region_name=None,
        address=fields["address"],
        cadastral_number=fields["cadastral_number"],
        vin=fields["vin"],
        area=fields["area"],
        start_price=fields["initial_price"],
        current_price=fields["current_price"],
        auction_status=fields["status"],
        lot_url=detail_url,
        source_url=detail_url,
        detail_level="detail",
        raw_data={
            "utpl_classifier": fields["classifier"],
            "utpl_property_kinds": list(fields["property_kinds"]),
            "structured_classifier_verified": fields["structured_classifier_verified"],
            "property_classification_verified": fields["property_classification_verified"],
            "complete_source_inventory": False,
            "safe_for_archival": False,
            "reconcile_missing": False,
        },
        platform_name=profile.platform_name,
        platform_code=profile.platform_code,
        procedure_number=fields["procedure_number"],
        application_start_at=fields["application_start"],
        application_deadline=fields["application_deadline"],
        auction_at=fields["auction_at"],
    )
