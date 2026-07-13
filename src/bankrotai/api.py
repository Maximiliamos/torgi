from __future__ import annotations

import logging
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select, text

from bankrotai.db import (
    session_scope, 
    get_processed_lot, 
    get_top_lots, 
    ProcessedLot, 
    get_region_sync_state,
    upsert_region_sync_state,
    BackgroundTaskState,
)
from bankrotai.logic import build_lots_response, build_stats_response, get_lot_response
from bankrotai.scrapers import TorgiGovClient, TorgiGovClientError, TorgiGovSearchFilters
from bankrotai.tasks import QueueUnavailableError, schedule_bulk_torgi_sync, schedule_region_sync

from bankrotai.core import get_logger, get_settings, utc_now, DEFAULT_REGION

logger = get_logger(__name__)
settings = get_settings()

app = FastAPI(title="BankrotAI API")
_rate_limit_hits: dict[str, list[float]] = {}


class BulkTorgiSyncRequest(BaseModel):
    search: str = Field("", max_length=200)
    region: str = Field("", max_length=200)
    category: str = Field("", max_length=100)
    price_min: float | None = Field(None, ge=0)
    price_max: float | None = Field(None, ge=0)
    notice_status: str | None = Field(None, max_length=100)
    lot_status: str | None = Field(None, max_length=100)
    max_items: int = Field(10_000, ge=1, le=50_000)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)

    now = time.time()
    client_ip = request.client.host if request.client else "unknown"
    hits = [ts for ts in _rate_limit_hits.get(client_ip, []) if now - ts < 60]
    if len(hits) >= settings.api_rate_limit_per_minute:
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    hits.append(now)
    _rate_limit_hits[client_ip] = hits

    if settings.public_api_key and request.method not in {"GET", "HEAD", "OPTIONS"}:
        auth_header = request.headers.get("authorization", "")
        bearer_token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
        api_key = request.headers.get("x-api-key") or bearer_token
        if api_key != settings.public_api_key:
            return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
    
    start_time = time.time()
    logger.info("Incoming request: %s %s", request.method, request.url)
    try:
        response = await call_next(request)
        process_time = (time.time() - start_time) * 1000
        logger.info("Response status: %s (took %.2fms)", response.status_code, process_time)
        return response
    except Exception as e:
        logger.exception("Error processing request: %s", e)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"}
        )

@app.get("/")
def read_root():
    return {"message": "Welcome to BankrotAI API"}

@app.get("/health/live")
def liveness_check():
    return {"status": "alive", "version": "1.0.0"}


@app.get("/health/ready")
@app.get("/health")
def readiness_check():
    checks: dict[str, Any] = {"database": "unavailable", "schema": "unknown", "queue": "optional"}
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
            checks["database"] = "ok"
            version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
            checks["schema"] = version or "missing"
    except Exception as exc:
        logger.error("Readiness database check failed: %s", exc)
        return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})
    return {"status": "ready", "checks": checks, "version": "1.0.0"}

# --- Endpoints ---

def _normalized_lot_to_dict(lot) -> dict:
    return {
        "external_id": lot.external_id,
        "source": lot.source,
        "source_system": lot.source_system,
        "title": lot.title,
        "description": lot.description,
        "category": lot.category,
        "region_slug": lot.region_slug,
        "region_name": lot.region_name,
        "address": lot.address,
        "cadastral_number": lot.cadastral_number,
        "area": lot.area,
        "start_price": lot.start_price,
        "current_price": lot.current_price,
        "auction_status": lot.auction_status,
        "lot_url": lot.lot_url,
        "source_url": lot.source_url,
        "detail_level": lot.detail_level,
        "published_at": lot.published_at.isoformat() if lot.published_at else None,
        "raw_data": lot.raw_data,
    }


@app.get("/api/online/torgi-gov/lots")
def get_torgi_gov_lots(
    search: str = Query("", max_length=200),
    region: str = Query("", max_length=200),
    category: str = Query("", max_length=100),
    price_min: float | None = Query(None, ge=0),
    price_max: float | None = Query(None, ge=0),
    notice_status: str | None = Query(None, max_length=100),
    lot_status: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1, le=100),
    page_size: int = Query(20, ge=1, le=100),
    all_pages: bool = Query(False),
    limit: int | None = Query(5000, ge=1, le=50_000),
    diagnostics: bool = Query(False),
):
    if all_pages:
        raise HTTPException(
            status_code=422,
            detail="all_pages is not supported by synchronous GET; use POST /api/online/torgi-gov/sync",
        )
    if price_min is not None and price_max is not None and price_min > price_max:
        raise HTTPException(status_code=422, detail="price_min must be <= price_max")
    category_code = TorgiGovClient.CATEGORY_LABEL_TO_CODE.get(category.lower(), category) if category else None
    filters = TorgiGovSearchFilters(
        search_text=search,
        subject_rf=region or None,
        category_code=category_code,
        price_min=price_min,
        price_max=price_max,
        notice_status=notice_status or None,
        lot_status=lot_status or None,
        page=page,
        page_size=page_size,
    )
    try:
        client = TorgiGovClient(diagnostics=diagnostics)
        lots, meta = client.search_lots(filters)
    except TorgiGovClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"items": [_normalized_lot_to_dict(lot) for lot in lots], "meta": meta}


