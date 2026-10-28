"""Admin-only M-ETS canary endpoints. Nothing is enabled for auto-sync.

GET preview collects at most one public detail without database writes.
POST exact-lot import requires a separate operator opt-in environment toggle,
validates cadastral/property evidence and uses the normal TORGI persistence,
duplicate reconciliation and audit log. No missing-lot archival is possible.
"""
from __future__ import annotations

import os
import re

from fastapi import APIRouter, Depends, HTTPException, Path

PROPERTY_CATEGORIES = frozenset({
    "apartment", "house", "land", "parking", "commercial_room",
    "commercial_building", "commercial_building_with_land",
    "unfinished", "complex", "real_estate",
})
LOT_ID = re.compile(r"^[1-9]\d{3,8}-[1-9]\d{0,4}$")


def validate_mets_canary_lot(lot) -> None:
    """Strict positive detail evidence before a single-lot manual import."""
    if (lot.source_system != "m-ets.ru"
            or lot.category not in PROPERTY_CATEGORIES
            or not lot.cadastral_number
            or lot.start_price is None and lot.current_price is None
            or lot.auction_status not in {"active", "scheduled"}
            or not lot.lot_url or lot.lot_url != f"https://m-ets.ru/{lot.external_id}"):
        raise ValueError("M-ETS lot fails real-estate, price, identity or status evidence gate")


def create_mets_canary_router(require_admin):
    router = APIRouter(prefix="/api/etp/pilots", tags=["ETP canary"])

    @router.get("/m-ets.ru/preview")
    async def preview_mets(_actor=Depends(require_admin)):
        from bankrotai.connectors.registry.mets_public import MetsConnector

        try:
            result = await MetsConnector().preview(max_lots=1)
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail="M-ETS public catalogue or detail unavailable"
            ) from exc
        result["production_import_enabled"] = os.environ.get("ETP_METS_CANARY_IMPORT_ENABLED") == "1"
        result["non_exhaustive"] = True
        return result

    @router.post("/m-ets.ru/import/{external_id}")
    async def import_exact_mets_lot(
        external_id: str = Path(pattern=r"^[1-9]\d{3,8}-[1-9]\d{0,4}$"),
        actor=Depends(require_admin),
    ):
        if os.environ.get("ETP_METS_CANARY_IMPORT_ENABLED") != "1":
            raise HTTPException(status_code=403, detail="ETP canary import is disabled by default")
        if not LOT_ID.fullmatch(external_id):
            raise HTTPException(status_code=422, detail="Invalid M-ETS lot identifier")
        from bankrotai.connectors.registry.mets_public import MetsConnector
        from bankrotai.db import session_scope
        from bankrotai.services.etp_canary_persistence import (persist_canary_lot, CanaryDuplicateReviewRequired)

        try:
            normalized = await MetsConnector().fetch_lot(external_id)
            validate_mets_canary_lot(normalized)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="M-ETS detail could not be verified") from exc
        with session_scope() as session:
            try:
                record = persist_canary_lot(session, normalized, str(actor.id))
            except CanaryDuplicateReviewRequired as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return {
                "source_system": "m-ets.ru",
                "external_id": normalized.external_id,
                "id": record.id,
                "status": "persisted",
                "requires_geocoding_and_map_publication": True,
                "full_catalogue_verified": False,
                "automatic_sync_enabled": False,
                "missing_lot_archival_enabled": False,
            }

    return router
