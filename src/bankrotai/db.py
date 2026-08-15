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
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
    event,
    select,
    desc,
    and_,
    Index,
    MetaData,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
from sqlalchemy.engine import Engine

from bankrotai.core import get_settings

logger = logging.getLogger(__name__)


@event.listens_for(Engine, "connect")
def _register_sqlite_unicode_functions(connection, _record) -> None:
    """SQLite's built-in NOCASE/LOWER only handle ASCII."""
    if hasattr(connection, "create_function"):
        connection.create_function(
            "unicode_casefold",
            1,
            lambda value: value.casefold() if isinstance(value, str) else value,
            deterministic=True,
        )


# --- Models ---

# Recommended naming convention for SQLite to ensure all constraints have names
# https://alembic.sqlalchemy.org/en/latest/batch.html#conntrol-of-naming-conventions
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
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
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


class RawLot(Base):
    __tablename__ = "raw_lots"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_raw_lots_source_external_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class AppSetting(Base):
    __tablename__ = "app_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class AppUser(Base):
    __tablename__ = "app_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="reader", index=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class ProcessedLot(Base):
    __tablename__ = "processed_lots"
    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "external_id",
            name="uq_processed_lots_source_system_external_id",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
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
    is_archived: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
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
    duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("processed_lots.id", ondelete="SET NULL"), index=True
    )
    last_update: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    # Основные параметры объекта
    object_name: Mapped[str | None] = mapped_column(String(300))  # название объекта (если отличается от title)
    property_type: Mapped[str | None] = mapped_column(String(50))  # Тип: ТРК, ОСЗ, встроенное помещение
    total_area_gba: Mapped[float | None] = mapped_column(Float)  # общая площадь здания, м²
    gla: Mapped[float | None] = mapped_column(Float)  # арендопригодная площадь
    land_area: Mapped[float | None] = mapped_column(Float)  # площадь участка
    floors: Mapped[int | None] = mapped_column(Integer)  # этажность
    year_built: Mapped[int | None] = mapped_column(Integer)  # год постройки

    # Арендный поток
    occupancy_rate: Mapped[float | None] = mapped_column(Float)  # заполняемость, %
    anchor_tenants: Mapped[str | None] = mapped_column(Text)  # ключевые арендаторы
    monthly_fixed_rent: Mapped[float | None] = mapped_column(Float)  # фикс. аренда в месяц
    monthly_variable_rent: Mapped[float | None] = mapped_column(Float)  # переменная часть
    monthly_other_income: Mapped[float | None] = mapped_column(Float)  # реклама, парковка
    monthly_opex: Mapped[float | None] = mapped_column(Float)  # операционные расходы
    noi_annual: Mapped[float | None] = mapped_column(Float)  # чистый операционный доход

    # Юридический / технический статус
    legal_status: Mapped[str | None] = mapped_column(Text)  # банкротство/залог/обременения
    encumbrances: Mapped[str | None] = mapped_column(Text)  # текст обременений
    land_risk_flag: Mapped[bool] = mapped_column(default=False)
    technical_condition: Mapped[str | None] = mapped_column(Text)  # состояние
    power_kw: Mapped[float | None] = mapped_column(Float)  # мощность, кВт
    parking_spaces: Mapped[int | None] = mapped_column(Integer)  # число парковочных мест