@app.post("/api/online/torgi-gov/sync", status_code=202)
def trigger_torgi_gov_bulk_sync(request: BulkTorgiSyncRequest):
    if request.price_min is not None and request.price_max is not None and request.price_min > request.price_max:
        raise HTTPException(status_code=422, detail="price_min must be <= price_max")
    category_code = TorgiGovClient.CATEGORY_LABEL_TO_CODE.get(request.category.lower(), request.category) if request.category else None
    filters = TorgiGovSearchFilters(
        search_text=request.search.strip(),
        subject_rf=request.region or None,
        category_code=category_code,
        price_min=request.price_min,
        price_max=request.price_max,
        notice_status=request.notice_status,
        lot_status=request.lot_status,
        page=1,
        page_size=100,
    )
    try:
        task_id = schedule_bulk_torgi_sync(filters.__dict__, request.max_items)
    except QueueUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"task_id": task_id, "status": "queued"}


@app.get("/api/tasks/{task_id}")
def get_background_task_status(task_id: str):
    with session_scope() as session:
        state = session.query(BackgroundTaskState).filter_by(task_id=task_id).one_or_none()
        if state is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return {
            "task_id": state.task_id,
            "task_type": state.task_type,
            "status": state.status,
            "progress": state.progress_json,
            "result": state.result_json,
            "error": state.error_message,
        }

@app.get("/api/lots")
def get_lots(
    city_slug: str = DEFAULT_REGION,
    page: int = Query(1, ge=1, le=10_000),
    per_page: int = Query(12, ge=1, le=100),
    search: str = Query("", max_length=200),
    categories: str = "", # Comma separated
    statuses: str = "", # Comma separated
    min_price: float = Query(0, ge=0),
    max_price: float = Query(1e10, ge=0),
    min_discount: float = Query(0, ge=-100, le=100),
    max_discount: float = Query(100, ge=-100, le=100),
    min_risk: int | None = Query(None, ge=0, le=10),
    max_risk: int | None = Query(None, ge=0, le=10),
    sort: str = Query("recommended", pattern="^(recommended|price_asc|price_desc|discount|newest)$")
):
    if min_price > max_price:
        raise HTTPException(status_code=422, detail="min_price must be <= max_price")
    if min_discount > max_discount:
        raise HTTPException(status_code=422, detail="min_discount must be <= max_discount")
    if min_risk is not None and max_risk is not None and min_risk > max_risk:
        raise HTTPException(status_code=422, detail="min_risk must be <= max_risk")
    cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else None
    stat_list = [s.strip() for s in statuses.split(",") if s.strip()] if statuses else None
    
    with session_scope() as session:
        return build_lots_response(
            session, city_slug, 
            page=page, per_page=per_page, search=search,
            categories=cat_list, statuses=stat_list, 
            min_price=min_price, max_price=max_price, 
            min_discount=min_discount, max_discount=max_discount,
            min_risk=min_risk, max_risk=max_risk, sort_mode=sort
        )

@app.get("/api/lots/{lot_id}")
def get_lot(lot_id: int, city_slug: str = DEFAULT_REGION):
    with session_scope() as session:
        item = get_lot_response(session, city_slug, lot_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Lot not found")
        return item

@app.get("/api/stats")
def get_stats(city_slug: str = DEFAULT_REGION):
    with session_scope() as session:
        return build_stats_response(session, city_slug)

@app.post("/api/regions/{city_slug}/sync")
def trigger_region_sync(city_slug: str, force: bool = False):
    try:
        dispatch_mode = schedule_region_sync(city_slug, force=force)
    except QueueUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "citySlug": city_slug,
        "status": "queued",
        "dispatchMode": dispatch_mode
    }

@app.get("/api/regions/{city_slug}/sync-status")
def get_sync_status(city_slug: str):
    with session_scope() as session:
        state = get_region_sync_state(session, city_slug)
        if not state:
            return {
                "citySlug": city_slug,
                "status": "idle",
                "hasData": False,
                "readyLots": 0
            }
        return {
            "citySlug": city_slug,
            "status": state.status,
            "hasData": state.ready_lots > 0,
            "readyLots": state.ready_lots,
            "error": state.error_message
        }

def run_api(host: str = "0.0.0.0", port: int = 8000):
    uvicorn.run(app, host=host, port=port)
