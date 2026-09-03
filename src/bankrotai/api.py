from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import hmac
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from typing import Any

import uvicorn
from fastapi import Cookie, FastAPI, Depends, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from redis import Redis
from redis.exceptions import RedisError

from bankrotai.db import (
    session_scope,
    read_session_scope,
    get_processed_lot, 
    get_top_lots, 
    ProcessedLot, 
    get_region_sync_state,
    upsert_region_sync_state,
    BackgroundTaskState,
    LotSyncRun,
    LotSyncSourceRun,
    LotParticipationChecklist,
    SourceLot,
    DiagnosticEvent,
    LotDocument,
    LotDocumentChange,
    LotDocumentVersion,
    LotGeoSnapshot,
    LotNote,
    SavedMaxBidScenario,
    SavedSearch,
    SourceHealthState,
    Watchlist,
    SCHEMA_REVISION,
)
from bankrotai.logic import build_lots_response, build_stats_response, get_lot_response, persist_lot
from bankrotai.domain import NormalizedLot
from bankrotai.finance import MaxBidInputs, calculate_max_bid
from bankrotai.scrapers import (
    LotOnlineClient,
    TBankrotClient,
    TorgiGovClient,
    TorgiGovClientError,
    TorgiGovSearchFilters,
)
from bankrotai.scraper_contracts import LotOnlineSearchFilters, TBankrotSearchFilters
from bankrotai.geo import CadastralGeocoder, CadastralObjectResult
from bankrotai.services.duplicates import manual_merge_lots, manual_split_lot
from bankrotai.services.operations import (
    add_lot_note,
    diagnostic_export,
    save_max_bid_scenario,
    save_search,
    toggle_watchlist,
)
from bankrotai.services.quality import data_quality_snapshot, list_source_health
from bankrotai.services.map_view import build_map_lot_detail, build_map_lot_statistics, build_map_lots_response
from bankrotai.logic import log_action
from bankrotai.tasks import (
    QueueUnavailableError,
    schedule_bulk_torgi_sync,
    schedule_nationwide_lot_sync,
    schedule_region_sync,
)
from bankrotai.services.ingestion import SyncAlreadyRunningError
from bankrotai.regions import REGION_DIRECTORY

from bankrotai.core import DEFAULT_REGION, get_logger, get_region_query_values, get_settings, utc_now
from bankrotai.services.trusted_time import trusted_time_status
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
_map_response_cache: dict[tuple, tuple[float, bytes, str]] = {}
_map_statistics_cache: dict[tuple, tuple[float, dict]] = {}
_map_response_cache_lock = threading.Lock()
_MAP_RESPONSE_CACHE_SECONDS = 60
_MAP_STATISTICS_CACHE_SECONDS = 300
_CADASTRAL_GEOCODER = CadastralGeocoder()
_CADASTRAL_CAPACITY = threading.BoundedSemaphore(1)
_CADASTRAL_DEADLINE_SECONDS = 7.0
_PUBLIC_HEALTH_PATHS = {"/health", "/health/live", "/health/ready"}
_SESSION_COOKIE = "bankrotai_session"
_LOGIN_PATHS = {"/api/auth/login", "/api/auth/logout"}
_READ_ONLY_EXACT_PATHS = {"/api/lots", "/api/stats", "/api/auth/login", "/api/auth/logout", "/api/auth/me"}
_EXPECTED_SCHEMA_REVISION = SCHEMA_REVISION
_AUTH_EXECUTOR_WORKERS = max(2, settings.database_pool_size + settings.database_max_overflow)
_AUTH_EXECUTOR = ThreadPoolExecutor(
    max_workers=_AUTH_EXECUTOR_WORKERS,
    thread_name_prefix="bankrotai-auth",
)
_AUTH_EXECUTOR_CAPACITY = threading.BoundedSemaphore(_AUTH_EXECUTOR_WORKERS)
_AUTH_PENDING_LIMIT = max(100, _AUTH_EXECUTOR_WORKERS * 10)
_AUTH_PENDING_CAPACITY = threading.BoundedSemaphore(_AUTH_PENDING_LIMIT)


def _resolve_session_actor(token: str) -> AuthenticatedUser | None:
    with read_session_scope() as session:
        return verify_session_token(session, token, settings.auth_session_secret or "")


def _resolve_session_actor_with_capacity(token: str) -> AuthenticatedUser | None:
    try:
        return _resolve_session_actor(token)
    finally:
        _AUTH_EXECUTOR_CAPACITY.release()


async def _wait_for_auth_executor_capacity(deadline: float) -> bool:
    loop = asyncio.get_running_loop()
    while loop.time() < deadline:
        if _AUTH_EXECUTOR_CAPACITY.acquire(blocking=False):
            return True
        await asyncio.sleep(min(0.005, max(0.0, deadline - loop.time())))
    return False


def _persist_request_failure(method: str, path: str, error: str) -> None:
    with session_scope() as session:
        session.add(DiagnosticEvent(
            severity="error",
            component="api",
            message="Unhandled API request error",
            context_json={"method": method, "path": path, "error": error[:2000]},
        ))


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
    model_config = ConfigDict(extra="forbid")

    scenario_name: str | None = Field(None, max_length=200)
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


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=10_000)


class SavedSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field("", max_length=200)
    query: dict[str, Any]


class DuplicateMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secondary_lot_id: int = Field(gt=0)
    reason: str = Field("", max_length=2000)


class DuplicateSplitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field("", max_length=2000)


class ReviewStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(None, pattern="^(approved|maybe|rejected)$")


class DocumentCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_version_id: int = Field(gt=0)
    to_version_id: int = Field(gt=0)


class OnlineLotImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=50)
    source_system: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=5000)
    description: str = Field("", max_length=50_000)
    category: str = Field("other", max_length=100)
    region_slug: str | None = Field(None, max_length=100)
    region_name: str | None = Field(None, max_length=200)
    address: str | None = Field(None, max_length=5000)
    cadastral_number: str | None = Field(None, max_length=100)
    current_price: float | None = Field(None, ge=0)
    start_price: float | None = Field(None, ge=0)
    auction_status: str = Field("unknown", max_length=100)
    lot_url: str | None = Field(None, max_length=5000)
    source_url: str | None = Field(None, max_length=5000)
    published_at: datetime | None = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    supplied_request_id = request.headers.get("x-request-id", "")
    request_id = supplied_request_id if len(supplied_request_id) <= 128 else ""
    request_id = request_id or str(uuid.uuid4())
    request.state.request_id = request_id
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
        if not _AUTH_PENDING_CAPACITY.acquire(blocking=False):
            logger.error("Session verification pending limit exhausted")
            return JSONResponse(status_code=503, content={"detail": "Authentication dependency unavailable"})
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + settings.database_auth_timeout_seconds
            if not await _wait_for_auth_executor_capacity(deadline):
                logger.error("Session verification timed out waiting for executor capacity")
                return JSONResponse(status_code=503, content={"detail": "Authentication dependency unavailable"})
            try:
                actor_future = loop.run_in_executor(
                    _AUTH_EXECUTOR,
                    _resolve_session_actor_with_capacity,
                    token,
                )
            except Exception:
                _AUTH_EXECUTOR_CAPACITY.release()
                raise
            try:
                actor = await asyncio.wait_for(
                    actor_future,
                    timeout=max(0.001, deadline - loop.time()),
                )
            except TimeoutError:
                logger.error("Session verification timed out")
                return JSONResponse(status_code=503, content={"detail": "Authentication dependency unavailable"})
            except Exception as exc:
                logger.error("Session verification failed: %s", exc)
                return JSONResponse(status_code=503, content={"detail": "Authentication dependency unavailable"})
        finally:
            _AUTH_PENDING_CAPACITY.release()
        if actor is None:
            return JSONResponse(status_code=401, content={"detail": "Session is invalid or expired"})
        request.state.authenticated_user = actor

    actor = getattr(request.state, "authenticated_user", None)
    # Cloudflare replaces CF-Connecting-IP with a shared Worker address on
    # cross-zone subrequests.  A verified session identity is therefore the
    # only stable, non-spoofable rate-limit key for authenticated traffic.
    client_ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")
    rate_limit_key = f"user:{actor.id}" if actor is not None else f"ip:{client_ip}"
    if not await asyncio.to_thread(_consume_rate_limit, rate_limit_key):
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    
    start_time = time.time()
    logger.info("Incoming request: request_id=%s method=%s url=%s", request_id, request.method, request.url)
    try:
        response = await call_next(request)
        process_time = (time.time() - start_time) * 1000
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "Response complete: request_id=%s status=%s duration_ms=%.2f",
            request_id,
            response.status_code,
            process_time,
        )
        return response
    except Exception as e:
        logger.exception("Error processing request: %s", e)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(_persist_request_failure, request.method, request.url.path, str(e)),
                timeout=settings.database_auth_timeout_seconds,
            )
        except Exception:
            logger.exception("Failed to persist API diagnostic event")
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"}
        )


def _consume_rate_limit(client_id: str) -> bool:
    base_limit = max(settings.api_rate_limit_per_minute, 1)
    # Keep login and other unauthenticated traffic on the strict per-IP limit.
    # The map workspace legitimately fans out many reads, so a cryptographically
    # verified session gets a larger per-user bucket without weakening brute-force
    # protection on the login endpoint.
    limit = base_limit * 10 if client_id.startswith("user:") else base_limit
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
    method = request.method
    if method in {"HEAD", "OPTIONS"}:
        return True
    if path in _LOGIN_PATHS:
        return method == "POST"
    if method == "GET":
        if path in _READ_ONLY_EXACT_PATHS:
            return True
        if path in {
            "/api/map/lots",
            "/api/cadastre/search",
            "/api/quality",
            "/api/sources",
            "/api/diagnostics",
            "/api/capabilities",
            "/api/regions",
            "/api/watchlist",
            "/api/saved-searches",
        }:
            return True
        if path.startswith("/api/map/lots/"):
            return path.rsplit("/", 1)[-1].isdigit()
        if path.startswith("/api/search/"):
            return True
        if path.startswith("/api/sync/lots/"):
            return True
        if path.startswith("/api/lots/"):
            parts = path.split("/")
            return len(parts) == 4 and parts[3].isdigit() or (
                len(parts) == 5
                and parts[3].isdigit()
                and parts[4] in {"procedure", "participation", "notes", "documents", "max-bid-scenarios"}
            )
        return False
    if method == "POST":
        if path in {"/api/saved-searches", "/api/search/import", "/api/sync/lots"}:
            return True
        if path.startswith("/api/lots/"):
            parts = path.split("/")
            return (
                len(parts) == 5
                and parts[3].isdigit()
                and parts[4] in {"max-bid", "watchlist", "notes", "merge", "split", "documents-compare"}
            )
        return False
    if method == "PUT" and path.startswith("/api/lots/"):
        parts = path.split("/")
        return (
            len(parts) == 5
            and parts[3].isdigit()
            and parts[4] in {"participation", "review-status"}
        )
    return False