class CanonicalLot(Base):
    """A physical asset grouped across registries, aggregators and ETPs."""

    __tablename__ = "canonical_lots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_key: Mapped[str] = mapped_column(String(300), nullable=False, unique=True, index=True)
    legacy_processed_lot_id: Mapped[int | None] = mapped_column(
        ForeignKey("processed_lots.id", ondelete="SET NULL"), unique=True, index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="other")
    address: Mapped[str | None] = mapped_column(Text)
    cadastral_number: Mapped[str | None] = mapped_column(String(50), index=True)
    area: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class SourceLot(Base):
    """A source-specific auction card linked to one canonical asset."""

    __tablename__ = "source_lots"
    __table_args__ = (UniqueConstraint("source_system", "external_id", name="uq_source_lots_source_external_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_lot_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_lots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    processed_lot_id: Mapped[int | None] = mapped_column(
        ForeignKey("processed_lots.id", ondelete="SET NULL"), unique=True, index=True
    )
    source_system: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    platform_name: Mapped[str | None] = mapped_column(String(300), index=True)
    platform_code: Mapped[str | None] = mapped_column(String(100), index=True)
    procedure_number: Mapped[str | None] = mapped_column(String(150), index=True)
    notice_number: Mapped[str | None] = mapped_column(String(150), index=True)
    efresb_message_number: Mapped[str | None] = mapped_column(String(150), index=True)
    debtor_name: Mapped[str | None] = mapped_column(String(500), index=True)
    organizer_name: Mapped[str | None] = mapped_column(String(500), index=True)
    auction_manager_name: Mapped[str | None] = mapped_column(String(500))
    bankruptcy_case_number: Mapped[str | None] = mapped_column(String(150), index=True)
    deposit_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    deposit_percent: Mapped[float | None] = mapped_column(Float)
    deposit_payment_details: Mapped[str | None] = mapped_column(Text)
    deposit_deadline: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    application_deadline: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    auction_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    auction_step_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    auction_step_percent: Mapped[float | None] = mapped_column(Float)
    auction_type: Mapped[str | None] = mapped_column(String(100), index=True)
    public_offer_schedule: Mapped[list[dict] | None] = mapped_column(JSON)
    next_interval_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    next_price_reduction_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    document_completeness: Mapped[str | None] = mapped_column(String(30))
    inspection_procedure: Mapped[str | None] = mapped_column(Text)
    organizer_contact: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict | None] = mapped_column(JSON)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class LotDocument(Base):
    __tablename__ = "lot_documents"
    __table_args__ = (
        UniqueConstraint("source_lot_id", "external_document_id", name="uq_lot_documents_source_external"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_lot_id: Mapped[int] = mapped_column(
        ForeignKey("source_lots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_document_id: Mapped[str] = mapped_column(String(200), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    document_kind: Mapped[str | None] = mapped_column(String(100), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class LotDocumentVersion(Base):
    __tablename__ = "lot_document_versions"
    __table_args__ = (UniqueConstraint("document_id", "sha256", name="uq_lot_document_versions_hash"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("lot_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class LotParticipationChecklist(Base):
    __tablename__ = "lot_participation_checklists"
    __table_args__ = (UniqueConstraint("source_lot_id", "user_id", name="uq_participation_source_user"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_lot_id: Mapped[int] = mapped_column(
        ForeignKey("source_lots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    etp_accredited: Mapped[bool] = mapped_column(default=False, nullable=False)
    signature_valid: Mapped[bool] = mapped_column(default=False, nullable=False)
    application_completed: Mapped[bool] = mapped_column(default=False, nullable=False)
    deposit_sent: Mapped[bool] = mapped_column(default=False, nullable=False)
    payment_purpose_verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    deposit_received: Mapped[bool] = mapped_column(default=False, nullable=False)
    documents_signed: Mapped[bool] = mapped_column(default=False, nullable=False)
    application_accepted: Mapped[bool] = mapped_column(default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


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


class LotStatusHistory(Base):
    """Business-level status transitions; raw observations remain in LotStatusEvent."""

    __tablename__ = "lot_status_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False, index=True)
    old_status: Mapped[str | None] = mapped_column(String(100))
    new_status: Mapped[str] = mapped_column(String(100), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="sync")


class LotGeoSnapshot(Base):
    __tablename__ = "lot_geo_snapshots"
    __table_args__ = (Index("ix_lot_geo_snapshots_viewport", "centroid_lat", "centroid_lon"),)
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
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="legacy", index=True)
    model: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False, default="v1")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed", index=True)
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


class BackgroundTaskState(Base):
    __tablename__ = "background_task_states"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    progress_json: Mapped[dict | None] = mapped_column(JSON)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class SourceHealthState(Base):
    __tablename__ = "source_health_states"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown", index=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    items_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class GeoFailure(Base):
    __tablename__ = "geo_failures"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lot_id: Mapped[int] = mapped_column(
        ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    last_failed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class DuplicateReview(Base):
    __tablename__ = "duplicate_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    primary_lot_id: Mapped[int] = mapped_column(
        ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    secondary_lot_id: Mapped[int] = mapped_column(
        ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="desktop")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class SavedMaxBidScenario(Base):
    __tablename__ = "saved_max_bid_scenarios"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("processed_lots.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="desktop", index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    inputs_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    results_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)


class LotDocumentChange(Base):
    __tablename__ = "lot_document_changes"
    __table_args__ = (UniqueConstraint("from_version_id", "to_version_id", name="uq_document_change_versions"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("lot_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_version_id: Mapped[int] = mapped_column(
        ForeignKey("lot_document_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_version_id: Mapped[int] = mapped_column(
        ForeignKey("lot_document_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class DiagnosticEvent(Base):
    __tablename__ = "diagnostic_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="info", index=True)
    component: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)


class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    lot_id: Mapped[int | None] = mapped_column(ForeignKey("processed_lots.id", ondelete="CASCADE"), index=True)
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
    watchlist_id: Mapped[int] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False, index=True
    )
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


def _migration_root() -> Path:
    """Locate Alembic resources in source, wheel/container, and frozen builds."""
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")),
        Path.cwd(),
        Path(__file__).resolve().parents[2],
    ]
    for candidate in candidates:
        if candidate and (candidate / "alembic.ini").is_file() and (candidate / "alembic").is_dir():
            return candidate
    return Path(__file__).resolve().parents[2]


REPO_ROOT = _migration_root()
SCHEMA_REVISION = "a2b3c4d5e6f7"
_SCHEMA_LOCK = Lock()
DB_WRITE_LOCK = RLock()
_SCHEMA_READY = False


@lru_cache(maxsize=1)
def get_engine():
    settings = get_settings()
    engine_kwargs: dict[str, Any] = {"future": True}
    if settings.database_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        engine_kwargs.update(
            {
                "pool_pre_ping": True,
                "pool_recycle": 300,
                "pool_use_lifo": True,
                "pool_size": settings.database_pool_size,
                "max_overflow": settings.database_max_overflow,
                "pool_timeout": settings.database_pool_timeout,
            }
        )
        if settings.database_url.startswith("postgresql"):
            engine_kwargs["connect_args"] = {
                "connect_timeout": settings.database_connect_timeout,
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 3,
                # Linux otherwise permits a black-holed established Neon TCP
                # connection to block a synchronous psycopg read for minutes.
                "tcp_user_timeout": settings.database_tcp_user_timeout_ms,
                "options": (
                    f"-c statement_timeout={settings.database_statement_timeout_ms} "
                    "-c lock_timeout=5000"
                ),
            }
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
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return

        from alembic import command
        from alembic.config import Config

        config_path = REPO_ROOT / "alembic.ini"
        script_path = REPO_ROOT / "alembic"
        if not config_path.exists() or not script_path.exists():
            raise RuntimeError(
                "Alembic migration resources are unavailable; the application cannot safely initialize its database"
            )
        alembic_config = Config(str(config_path))
        alembic_config.set_main_option("script_location", str(script_path))
        settings = get_settings()
        migration_url = settings.database_migration_url or settings.database_url
        alembic_config.set_main_option("sqlalchemy.url", migration_url)
        logger.info("Applying database migrations from %s", script_path)
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
    return list(
        session.scalars(select(LotNote).where(LotNote.lot_id == lot_id).order_by(desc(LotNote.created_at))).all()
    )


def get_watchlists(session: Session, user_id: str) -> list[ProcessedLot]:
    stmt = select(ProcessedLot).join(Watchlist).where(Watchlist.user_id == user_id)
    return list(session.scalars(stmt).all())


def get_region_sync_state(session: Session, city_slug: str) -> RegionSyncState | None:
    return session.scalar(select(RegionSyncState).where(RegionSyncState.city_slug == city_slug))


def get_region_sync_status(session: Session, city_slug: str) -> dict:
    state = get_region_sync_state(session, city_slug)
    if not state:
        return {"status": "idle", "lots_discovered": 0}
    return {
        "status": state.status,
        "lots_discovered": state.lots_discovered,
        "ready_lots": state.ready_lots,
        "error": state.error_message,
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
            if not state:
                raise

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
