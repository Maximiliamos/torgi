"""Administrator-only inspection of bounded M-ETS/CDTRF incremental windows.

This is a safe pre-ingestion checkpoint; no DB, archive, journal or map writes.
"""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query

from bankrotai.connectors.registry import connector_registry
from bankrotai.connectors.registry.etp_incremental import (
    CANARY_SOURCES, EtpIncrementalFilters,
)


def create_etp_incremental_preview_router(require_admin):
    router = APIRouter(prefix="/api/etp/pilots", tags=["ETP bounded previews"])

    @router.get("/{source_id}/batch-preview")
    async def batch_preview(
        source_id: str,
        limit: int = Query(2, ge=1, le=5),
        cursor: str | None = Query(None, max_length=30),
        _actor=Depends(require_admin),
    ):
        if source_id not in CANARY_SOURCES:
            raise HTTPException(status_code=404, detail="No bounded source contract")
        try:
            page = await connector_registry.create(source_id).search(
                EtpIncrementalFilters(source_id=source_id, max_detail_requests=limit),
                cursor,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"{source_id}: public lot verification failed"
            ) from exc
        return {
            "source": source_id,
            "items": [
                {"external_id": lot.external_id, "title": lot.title,
                 "category": lot.category, "cadastral_number": lot.cadastral_number,
                 "initial_price": lot.start_price, "current_price": lot.current_price,
                 "status": lot.auction_status, "url": lot.lot_url}
                for lot in page.items
            ],
            "next_cursor": page.next_cursor,
            "catalogue_lots": page.metadata.get("catalogue_lots"),
            "details_rejected": page.metadata.get("details_rejected"),
            "detail_attempts": page.metadata.get("detail_attempts"),
            "rejection_codes": page.metadata.get("rejection_codes", {}),
            "database_writes": 0, "complete_source_inventory": False,
            "archival_allowed": False, "map_publication": False,
        }
    return router
