"""Authenticated, rate-limited ETP QA preview; never a production map source."""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from bankrotai.services.etp_qa_map import SUPPORTED, preview

_LOCK = asyncio.Lock()
_CACHE: dict[tuple[str, int, int, int], tuple[float, dict]] = {}
_CACHE_SECONDS = 180


def create_etp_qa_map_router(require_admin):
    router = APIRouter(prefix="/api/etp/qa-map", tags=["ETP manual QA"])

    @router.get("/sources")
    async def source_list(_actor=Depends(require_admin)):
        from bankrotai.services.etp_qa_map import PROFILES_BY_ID
        return {"items": [
            {"id": key, "label": PROFILES_BY_ID[key].platform_name,
             "blocked": bool(PROFILES_BY_ID[key].blocked_reason)}
            for key in sorted(SUPPORTED)
        ], "qa_only": True}

    @router.get("/{source_id}")
    async def preview_source(
        source_id: str,
        pages: int = Query(2, ge=1, le=2),
        per_page: int = Query(3, ge=1, le=3),
        geo_limit: int = Query(2, ge=0, le=3),
        _actor=Depends(require_admin),
    ):
        if source_id not in SUPPORTED:
            raise HTTPException(status_code=404, detail="Unknown ETP")
        key = (source_id, pages, per_page, geo_limit)
        async with _LOCK:
            now = time.monotonic()
            hit = _CACHE.get(key)
            if hit and now - hit[0] < _CACHE_SECONDS:
                return hit[1]
            try:
                result = await preview(
                    source_id, pages=pages, per_page=per_page,
                    geo_limit=geo_limit,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail="ETP QA public source not accessible: " + type(exc).__name__,
                ) from exc
            if len(_CACHE) > 60:
                _CACHE.clear()
            _CACHE[key] = (time.monotonic(), result)
            return result

    return router
