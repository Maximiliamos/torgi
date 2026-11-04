"""Administrator-only, bounded ETP parser stage diagnostics API.

Live read-only probes are rate-limited and never certify production ingestion.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from bankrotai.services.etp_parser_checklist import diagnose_source, source_ids

_LOCK = asyncio.Lock()
_CACHE: dict[tuple[str, int, int], tuple[float, dict]] = {}
_CACHE_SECONDS = 300


def create_etp_parser_checklist_router(require_admin):
    router = APIRouter(prefix="/api/etp/checklist", tags=["ETP diagnostics"])

    @router.get("/sources")
    async def sources(_actor=Depends(require_admin)):
        return {
            "items": [{"source_id": sid} for sid in source_ids()],
            "qa_only": True, "production_approved": False,
        }

    @router.get("/{source_id}")
    async def inspect(
        source_id: str,
        pages: int = Query(2, ge=1, le=2),
        detail_probes: int = Query(1, ge=0, le=2),
        _actor=Depends(require_admin),
    ):
        if source_id not in source_ids():
            raise HTTPException(status_code=404, detail="Unknown ETP")
        key = (source_id, pages, detail_probes)
        async with _LOCK:
            hit = _CACHE.get(key)
            if hit is not None and time.monotonic() - hit[0] < _CACHE_SECONDS:
                return hit[1]
            try:
                result = await asyncio.wait_for(
                    diagnose_source(
                        source_id, max_pages=pages,
                        detail_probes=detail_probes,
                    ),
                    timeout=95,
                )
            except asyncio.TimeoutError as exc:
                raise HTTPException(
                    status_code=504, detail="ETP diagnosis timed out"
                ) from exc
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail="ETP diagnosis failed: " + type(exc).__name__,
                ) from exc
            if len(_CACHE) > 50:
                _CACHE.clear()
            _CACHE[key] = (time.monotonic(), result)
            return result

    return router
