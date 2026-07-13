from __future__ import annotations

import logging
import math
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from threading import Lock, RLock
from typing import Any

from sqlalchemy import (
    JSON, DateTime, Float, ForeignKey, Integer, Numeric, String, Text,
    create_engine, event, select, desc, and_, MetaData
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session

from bankrotai.core import get_settings

logger = logging.getLogger(__name__)

# --- Models ---

# Recommended naming convention for SQLite to ensure all constraints have names
# https://alembic.sqlalchemy.org/en/latest/batch.html#conntrol-of-naming-conventions
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s"
}

class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=naming_convention)
    pass

def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine formula to calculate distance between two points on Earth."""
    R = 6371  # Earth radius in km
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

class RawLot(Base):
    __tablename__ = "raw_lots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

class AppSetting(Base):
    __tablename__ = "app_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

class ProcessedLot(Base):
    __tablename__ = "processed_lots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="tbankrot")
    source_system: Mapped[str] = mapped_column(String(50), nullable=False, default="tbankrot", index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    region_slug: Mapped[str | None] = mapped_column(String(100), index=True)
    region_name: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    cadastral_number: Mapped[str | None] = mapped_column(String(50), index=True)
    cadastral_numbers: Mapped[list[str] | None] = mapped_column(JSON)
    vin: Mapped[str | None] = mapped_column(String(20), index=True)
    start_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    auction_status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    market_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    market_price_min: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    market_price_max: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    discount_percent: Mapped[float | None] = mapped_column(Float, index=True)
    risk_score: Mapped[int | None] = mapped_column(Integer)
    ai_recommendation: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[float | None] = mapped_column(Float, index=True)
    links_to_analogs: Mapped[list[str] | None] = mapped_column(JSON)
    lot_url: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    area: Mapped[float | None] = mapped_column(Float)
    detail_level: Mapped[str] = mapped_column(String(30), nullable=False, default="detail")
    needs_human_review: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    is_deal_of_the_week: Mapped[bool] = mapped_column(default=False, nullable=False)
    review_status: Mapped[str | None] = mapped_column(String(20), default=None)  # 'approved', 'rejected', 'maybe'
    needs_geo_check: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("processed_lots.id", ondelete="SET NULL"), index=True)
    last_update: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    # Основные параметры объекта
    object_name: Mapped[str | None] = mapped_column(String(300))       # название объекта (если отличается от title)
    property_type: Mapped[str | None] = mapped_column(String(50))      # Тип: ТРК, ОСЗ, встроенное помещение
    total_area_gba: Mapped[float | None] = mapped_column(Float)        # общая площадь здания, м²
    gla: Mapped[float | None] = mapped_column(Float)                   # арендопригодная площадь
    land_area: Mapped[float | None] = mapped_column(Float)             # площадь участка
    floors: Mapped[int | None] = mapped_column(Integer)                # этажность
    year_built: Mapped[int | None] = mapped_column(Integer)            # год постройки

    # Арендный поток
    occupancy_rate: Mapped[float | None] = mapped_column(Float)        # заполняемость, %
    anchor_tenants: Mapped[str | None] = mapped_column(Text)           # ключевые арендаторы
    monthly_fixed_rent: Mapped[float | None] = mapped_column(Float)    # фикс. аренда в месяц
    monthly_variable_rent: Mapped[float | None] = mapped_column(Float) # переменная часть
    monthly_other_income: Mapped[float | None] = mapped_column(Float)  # реклама, парковка
    monthly_opex: Mapped[float | None] = mapped_column(Float)          # операционные расходы
    noi_annual: Mapped[float | None] = mapped_column(Float)            # чистый операционный доход

    # Юридический / технический статус
    legal_status: Mapped[str | None] = mapped_column(Text)             # банкротство/залог/обременения
    encumbrances: Mapped[str | None] = mapped_column(Text)             # текст обременений
    land_risk_flag: Mapped[bool] = mapped_column(default=False)
    technical_condition: Mapped[str | None] = mapped_column(Text)      # состояние
    power_kw: Mapped[float | None] = mapped_column(Float)              # мощность, кВт
    parking_spaces: Mapped[int | None] = mapped_column(Integer)        # число парковочных мест

class LotStatusEvent(Base):
    __tablename__ = "lot_status_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_status: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_status: Mapped[str] = mapped_column(String(100), nullable=False)
    status_confidence: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    trace_reason: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)
    source_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    snapshot_ref: Mapped[str | None] = mapped_column(String(100))
    metadata_json: Mapped[dict | None] = mapped_column(JSON)

class LotGeoSnapshot(Base):
    __tablename__ = "lot_geo_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False, index=True)
    geo_source: Mapped[str] = mapped_column(String(50), nullable=False)
    geo_method: Mapped[str] = mapped_column(String(50), nullable=False)
    geo_confidence: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    centroid_lat: Mapped[float] = mapped_column(Float, nullable=False)
    centroid_lon: Mapped[float] = mapped_column(Float, nullable=False)
    geometry_json: Mapped[dict | None] = mapped_column(JSON)
    trace_reason: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)
    source_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)

class LotPriceEvent(Base):
    __tablename__ = "lot_price_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    price_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="RUB")
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)
    source_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)

class ValuationRun(Base):
    __tablename__ = "valuation_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False, index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    run_kind: Mapped[str] = mapped_column(String(50), nullable=False, default="primary")
    valuation_method: Mapped[str] = mapped_column(String(100), nullable=False)
    valuation_confidence: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    valuation_version: Mapped[str] = mapped_column(String(50), nullable=False, default="v1")
    valuation_sources: Mapped[list[str] | None] = mapped_column(JSON)
    valuation_snapshot: Mapped[dict | None] = mapped_column(JSON)
    needs_human_review: Mapped[bool] = mapped_column(default=False, nullable=False)
    appraised_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)

class RegionSyncState(Base):
    __tablename__ = "region_sync_states"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="idle")
    requested_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    lots_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ready_lots: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)

class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

class LotNote(Base):
    __tablename__ = "lot_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

class SavedSearch(Base):
    __tablename__ = "saved_searches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    query_params: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    search_id: Mapped[int | None] = mapped_column(ForeignKey("saved_searches.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="telegram")
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

class WatchlistLot(Base):
    __tablename__ = "watchlist_lots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False, index=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

class WorkflowTask(Base):
    __tablename__ = "workflow_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    task_status: Mapped[str] = mapped_column(String(20), default="open")
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    due_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

class LotWorkflowState(Base):
    __tablename__ = "lot_workflow_states"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    pipeline_stage: Mapped[str] = mapped_column(String(50), default="triage")
    stage_note: Mapped[str | None] = mapped_column(Text)
    calendar_due_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

class LotEventSubscription(Base):
    __tablename__ = "lot_event_subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), default="telegram")
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_types: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(100))
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

# --- Session Management ---

APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
REPO_ROOT = APP_ROOT
_SCHEMA_LOCK = Lock()
DB_WRITE_LOCK = RLock()
_SCHEMA_READY = False

@lru_cache(maxsize=1)
def get_engine():
    settings = get_settings()
    engine_kwargs = {"future": True}
    if settings.database_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(settings.database_url, **engine_kwargs)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _configure_sqlite(connection, _record) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()
    return engine

SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)

def init_db() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY: return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY: return

        if getattr(sys, "frozen", False):
            Base.metadata.create_all(get_engine())
            _SCHEMA_READY = True
            return
        
        # Safe migration for cadastral_numbers
        try:
            with session_scope() as session:
                from sqlalchemy import text
                session.execute(text("ALTER TABLE processed_lots ADD COLUMN cadastral_numbers JSON"))
                logger.info("Column cadastral_numbers added to processed_lots")
        except Exception as e:
            if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
                pass
            else:
                logger.debug(f"Migration notice: {e}")

        from alembic import command
        from alembic.config import Config
        alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
        alembic_config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        alembic_config.set_main_option("sqlalchemy.url", get_settings().database_url)
        command.upgrade(alembic_config, "head")
        _SCHEMA_READY = True

@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

# --- Repository Helpers ---

def get_processed_lot(session: Session, lot_id: int) -> ProcessedLot | None:
    return session.get(ProcessedLot, lot_id)

def get_lot_notes(session: Session, lot_id: int) -> list[LotNote]:
    return list(session.scalars(select(LotNote).where(LotNote.lot_id == lot_id).order_by(desc(LotNote.created_at))).all())

def get_watchlists(session: Session, user_id: str) -> list[ProcessedLot]:
    stmt = select(ProcessedLot).join(Watchlist).where(Watchlist.user_id == user_id)
    return list(session.scalars(stmt).all())

def get_region_sync_state(session: Session, city_slug: str) -> RegionSyncState | None:
    return session.scalar(select(RegionSyncState).where(RegionSyncState.city_slug == city_slug))

def get_region_sync_status(session: Session, city_slug: str) -> dict:
    state = get_region_sync_state(session, city_slug)
    if not state: return {"status": "idle", "lots_discovered": 0}
    return {
        "status": state.status,
        "lots_discovered": state.lots_discovered,
        "ready_lots": state.ready_lots,
        "error": state.error_message
    }

def get_top_lots(session: Session, limit: int = 5) -> list[ProcessedLot]:
    stmt = select(ProcessedLot).where(ProcessedLot.rating.isnot(None)).order_by(desc(ProcessedLot.rating)).limit(limit)
    return list(session.scalars(stmt).all())

def find_unappraised_lots(session: Session, limit: int = 50) -> list[ProcessedLot]:
    stmt = select(ProcessedLot).where(ProcessedLot.rating.is_(None)).limit(limit)
    return list(session.scalars(stmt).all())

def upsert_region_sync_state(session: Session, city_slug: str, **kwargs) -> RegionSyncState:
    from sqlalchemy.exc import IntegrityError
    state = get_region_sync_state(session, city_slug)
    if not state:
        state = RegionSyncState(city_slug=city_slug)
        session.add(state)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            state = get_region_sync_state(session, city_slug)
            if not state: raise

    for k, v in kwargs.items():
        setattr(state, k, v)
    session.flush()
    return state

from datetime import timedelta

def _region_sync_is_stuck(state: RegionSyncState, *, now: datetime | None = None) -> bool:
    now = now or utc_now()
    reference_time = state.started_at or state.requested_at
    if state.status not in {"queued", "running"} or reference_time is None:
        return False
    # Increased timeout to 30 minutes
    return now - reference_time > timedelta(minutes=30)
