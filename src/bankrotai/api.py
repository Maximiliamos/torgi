from __future__ import annotations

import logging
import hmac
import time
from dataclasses import asdict
from typing import Any

import uvicorn
from fastapi import Cookie, FastAPI, Depends, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from sqlalchemy import select, text
from redis import Redis
from redis.exceptions import RedisError

from bankrotai.db import (
    session_scope, 
    get_processed_lot, 
    get_top_lots, 
    ProcessedLot, 
    get_region_sync_state,
    upsert_region_sync_state,
    BackgroundTaskState,
    LotParticipationChecklist,
    SourceLot,
    DiagnosticEvent,
)
from bankrotai.logic import build_lots_response, build_stats_response, get_lot_response
from bankrotai.finance import MaxBidInputs, calculate_max_bid
from bankrotai.scrapers import TorgiGovClient, TorgiGovClientError, TorgiGovSearchFilters
from bankrotai.tasks import QueueUnavailableError, schedule_bulk_torgi_sync, schedule_region_sync

from bankrotai.core import get_logger, get_settings, utc_now, DEFAULT_REGION
from bankrotai.auth import (
    AuthenticatedUser,
    authenticate_user,
    create_session_token,
    verify_session_token,
)
from bankrotai import __version__

logger = get_logger(__name__)
settings = get_settings()

app = FastAPI(title="BankrotAI API")
_rate_limit_hits: dict[str, list[float]] = {}
_PUBLIC_HEALTH_PATHS = {"/health", "/health/live", "/health/ready"}
_SESSION_COOKIE = "bankrotai_session"
_LOGIN_PATHS = {"/api/auth/login", "/api/auth/logout"}
_READ_ONLY_EXACT_PATHS = {"/api/lots", "/api/stats", "/api/auth/login", "/api/auth/logout", "/api/auth/me"}
_EXPECTED_SCHEMA_REVISION = "e0f1a2b3c4d5"


class BulkTorgiSyncRequest(BaseModel):
    search: str = Field("", max_length=200)
    region: str = Field("", max_length=200)
    category: str = Field("", max_length=100)
    price_min: float | None = Field(None, ge=0)
    price_max: float | None = Field(None, ge=0)
    notice_status: str | None = Field(None, max_length=100)
    lot_status: str | None = Field(None, max_length=100)
    max_items: int = Field(10_000, ge=1, le=50_000)


class MaxBidRequest(BaseModel):
    conservative_sale_price: float = Field(gt=0)
    repair_cost: float = Field(0, ge=0)
    legal_cost: float = Field(0, ge=0)
    monthly_holding_cost: float = Field(0, ge=0)
    holding_months: float = Field(6, gt=0, le=120)
    taxes: float = Field(0, ge=0)
    sale_commission_percent: float = Field(0, ge=0, le=100)
    target_profit: float = Field(0, ge=0)
    risk_reserve: float = Field(0, ge=0)
    annual_capital_cost_percent: float = Field(0, ge=0, le=100)
    intended_bid: float | None = Field(None, ge=0)


class ParticipationChecklistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    etp_accredited: bool = False
    signature_valid: bool = False
    application_completed: bool = False
    deposit_sent: bool = False
    payment_purpose_verified: bool = False
    deposit_received: bool = False
    documents_signed: bool = False
    application_accepted: bool = False
    notes: str | None = Field(None, max_length=5000)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    if request.url.path in _PUBLIC_HEALTH_PATHS:
        return await call_next(request)

    configuration_errors = settings.production_configuration_errors()
    if configuration_errors:
        logger.error("Unsafe production configuration: %s", "; ".join(configuration_errors))
        return JSONResponse(
            status_code=503,
            content={"detail": "API security configuration is incomplete"},
        )

    if request.method != "OPTIONS" and settings.public_api_key:
        auth_header = request.headers.get("authorization", "")
        bearer_token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
        api_key = request.headers.get("x-api-key") or bearer_token
        if not api_key or not hmac.compare_digest(api_key, settings.public_api_key):
            return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
    elif request.method != "OPTIONS" and settings.is_production:
        return JSONResponse(status_code=503, content={"detail": "API authentication is not configured"})

    if settings.api_read_only and request.method != "OPTIONS" and not _is_read_only_mvp_path(request):
        return JSONResponse(status_code=404, content={"detail": "Endpoint is not part of the read-only MVP API"})

    session_auth_enabled = settings.is_production or bool(settings.auth_session_secret)
    if (
        session_auth_enabled
        and request.method != "OPTIONS"
        and request.url.path.startswith("/api/")
        and request.url.path not in _LOGIN_PATHS
    ):
        token = request.cookies.get(_SESSION_COOKIE, "")
        if not token or not settings.auth_session_secret:
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
        with session_scope() as session:
            actor = verify_session_token(session, token, settings.auth_session_secret)
        if actor is None:
            return JSONResponse(status_code=401, content={"detail": "Session is invalid or expired"})
        request.state.authenticated_user = actor

    client_ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")
    if not _consume_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    
    start_time = time.time()
    logger.info("Incoming request: %s %s", request.method, request.url)
    try:
        response = await call_next(request)
        process_time = (time.time() - start_time) * 1000
        logger.info("Response status: %s (took %.2fms)", response.status_code, process_time)
        return response
    except Exception as e:
        logger.exception("Error processing request: %s", e)
        try:
            with session_scope() as session:
                session.add(DiagnosticEvent(
                    severity="error",
                    component="api",
                    message="Unhandled API request error",
                    context_json={"method": request.method, "path": request.url.path, "error": str(e)[:2000]},
                ))
        except Exception:
            logger.exception("Failed to persist API diagnostic event")
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"}
        )


def _consume_rate_limit(client_id: str) -> bool:
    limit = max(settings.api_rate_limit_per_minute, 1)
    bucket = int(time.time() // 60)
    try:
        redis = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        key = f"bankrotai:api-rate:{bucket}:{client_id}"
        current = int(redis.incr(key))
        if current == 1:
            redis.expire(key, 70)
        return current <= limit
    except RedisError as exc:
        if settings.is_production and not settings.api_read_only:
            logger.error("Distributed rate limiter unavailable: %s", exc)
            return False

    now = time.time()
    hits = [ts for ts in _rate_limit_hits.get(client_id, []) if now - ts < 60]
    if len(hits) >= limit:
        return False
    hits.append(now)
    _rate_limit_hits[client_id] = hits
    return True


def _is_read_only_mvp_path(request: Request) -> bool:
    path = request.url.path.rstrip("/") or "/"
    if request.method not in {"GET", "HEAD", "OPTIONS"} and path not in _LOGIN_PATHS:
        return False
    if path in _READ_ONLY_EXACT_PATHS:
        return True
    if path.startswith("/api/lots/"):
        parts = path.split("/")
        return len(parts) == 4 and parts[3].isdigit() or (
            len(parts) == 5 and parts[3].isdigit() and parts[4] == "procedure"
        )
    return False


def require_user(request: Request) -> AuthenticatedUser:
    actor = getattr(request.state, "authenticated_user", None)
    if actor is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return actor

@app.get("/")
def read_root():
    return {"message": "Welcome to BankrotAI API"}

@app.get("/health/live")
def liveness_check():
    return {"status": "alive", "version": __version__}


@app.get("/health/ready")
@app.get("/health")
def readiness_check():
    checks: dict[str, Any] = {
        "configuration": "ok",
        "database": "unavailable",
        "schema": "unknown",
        "queue": "disabled_read_only" if settings.api_read_only else "unavailable",
    }
    configuration_errors = settings.production_configuration_errors()
    if configuration_errors:
        checks["configuration"] = configuration_errors
        return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
            checks["database"] = "ok"
            version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
            checks["schema"] = version or "missing"
            if version != _EXPECTED_SCHEMA_REVISION:
                return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})
    except Exception as exc:
        logger.error("Readiness database check failed: %s", exc)
        return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})
    if not settings.api_read_only:
        try:
            Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=2,
                socket_timeout=2,
                decode_responses=True,
            ).ping()
            checks["queue"] = "ok"
        except RedisError as exc:
            logger.error("Readiness Redis check failed: %s", exc)
            return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})
    return {"status": "ready", "checks": checks, "version": __version__}

# --- Endpoints ---