def require_user(request: Request) -> AuthenticatedUser:
    actor = getattr(request.state, "authenticated_user", None)
    if actor is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return actor


def require_admin(actor: AuthenticatedUser = Depends(require_user)) -> AuthenticatedUser:
    if actor.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return actor

@app.get("/")
def read_root():
    return {"message": "Welcome to BankrotAI API"}


@app.get("/api/time")
def current_time(_: AuthenticatedUser = Depends(require_user)):
    return trusted_time_status()

@app.get("/health/live")
async def liveness_check():
    # Keep liveness off AnyIO's shared worker-thread pool. Slow synchronous
    # source/database calls must not make a healthy event loop look dead.
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
        with read_session_scope() as session:
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


def _cached_public_source_lots(
    source_system: str,
    *,
    search: str,
    region: str | None,
    price_min: float | None,
    price_max: float | None,
    page: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], int]:
    with read_session_scope() as session:
        conditions = [SourceLot.source_system == source_system, SourceLot.is_archived.is_(False)]
        cached_region_name = func.coalesce(
            SourceLot.region_name,
            SourceLot.raw_data["region_name"].as_string(),
        )
        if region:
            region_pattern = f"%{region.casefold()}%"
            missing_region = func.coalesce(cached_region_name, "") == ""
            conditions.append(
                func.lower(cached_region_name).like(region_pattern)
                | (
                    missing_region
                    & (
                        func.lower(func.coalesce(SourceLot.address, "")).like(region_pattern)
                        | func.lower(func.coalesce(SourceLot.title, "")).like(region_pattern)
                    )
                )
            )
        if search:
            pattern = f"%{search.casefold()}%"
            conditions.append(
                func.lower(func.coalesce(SourceLot.title, "")).like(pattern)
                | func.lower(func.coalesce(SourceLot.address, "")).like(pattern)
            )
        if price_min is not None:
            conditions.append(func.coalesce(SourceLot.current_price, SourceLot.start_price) >= price_min)
        if price_max is not None:
            conditions.append(func.coalesce(SourceLot.current_price, SourceLot.start_price) <= price_max)
        total = session.scalar(select(func.count()).select_from(SourceLot).where(*conditions)) or 0
        rows = session.scalars(
            select(SourceLot)
            .where(*conditions)
            .order_by(SourceLot.last_seen_at.desc(), SourceLot.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    items = [{
        "external_id": row.external_id,
        "source": source_system,
        "source_system": source_system,
        "title": row.title or "Лот без названия",
        "description": row.description or "",
        "category": row.category or "real_estate",
        "region_slug": row.region_code or ((row.raw_data or {}).get("region_code") if isinstance(row.raw_data, dict) else None),
        "region_name": row.region_name or ((row.raw_data or {}).get("region_name") if isinstance(row.raw_data, dict) else None),
        "address": row.address,
        "cadastral_number": row.cadastral_number,
        "area": None,
        "start_price": row.start_price,
        "current_price": row.current_price,
        "auction_status": row.source_status or "active",
        "lot_url": row.lot_url or row.source_url,
        "source_url": row.source_url or row.lot_url,
        "detail_level": "cached",
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "raw_data": row.raw_data or {},
    } for row in rows]
    return items, total


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
        client = TorgiGovClient(diagnostics=diagnostics, base_url=settings.torgi_gov_base_url)
        lots, meta = client.search_lots(filters)
    except TorgiGovClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"items": [_normalized_lot_to_dict(lot) for lot in lots], "meta": meta}


@app.get("/api/search/{source}")
async def search_auction_source(
    source: str,
    search: str = Query("", max_length=200),
    region: str = Query("", max_length=200),
    category: str = Query("", max_length=100),
    price_min: float | None = Query(None, ge=0),
    price_max: float | None = Query(None, ge=0),
    page: int = Query(1, ge=1, le=100),
    page_size: int = Query(20, ge=1, le=100),
    include_closed: bool = False,
):
    """Search a public auction catalogue without mutating the local read model."""

    if price_min is not None and price_max is not None and price_min > price_max:
        raise HTTPException(status_code=422, detail="price_min must be <= price_max")
    region_name = _normalize_public_region(region)
    request_timeout = (settings.external_connect_timeout, settings.external_read_timeout)
    # The browser-facing route crosses an edge proxy with a 10-second request
    # deadline. GIS search can make a JSON request and then an HTML fallback, so
    # keep each attempt bounded and leave time to return the controlled empty
    # state when the public source is unavailable.
    torgi_request_timeout = (
        min(settings.external_connect_timeout, 2.0),
        min(settings.external_read_timeout, 3.0),
    )
    cached_system = {"tbankrot": "tbankrot.ru", "lot-online": "lot-online.ru"}.get(source)
    if settings.online_source_cache_first and cached_system:
        items, total = await asyncio.to_thread(
            _cached_public_source_lots,
            cached_system,
            search=search,
            region=region_name,
            price_min=price_min,
            price_max=price_max,
            page=page,
            page_size=page_size,
        )
        return {
            "source": source,
            "items": items,
            "meta": {"total": total, "cached": True, "source_available": None, "warnings": []},
        }
    try:
        if source == "torgi-gov":
            filters = TorgiGovSearchFilters(
                search_text=search,
                subject_rf=region_name,
                category_code=(
                    TorgiGovClient.CATEGORY_LABEL_TO_CODE.get(category.lower(), category) if category else None
                ),
                price_min=price_min,
                price_max=price_max,
                lot_status=None if include_closed else TorgiGovClient.DEFAULT_LOT_STATUS,
                page=page,
                page_size=page_size,
            )
            lots, metadata = await asyncio.to_thread(
                TorgiGovClient(timeout=torgi_request_timeout, base_url=settings.torgi_gov_base_url).search_lots,
                filters,
            )
        elif source == "tbankrot":
            filters = TBankrotSearchFilters(
                search_text=search,
                region=TBankrotClient.normalize_region_filter(region),
                price_min=price_min,
                price_max=price_max,
                category_codes=category or "3,4,5",
                show_closed=include_closed,
                page=page,
                page_size=page_size,
            )
            lots, metadata = await asyncio.to_thread(
                TBankrotClient(timeout=settings.external_read_timeout).search_filtered_lots,
                filters,
            )
        elif source == "lot-online":
            filters = LotOnlineSearchFilters(
                search_text=search,
                category_id=category or "1",
                region_feature=region_name,
                archive_mode="true" if include_closed else "false",
                page=page,
                page_size=page_size,
            )
            lots, metadata = await asyncio.to_thread(LotOnlineClient(timeout=request_timeout).search_lots, filters)
        else:
            raise HTTPException(status_code=404, detail="Unknown auction source")
    except HTTPException:
        raise
    except TorgiGovClientError as exc:
        logger.warning("Public GIS search unavailable; returning a controlled empty state: %s", exc)
        return {
            "source": source,
            "items": [],
            "meta": {
                "total": 0,
                "warnings": ["ГИС Торги временно недоступен; показано корректное пустое состояние."],
                "source_available": False,
            },
        }
    except Exception as exc:
        logger.warning("Public search failed for %s: %s", source, exc)
        raise HTTPException(status_code=502, detail=f"Source {source} is temporarily unavailable") from exc
    return {
        "source": source,
        "items": [_normalized_lot_to_dict(lot) for lot in lots],
        "meta": metadata,
    }


@app.post("/api/search/import", status_code=201)
def import_online_lot(
    request: OnlineLotImportRequest,
    actor: AuthenticatedUser = Depends(require_user),
):
    value = request.model_dump()
    normalized = NormalizedLot(
        **value,
        vin=None,
        area=None,
        detail_level="search",
        raw_data={"imported_from": "web_search"},
    )
    with session_scope() as session:
        lot = persist_lot(session, normalized)
        log_action(
            session,
            str(actor.id),
            "import_search_result",
            "lot",
            str(lot.id),
            {"source_system": request.source_system, "external_id": request.external_id},
        )
        return {"id": lot.id, "external_id": lot.external_id, "source_system": lot.source_system}


@app.post("/api/online/torgi-gov/sync", status_code=202, dependencies=[Depends(require_admin)])
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


@app.get("/api/tasks/{task_id}", dependencies=[Depends(require_admin)])
def get_background_task_status(task_id: str):
    with read_session_scope() as session:
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


@app.post("/api/sync/lots", status_code=202)
def start_nationwide_lot_sync(actor: AuthenticatedUser = Depends(require_admin)):
    try:
        task_id = schedule_nationwide_lot_sync(triggered_by=actor.username)
    except SyncAlreadyRunningError as exc:
        return JSONResponse(
            status_code=409,
            content={"task_id": exc.run_id, "status": "already_running"},
        )
    except QueueUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"task_id": task_id, "status": "queued"}


