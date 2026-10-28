"""Authenticated read-only catalogue diagnostics for staged ETP sources.

This intentionally returns unverified candidates, never normalized lots,
database writes or archive-safe full-inventory claims.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from bankrotai.connectors.registry.direct_etp_pilots import (
    DirectEtpPilotConnector, PILOT_SOURCE_IDS,
)
from bankrotai.connectors.registry.direct_etp_catalogue import PROFILE_BY_ID


def create_etp_pilot_catalogue_router(require_admin):
    router = APIRouter(prefix="/api/etp/pilots", tags=["ETP canary"])

    @router.get("/sources")
    async def sources(_actor=Depends(require_admin)):
        return {
            "items": [
                {"id": source_id, "name": PROFILE_BY_ID[source_id].name,
                 "strategy": PROFILE_BY_ID[source_id].strategy,
                 "source_complete": False, "production_ingestion_enabled": False}
                for source_id in PILOT_SOURCE_IDS
            ],
            "lot_online_is_existing_source": True,
            "all_new_etps_read_only": True,
        }

    @router.get("/{source_id}/preview")
    async def preview(
        source_id: str, limit: int = Query(5, ge=1, le=10),
        _actor=Depends(require_admin),
    ):
        if source_id not in PILOT_SOURCE_IDS:
            raise HTTPException(status_code=404, detail="Unknown ETP pilot")
        try:
            return await DirectEtpPilotConnector(source_id).preview(limit=limit)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"ETP {source_id} public catalogue unavailable or parser changed",
            ) from exc

    return router