@app.post("/api/auth/login")
def login(request: LoginRequest, response: Response):
    with session_scope() as session:
        user = authenticate_user(session, request.username, request.password)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        if not settings.auth_session_secret:
            raise HTTPException(status_code=503, detail="Session authentication is not configured")
        token = create_session_token(user, settings.auth_session_secret, ttl_seconds=settings.auth_session_ttl_seconds)
        result = {"id": user.id, "username": user.username, "role": user.role}
    response.set_cookie(
        _SESSION_COOKIE,
        token,
        max_age=settings.auth_session_ttl_seconds,
        httponly=True,
        secure=settings.is_production,
        samesite="strict",
        path="/",
    )
    return result


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(_SESSION_COOKIE, path="/", httponly=True, secure=settings.is_production, samesite="strict")
    return {"status": "signed_out"}


@app.get("/api/auth/me")
def current_user(actor: AuthenticatedUser = Depends(require_user)):
    return {"id": actor.id, "username": actor.username, "role": actor.role}

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


@app.get("/api/lots/{lot_id}/procedure")
def get_lot_procedure(lot_id: int):
    with session_scope() as session:
        source_lot = session.scalar(select(SourceLot).where(SourceLot.processed_lot_id == lot_id))
        if source_lot is None:
            raise HTTPException(status_code=404, detail="Source lot not found")
        return {
            "source_lot_id": source_lot.id,
            "source_system": source_lot.source_system,
            "platform_name": source_lot.platform_name,
            "platform_code": source_lot.platform_code,
            "procedure_number": source_lot.procedure_number,
            "notice_number": source_lot.notice_number,
            "efresb_message_number": source_lot.efresb_message_number,
            "organizer_name": source_lot.organizer_name,
            "bankruptcy_case_number": source_lot.bankruptcy_case_number,
            "deposit_amount": float(source_lot.deposit_amount) if source_lot.deposit_amount is not None else None,
            "deposit_percent": source_lot.deposit_percent,
            "deposit_deadline": source_lot.deposit_deadline,
            "application_deadline": source_lot.application_deadline,
            "auction_at": source_lot.auction_at,
            "auction_step_amount": (
                float(source_lot.auction_step_amount) if source_lot.auction_step_amount is not None else None
            ),
            "auction_type": source_lot.auction_type,
            "public_offer_schedule": source_lot.public_offer_schedule,
            "next_interval_price": (
                float(source_lot.next_interval_price) if source_lot.next_interval_price is not None else None
            ),
            "next_price_reduction_at": source_lot.next_price_reduction_at,
            "document_completeness": source_lot.document_completeness,
            "inspection_procedure": source_lot.inspection_procedure,
        }


@app.post("/api/lots/{lot_id}/max-bid")
def calculate_lot_max_bid(lot_id: int, request: MaxBidRequest):
    with session_scope() as session:
        if session.get(ProcessedLot, lot_id) is None:
            raise HTTPException(status_code=404, detail="Lot not found")
    scenarios = calculate_max_bid(MaxBidInputs(**request.model_dump()))
    return {
        "lot_id": lot_id,
        "valuation_source": "user_supplied_conservative_sale_price",
        "warning": "The bid ceiling is a financial scenario, not an independent property appraisal.",
        "scenarios": {name: asdict(value) for name, value in scenarios.items()},
    }


@app.put("/api/lots/{lot_id}/participation")
def update_participation_checklist(
    lot_id: int,
    request: ParticipationChecklistRequest,
    actor: AuthenticatedUser = Depends(require_user),
):
    with session_scope() as session:
        source_lot = session.scalar(select(SourceLot).where(SourceLot.processed_lot_id == lot_id))
        if source_lot is None:
            raise HTTPException(status_code=404, detail="Source lot not found")
        checklist = session.scalar(
            select(LotParticipationChecklist).where(
                LotParticipationChecklist.source_lot_id == source_lot.id,
                LotParticipationChecklist.user_id == str(actor.id),
            )
        )
        if checklist is None:
            checklist = LotParticipationChecklist(source_lot_id=source_lot.id, user_id=str(actor.id))
            session.add(checklist)
        for field_name, value in request.model_dump().items():
            setattr(checklist, field_name, value)
        checklist.updated_at = utc_now()
        session.flush()
        return {"lot_id": lot_id, "source_lot_id": source_lot.id, **request.model_dump()}

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