@app.get("/api/sync/lots/{task_id}")
def get_nationwide_lot_sync(task_id: str, actor: AuthenticatedUser = Depends(require_user)):
    with read_session_scope() as session:
        run = session.get(LotSyncRun, task_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Synchronization task not found")
        sources = session.scalars(
            select(LotSyncSourceRun)
            .where(LotSyncSourceRun.sync_run_id == task_id)
            .order_by(LotSyncSourceRun.source_system)
        ).all()
        return {
            "task_id": run.id,
            "status": run.status,
            "trigger_type": run.trigger_type,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "heartbeat_at": run.heartbeat_at,
            "result": run.result_json,
            "sources": [
                {
                    "source_system": source.source_system,
                    "status": source.status,
                    "complete_source_run": source.complete_source_run,
                    "pages_scanned": source.pages_scanned,
                    "items_seen": source.items_seen,
                    "items_inserted": source.items_inserted,
                    "items_updated": source.items_updated,
                    "items_unchanged": source.items_unchanged,
                    "items_archived": source.items_archived,
                    "items_failed": source.items_failed,
                    "geocoded": source.geocoded,
                    "duplicates_merged": source.duplicates_merged,
                    "error": source.error_message,
                }
                for source in sources
            ],
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
    
    with read_session_scope() as session:
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
    with read_session_scope() as session:
        item = get_lot_response(session, city_slug, lot_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Lot not found")
        return item


@app.get("/api/lots/{lot_id}/procedure")
def get_lot_procedure(lot_id: int):
    with read_session_scope() as session:
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
            "application_start_at": source_lot.application_start_at,
            "application_deadline": source_lot.application_deadline,
            "auction_at": source_lot.auction_at,
            "auction_timezone": source_lot.auction_timezone,
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
def calculate_lot_max_bid(
    lot_id: int,
    request: MaxBidRequest,
    actor: AuthenticatedUser = Depends(require_user),
):
    input_values = request.model_dump(exclude={"scenario_name"})
    inputs = MaxBidInputs(**input_values)
    with session_scope() as session:
        if session.get(ProcessedLot, lot_id) is None:
            raise HTTPException(status_code=404, detail="Lot not found")
        saved_id = None
        if request.scenario_name:
            saved = save_max_bid_scenario(
                session,
                lot_id,
                inputs,
                name=request.scenario_name,
                user_id=str(actor.id),
            )
            saved_id = saved.id
            log_action(session, str(actor.id), "save_max_bid", "lot", str(lot_id), {"scenario_id": saved.id})
    scenarios = calculate_max_bid(inputs)
    return {
        "lot_id": lot_id,
        "saved_scenario_id": saved_id,
        "valuation_source": "user_supplied_conservative_sale_price",
        "warning": "The bid ceiling is a financial scenario, not an independent property appraisal.",
        "scenarios": {name: asdict(value) for name, value in scenarios.items()},
    }


@app.get("/api/lots/{lot_id}/max-bid-scenarios")
def get_max_bid_scenarios(lot_id: int, actor: AuthenticatedUser = Depends(require_user)):
    with read_session_scope() as session:
        rows = session.scalars(
            select(SavedMaxBidScenario)
            .where(SavedMaxBidScenario.lot_id == lot_id, SavedMaxBidScenario.user_id == str(actor.id))
            .order_by(SavedMaxBidScenario.created_at.desc())
        ).all()
        return [
            {
                "id": row.id,
                "name": row.name,
                "inputs": row.inputs_json,
                "results": row.results_json,
                "created_at": row.created_at,
            }
            for row in rows
        ]


@app.get("/api/lots/{lot_id}/participation")
def get_participation_checklist(lot_id: int, actor: AuthenticatedUser = Depends(require_user)):
    with read_session_scope() as session:
        source_lot = session.scalar(select(SourceLot).where(SourceLot.processed_lot_id == lot_id))
        if source_lot is None:
            raise HTTPException(status_code=404, detail="Source lot not found")
        checklist = session.scalar(
            select(LotParticipationChecklist).where(
                LotParticipationChecklist.source_lot_id == source_lot.id,
                LotParticipationChecklist.user_id == str(actor.id),
            )
        )
        values: dict[str, Any] = {
            name: False for name in ParticipationChecklistRequest.model_fields if name != "notes"
        }
        values["notes"] = None
        if checklist is not None:
            values = {name: getattr(checklist, name) for name in ParticipationChecklistRequest.model_fields}
        return {"lot_id": lot_id, "source_lot_id": source_lot.id, **values}


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
        log_action(session, str(actor.id), "update_participation", "lot", str(lot_id), None)
        return {"lot_id": lot_id, "source_lot_id": source_lot.id, **request.model_dump()}


@app.post("/api/lots/{lot_id}/watchlist")
def toggle_lot_watchlist(lot_id: int, actor: AuthenticatedUser = Depends(require_user)):
    with session_scope() as session:
        try:
            enabled = toggle_watchlist(session, lot_id, user_id=str(actor.id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        log_action(session, str(actor.id), "toggle_watchlist", "lot", str(lot_id), {"enabled": enabled})
        return {"lot_id": lot_id, "watchlisted": enabled}


@app.get("/api/watchlist")
def get_watchlist(actor: AuthenticatedUser = Depends(require_user)):
    with read_session_scope() as session:
        lot_ids = session.scalars(
            select(Watchlist.lot_id)
            .where(Watchlist.user_id == str(actor.id), Watchlist.lot_id.is_not(None))
            .order_by(Watchlist.created_at.desc())
        ).all()
        items = []
        for lot_id in lot_ids:
            item = get_lot_response(session, DEFAULT_REGION, lot_id)
            if item is not None:
                items.append(item)
        return {"items": items, "total": len(items)}


@app.get("/api/lots/{lot_id}/notes")
def get_lot_note_items(lot_id: int, actor: AuthenticatedUser = Depends(require_user)):
    with read_session_scope() as session:
        rows = session.scalars(
            select(LotNote)
            .where(LotNote.lot_id == lot_id, LotNote.user_id == str(actor.id))
            .order_by(LotNote.created_at.desc())
        ).all()
        return [
            {"id": row.id, "content": row.content, "created_at": row.created_at, "updated_at": row.updated_at}
            for row in rows
        ]


@app.post("/api/lots/{lot_id}/notes", status_code=201)
def create_lot_note(lot_id: int, request: NoteRequest, actor: AuthenticatedUser = Depends(require_user)):
    with session_scope() as session:
        try:
            note = add_lot_note(session, lot_id, request.content, user_id=str(actor.id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        log_action(session, str(actor.id), "add_note", "lot", str(lot_id), {"note_id": note.id})
        return {"id": note.id, "lot_id": lot_id, "content": note.content, "created_at": note.created_at}


@app.put("/api/lots/{lot_id}/review-status")
def update_lot_review_status(
    lot_id: int,
    request: ReviewStatusRequest,
    actor: AuthenticatedUser = Depends(require_user),
):
    with session_scope() as session:
        lot = session.get(ProcessedLot, lot_id)
        if lot is None:
            raise HTTPException(status_code=404, detail="Lot not found")
        lot.review_status = request.status
        log_action(session, str(actor.id), "review_status", "lot", str(lot_id), {"status": request.status})
        result = {"lot_id": lot_id, "review_status": request.status}
    with _map_response_cache_lock:
        _map_response_cache.clear()
        _map_statistics_cache.clear()
    return result


@app.post("/api/lots/{lot_id}/merge")
def merge_lot_duplicate(
    lot_id: int,
    request: DuplicateMergeRequest,
    actor: AuthenticatedUser = Depends(require_admin),
):
    with session_scope() as session:
        try:
            review = manual_merge_lots(
                session,
                lot_id,
                request.secondary_lot_id,
                reason=request.reason,
                user_id=str(actor.id),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        log_action(session, str(actor.id), "merge_lots", "lot", str(lot_id), {"secondary": request.secondary_lot_id})
        return {"review_id": review.id, "primary_lot_id": lot_id, "secondary_lot_id": request.secondary_lot_id}


@app.post("/api/lots/{lot_id}/split")
def split_lot_duplicate(
    lot_id: int,
    request: DuplicateSplitRequest,
    actor: AuthenticatedUser = Depends(require_admin),
):
    with session_scope() as session:
        try:
            review = manual_split_lot(session, lot_id, reason=request.reason, user_id=str(actor.id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        log_action(session, str(actor.id), "split_lot", "lot", str(lot_id), None)
        return {"review_id": review.id, "lot_id": lot_id}

@app.get("/api/stats")
def get_stats(city_slug: str = DEFAULT_REGION):
    with read_session_scope() as session:
        return build_stats_response(session, city_slug)


@app.get("/api/map/lots")
def get_map_lots(
    request: Request,
    city_slug: str | None = None,
    region_code: str | None = Query(None, pattern="^\\d{2,3}$"),
    min_start_price: float | None = Query(None, ge=0),
    max_start_price: float | None = Query(None, ge=0),
    include_archived: bool = False,
    limit: int = Query(3000, ge=1, le=5000),
    west: float | None = Query(None, ge=-180, le=180),
    south: float | None = Query(None, ge=-90, le=90),
    east: float | None = Query(None, ge=-180, le=180),
    north: float | None = Query(None, ge=-90, le=90),
    review_status: str | None = Query(None, pattern="^(approved|maybe|rejected)$"),
):
    bounds = (west, south, east, north)
    if any(value is not None for value in bounds) and not all(value is not None for value in bounds):
        raise HTTPException(status_code=422, detail="west, south, east and north must be provided together")
    if min_start_price is not None and max_start_price is not None and min_start_price > max_start_price:
        raise HTTPException(status_code=422, detail="min_start_price must not exceed max_start_price")
    cache_key = (
        city_slug, region_code, min_start_price, max_start_price,
        include_archived, limit, west, south, east, north, review_status,
    )
    statistics_key = (
        city_slug, region_code, min_start_price, max_start_price,
        include_archived, review_status,
    )
    now = time.monotonic()
    with _map_response_cache_lock:
        cached = _map_response_cache.get(cache_key)
    cache_state = "HIT"
    if cached is None or now - cached[0] >= _MAP_RESPONSE_CACHE_SECONDS:
        cache_state = "MISS"
        with _map_response_cache_lock:
            statistics_cached = _map_statistics_cache.get(statistics_key)
        statistics = None
        if statistics_cached is not None and now - statistics_cached[0] < _MAP_STATISTICS_CACHE_SECONDS:
            statistics = statistics_cached[1]
        with read_session_scope() as session:
            bounded_viewport = all(value is not None for value in bounds)
            if statistics is None and not bounded_viewport:
                statistics = build_map_lot_statistics(
                    session,
                    city_slug=city_slug,
                    region_code=region_code,
                    min_start_price=min_start_price,
                    max_start_price=max_start_price,
                    include_archived=include_archived,
                    review_status=review_status,
                )
                with _map_response_cache_lock:
                    _map_statistics_cache[statistics_key] = (now, statistics)
            value = build_map_lots_response(
                session,
                city_slug=city_slug,
                region_code=region_code,
                min_start_price=min_start_price,
                max_start_price=max_start_price,
                include_archived=include_archived,
                limit=limit,
                west=west,
                south=south,
                east=east,
                north=north,
                review_status=review_status,
                statistics=statistics,
                defer_statistics=bounded_viewport and statistics is None,
            )
        encoded = jsonable_encoder(value)
        body = json.dumps(encoded, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        stable_value = {key: item for key, item in encoded.items() if key != "timings"}
        stable_body = json.dumps(stable_value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        etag = '"' + hashlib.sha256(stable_body).hexdigest() + '"'
        cached = (now, body, etag)
        with _map_response_cache_lock:
            expired = [key for key, item in _map_response_cache.items() if now - item[0] >= 300]
            for key in expired:
                _map_response_cache.pop(key, None)
            if len(_map_response_cache) >= 256:
                oldest = min(_map_response_cache, key=lambda key: _map_response_cache[key][0])
                _map_response_cache.pop(oldest, None)
            _map_response_cache[cache_key] = cached
    _, body, etag = cached
    headers = {
        "Cache-Control": "private, max-age=60, stale-while-revalidate=300",
        "ETag": etag,
        "X-Map-Cache": cache_state,
        "Server-Timing": f'map;desc="{cache_state}"',
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


@app.get("/api/map/lots/{lot_id}")
def get_map_lot(lot_id: int):
    with read_session_scope() as session:
        value = build_map_lot_detail(session, lot_id)
    if value is None:
        raise HTTPException(status_code=404, detail="Lot is not available on the map")
    return value


@app.get("/api/cadastre/search")
async def search_cadastre(query: str = Query(min_length=3, max_length=500)):
    normalized_query = query.strip()
    normalized_cadastral = normalized_query.replace(" ", "") if ":" in normalized_query else None
    try:
        with read_session_scope() as session:
            database_query = select(ProcessedLot).where(ProcessedLot.duplicate_of_id.is_(None))
            if normalized_cadastral:
                database_query = database_query.where(
                    func.replace(ProcessedLot.cadastral_number, " ", "") == normalized_cadastral
                )
            else:
                database_query = database_query.where(ProcessedLot.address.ilike(f"%{normalized_query}%"))
            stored_lot = session.scalar(database_query.order_by(ProcessedLot.is_archived, ProcessedLot.last_update.desc()))
            if stored_lot is not None:
                snapshot = session.scalar(
                    select(LotGeoSnapshot)
                    .where(LotGeoSnapshot.lot_id == stored_lot.id)
                    .order_by(LotGeoSnapshot.observed_at.desc(), LotGeoSnapshot.id.desc())
                )
                return asdict(CadastralObjectResult(
                    query=normalized_query,
                    cadastral_number=stored_lot.cadastral_number,
                    object_type=stored_lot.category,
                    title=stored_lot.title,
                    address=stored_lot.address,
                    lat=snapshot.centroid_lat if snapshot else None,
                    lon=snapshot.centroid_lon if snapshot else None,
                    geometry_json=snapshot.geometry_json if snapshot else None,
                    has_boundary=bool(snapshot and snapshot.geometry_json),
                    source="bankrotai_database",
                    confidence=snapshot.geo_confidence if snapshot else "medium",
                    info={"lot_id": stored_lot.id, "is_archived": stored_lot.is_archived},
                ))
    except SQLAlchemyError as exc:
        logger.warning("Cadastre database-first lookup unavailable: %s", exc)

    def unavailable_result() -> dict[str, Any]:
        cadastral_number = query if ":" in query else None
        return asdict(CadastralObjectResult(
            query=query,
            cadastral_number=cadastral_number,
            source="pkk/nspd",
            confidence="none",
            error="Кадастровые API временно недоступны; повторите проверку позже.",
        ))

    if not _CADASTRAL_CAPACITY.acquire(blocking=False):
        return unavailable_result()

    def bounded_search() -> CadastralObjectResult:
        try:
            return _CADASTRAL_GEOCODER.search(query)
        finally:
            _CADASTRAL_CAPACITY.release()

    task = asyncio.create_task(asyncio.to_thread(bounded_search))
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=_CADASTRAL_DEADLINE_SECONDS)
    except TimeoutError:
        task.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)
        return unavailable_result()
    except Exception as exc:
        logger.warning("Cadastre search failed: %s", exc)
        raise HTTPException(status_code=502, detail="Cadastre service is temporarily unavailable") from exc
    return asdict(result)


@app.get("/api/saved-searches")
def get_saved_searches(actor: AuthenticatedUser = Depends(require_user)):
    with read_session_scope() as session:
        rows = session.scalars(
            select(SavedSearch)
            .where(SavedSearch.user_id == str(actor.id))
            .order_by(SavedSearch.created_at.desc())
        ).all()
        return [
            {"id": row.id, "name": row.name, "query": row.query_params, "created_at": row.created_at}
            for row in rows
        ]


@app.post("/api/saved-searches", status_code=201)
def create_saved_search(request: SavedSearchRequest, actor: AuthenticatedUser = Depends(require_user)):
    with session_scope() as session:
        row = save_search(session, request.name, request.query, user_id=str(actor.id))
        log_action(session, str(actor.id), "save_search", "saved_search", str(row.id), None)
        return {"id": row.id, "name": row.name, "query": row.query_params, "created_at": row.created_at}


@app.get("/api/lots/{lot_id}/documents")
def get_lot_documents(lot_id: int):
    with read_session_scope() as session:
        source_lot = session.scalar(select(SourceLot).where(SourceLot.processed_lot_id == lot_id))
        if source_lot is None:
            return []
        documents = session.scalars(
            select(LotDocument)
            .where(LotDocument.source_lot_id == source_lot.id)
            .order_by(LotDocument.updated_at.desc())
        ).all()
        result = []
        for document in documents:
            versions = session.scalars(
                select(LotDocumentVersion)
                .where(LotDocumentVersion.document_id == document.id)
                .order_by(LotDocumentVersion.fetched_at.desc())
            ).all()
            result.append({
                "id": document.id,
                "filename": document.filename,
                "source_url": document.source_url,
                "document_kind": document.document_kind,
                "versions": [
                    {
                        "id": version.id,
                        "sha256": version.sha256,
                        "mime_type": version.mime_type,
                        "size_bytes": version.size_bytes,
                        "metadata": version.metadata_json,
                        "fetched_at": version.fetched_at,
                    }
                    for version in versions
                ],
            })
        return result


@app.post("/api/lots/{lot_id}/documents-compare")
def compare_lot_documents(
    lot_id: int,
    request: DocumentCompareRequest,
    actor: AuthenticatedUser = Depends(require_user),
):
    with session_scope() as session:
        source_lot = session.scalar(select(SourceLot).where(SourceLot.processed_lot_id == lot_id))
        if source_lot is None:
            raise HTTPException(status_code=404, detail="Source lot not found")
        before = session.get(LotDocumentVersion, request.from_version_id)
        after = session.get(LotDocumentVersion, request.to_version_id)
        if before is None or after is None:
            raise HTTPException(status_code=404, detail="Document version not found")
        document = session.get(LotDocument, before.document_id)
        if document is None or document.source_lot_id != source_lot.id:
            raise HTTPException(status_code=422, detail="Document does not belong to this lot")
        if before.document_id != after.document_id or before.id == after.id:
            raise HTTPException(status_code=422, detail="Choose two versions of the same document")
        existing = session.scalar(select(LotDocumentChange).where(
            LotDocumentChange.from_version_id == before.id,
            LotDocumentChange.to_version_id == after.id,
        ))
        if existing is None:
            before_meta = before.metadata_json or {}
            after_meta = after.metadata_json or {}
            summary = {
                "content_changed": before.sha256 != after.sha256,
                "size": {"before": before.size_bytes, "after": after.size_bytes},
                "mime_type": {"before": before.mime_type, "after": after.mime_type},
                "metadata_changes": {
                    key: {"before": before_meta.get(key), "after": after_meta.get(key)}
                    for key in sorted(set(before_meta) | set(after_meta))
                    if before_meta.get(key) != after_meta.get(key)
                },
            }
            existing = LotDocumentChange(
                document_id=before.document_id,
                from_version_id=before.id,
                to_version_id=after.id,
                summary_json=summary,
            )
            session.add(existing)
            session.flush()
        log_action(session, str(actor.id), "compare_documents", "lot", str(lot_id), None)
        return {"id": existing.id, "summary": existing.summary_json, "created_at": existing.created_at}


@app.get("/api/quality")
def get_data_quality():
    with read_session_scope() as session:
        return data_quality_snapshot(session).model_dump(mode="json")


@app.get("/api/sources")
def get_source_states():
    with read_session_scope() as session:
        return [item.model_dump(mode="json") for item in list_source_health(session)]


@app.get("/api/diagnostics", dependencies=[Depends(require_admin)])
def get_diagnostics():
    with read_session_scope() as session:
        return diagnostic_export(session)


@app.get("/api/capabilities")
def get_api_capabilities():
    """Describe deployment-level operations so clients do not render dead actions."""
    return {
        "curated_mode": settings.api_read_only,
        "region_sync": not settings.api_read_only,
        "bulk_torgi_sync": not settings.api_read_only,
        "background_jobs": not settings.api_read_only,
    }


def _public_regions() -> list[dict[str, str]]:
    return [
        {"code": region.code, "name": region.name}
        for region in sorted(REGION_DIRECTORY, key=lambda item: item.name)
    ]


def _normalize_public_region(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    cleaned = value.strip()
    if cleaned.isdigit():
        code = cleaned.zfill(2)
        return next((item["name"] for item in _public_regions() if item["code"] == code), cleaned)
    return cleaned


@app.get("/api/regions")
def get_public_regions():
    return _public_regions()

@app.post("/api/regions/{city_slug}/sync", dependencies=[Depends(require_admin)])
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

@app.get("/api/regions/{city_slug}/sync-status", dependencies=[Depends(require_admin)])
def get_sync_status(city_slug: str):
    with read_session_scope() as session:
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
