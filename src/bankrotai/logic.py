from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, func, desc, asc, update
from sqlalchemy.orm import Session

from bankrotai.domain import NormalizedLot
from bankrotai.db import (
    LotGeoSnapshot,
    LotStatusHistory,
    LotStatusEvent,
    ProcessedLot,
)
from bankrotai.geo import enrich_lot_geo
from bankrotai.core import get_region_query_values, utc_now # Assuming utc_now exists or I should use datetime.now(timezone.utc)

logger = logging.getLogger(__name__)

# --- Classifiers ---

def classify_category(title: str, description: str) -> str: 
    h = f"{title} {description}".lower() 
 
    if any(token in h for token in ("автомоб", "vin", "грузов", "транспорт", "авто", "прицеп", "легков")): 
        return "car" 
 
    if "квартир" in h: 
        return "apartment" 
 
    if "нежилое помещение" in h or "нежилые помещения" in h or "помещени" in h: 
        return "commercial_room" 
 
    if "нежилое здание" in h or "здание" in h or "склад" in h or "цех" in h: 
        if "земельн" in h or "участ" in h: 
            return "commercial_building_with_land" 
        return "commercial_building" 
 
    if "жилой дом" in h or "дом жилой" in h or "коттедж" in h: 
        return "house" 
 
    if "земельн" in h or "участ" in h: 
        return "land" 
 
    if "машино-место" in h or "гараж" in h: 
        return "parking" 
 
    if "незаверш" in h: 
        return "unfinished" 
 
    if "комплекс" in h: 
        return "complex" 
 
    if "дебитор" in h or "право требования" in h: 
        return "receivable" 
 
    if "недвиж" in h: 
        return "real_estate" 
 
    return "other" 

# --- Ingest Utils ---

def _to_decimal(value: float | None) -> Decimal | None:
    if value is None: return None
    return Decimal(f"{value:.2f}")

# --- Pipelines (Geo & Status) ---

def build_geo_decision(city_slug: str, raw_payload: dict[str, Any], fallback_text: str = "") -> dict:
    lat, lon = 55.751574, 37.573856
    address = raw_payload.get("address") or raw_payload.get("location") or fallback_text
    
    return {
        "geo_source": "fallback",
        "geo_method": "default",
        "geo_confidence": "low",
        "centroid_lat": lat,
        "centroid_lon": lon,
        "trace_reason": "Geocoding simplified",
        "geometry_json": None,
        "metadata_json": {"address_used": address}
    }

def build_status_decision(raw_payload: dict[str, Any], fallback_status: str = "") -> dict: 
    raw_status = str(raw_payload.get("status") or fallback_status or "").lower() 
    dates = raw_payload.get("dates") or [] 
    date_text = " ".join( 
        f"{d.get('title', '')} {d.get('text', '')}" 
        for d in dates 
    ).lower() 
 
    text = f"{raw_status} {date_text}" 
 
    final_status = "unknown" 
 
    if any(word in text for word in ("заверш", "архив", "состоял", "продан")): 
        final_status = "closed" 
    elif any(word in text for word in ("опублик", "прием заявок", "приём заявок", "осталось", "до окончания")): 
        final_status = "active" 
    elif "начало торгов" in text: 
        final_status = "scheduled" 
 
    return { 
        "final_status": final_status, 
        "confidence": "medium", 
        "source": "primary", 
        "trace_reason": f"Normalized from raw status/dates: {text[:300]}" 
    } 

def normalize_status(status: str) -> str:
    value = (status or "").strip().lower()
    if not value:
        return "unknown"
    if any(token in value for token in ("closed", "completed", "finished", "заверш", "закрыт", "несостоя")):
        return "closed"
    if any(token in value for token in ("scheduled", "pending", "приём", "прием", "ожида")):
        return "scheduled"
    if any(token in value for token in ("active", "published", "open", "опублик", "идут торги")):
        return "active"
    if value in {"active", "scheduled", "closed", "unknown"}:
        return value
    return status.strip()

# --- Scoring Logic ---

def calculate_discount_percent(market_price: float | None, auction_price: float | None) -> float | None:
    if not market_price or not auction_price or market_price <= 0: return None
    return round(((market_price - auction_price) / market_price) * 100, 2)

def calculate_potential_profit(market_price: float | None, auction_price: float | None) -> float | None:
    if market_price is None or auction_price is None: return None
    return float(market_price - auction_price)

def calculate_rating( 
    discount_percent: float | None, 
    risk_score: int | None, 
    status: str = "active", 
    confidence: str = "low", 
    category: str = "", 
    legal_status: str | None = None, 
    address: str | None = None, 
    area: float | None = None, 
) -> float | None: 
    if discount_percent is None or risk_score is None: 
        return None 
 
    if status not in {"active", "scheduled"}: 
        return 0 
 
    score = 0.0 
 
    # Дисконт важен, но не должен доминировать 
    safe_discount = max(-50, min(discount_percent, 95)) 
    score += safe_discount * 0.35 
 
    # Риск 
    score += (10 - risk_score) * 3.0 
 
    # Базовая ликвидность по типу объекта 
    liquidity_by_category = { 
        "apartment": 20, 
        "house": 14, 
        "commercial_room": 10, 
        "commercial_building": 8, 
        "commercial_building_with_land": 8, 
        "land": 7, 
        "parking": 9, 
        "unfinished": 3, 
        "receivable": 2, 
        "other": 3, 
    } 
    score += liquidity_by_category.get(category, 4) 
 
    # Штраф за низкую уверенность оценки 
    if confidence == "low": 
        score -= 10 
    elif confidence == "medium": 
        score -= 4 
 
    text_legal = (legal_status or "").lower() 
    text_addr = (address or "").lower() 
 
    # ОКН / наследие 
    if "культурн" in text_legal or "наслед" in text_legal: 
        score -= 20 
 
    # Плохой адрес 
    if not address or len(address) < 15: 
        score -= 8 
 
    # Нет площади 
    if not area: 
        score -= 8 
 
    # Сельская коммерция 
    if category in {"commercial_building", "commercial_building_with_land", "commercial_room"}: 
        if any(x in text_addr for x in [" д ", " дерев", " с ", " село", "посел", "р-н", "район"]): 
            score -= 8 
 
    return round(max(0, min(score, 100)), 2) 

def needs_human_review(confidence: str) -> bool:
    return confidence.lower() in ("низкая", "low")

# --- Core Logic ---

def delete_lot(session: Session, lot_id: int) -> bool:
    processed = session.get(ProcessedLot, lot_id)
    if not processed: return False
    try:
        session.execute(
            update(ProcessedLot)
            .where(ProcessedLot.duplicate_of_id == lot_id)
            .values(duplicate_of_id=None)
        )
        session.delete(processed)
        session.flush()
        logger.info("Manually deleted lot %s with database-enforced cascades", lot_id)
        return True
    except Exception:
        logger.exception("Failed to manually delete lot %s", lot_id)
        raise

def delete_lots_batch(session: Session, lot_ids: list[int]) -> int:
    count = 0
    for lid in lot_ids:
        if delete_lot(session, lid): count += 1
    return count

def cleanup_closed_lots(session: Session) -> int:
    """Archive closed lots without deleting their analytical or workflow history."""
    closed_lots = session.scalars(
        select(ProcessedLot).where(
            ProcessedLot.auction_status == "closed",
            ProcessedLot.is_archived.is_(False),
        )
    ).all()
    now = utc_now()
    for lot in closed_lots:
        lot.is_archived = True
        lot.archived_at = lot.archived_at or now
        lot.closed_at = lot.closed_at or now
    session.flush()
    if closed_lots:
        logger.info("Archived %s closed lots without deleting related data", len(closed_lots))
    return len(closed_lots)


def apply_lot_status(session: Session, lot: ProcessedLot, new_status: str, source: str) -> bool:
    normalized = normalize_status(new_status)
    old_status = lot.auction_status
    if not normalized or normalized == old_status:
        return False
    lot.auction_status = normalized
    if normalized == "closed":
        lot.closed_at = lot.closed_at or utc_now()
        lot.is_archived = True
        lot.archived_at = lot.archived_at or utc_now()
    elif old_status == "closed":
        lot.is_archived = False
        lot.archived_at = None
    session.add(LotStatusHistory(
        lot_id=lot.id,
        old_status=old_status,
        new_status=normalized,
        source=source or "sync",
    ))
    return True

def persist_lot(session: Session, normalized: NormalizedLot) -> ProcessedLot:
    processed = session.scalar(select(ProcessedLot).where(ProcessedLot.external_id == normalized.external_id))
    if processed is None:
        processed = ProcessedLot(
            external_id=normalized.external_id,
            source=normalized.source,
            source_system=normalized.source_system,
            title=normalized.title,
            description=normalized.description,
            category=normalized.category,
            region_slug=normalized.region_slug,
            region_name=normalized.region_name,
            address=normalized.address,
            cadastral_number=normalized.cadastral_number,
            cadastral_numbers=normalized.raw_data.get("cadastral_numbers"),
            vin=normalized.vin,
            start_price=_to_decimal(normalized.start_price),
            current_price=_to_decimal(normalized.current_price),
            auction_status=normalized.auction_status,
            lot_url=normalized.lot_url,
            source_url=normalized.source_url,
            area=normalized.area,
            published_at=normalized.published_at,
            # Новые инвестиционные поля
            object_name=normalized.object_name,
            property_type=normalized.property_type,
            total_area_gba=normalized.total_area_gba,
            gla=normalized.gla,
            land_area=normalized.land_area,
            floors=normalized.floors,
            year_built=normalized.year_built,
            occupancy_rate=normalized.occupancy_rate,
            anchor_tenants=normalized.anchor_tenants,
            monthly_fixed_rent=normalized.monthly_fixed_rent,
            monthly_variable_rent=normalized.monthly_variable_rent,
            monthly_other_income=normalized.monthly_other_income,
            monthly_opex=normalized.monthly_opex,
            noi_annual=normalized.noi_annual,
            legal_status=normalized.legal_status,
            encumbrances=normalized.encumbrances,
            land_risk_flag=normalized.land_risk_flag,
            technical_condition=normalized.technical_condition,
            power_kw=normalized.power_kw,
            parking_spaces=normalized.parking_spaces,
        )
        session.add(processed)
        session.flush()
        if processed.auction_status:
            session.add(LotStatusHistory(
                lot_id=processed.id,
                old_status=None,
                new_status=processed.auction_status,
                source=normalized.source or "import",
            ))
    else:
        # Обновляем существующий лот, если он еще не прошел ручную проверку
        if processed.review_status is None:
            processed.title = normalized.title
            processed.description = normalized.description
            processed.category = normalized.category
            processed.address = normalized.address or processed.address
            processed.cadastral_number = normalized.cadastral_number or processed.cadastral_number
            if normalized.raw_data.get("cadastral_numbers"):
                processed.cadastral_numbers = normalized.raw_data["cadastral_numbers"]
            processed.area = normalized.area if normalized.area is not None else processed.area
            processed.total_area_gba = normalized.total_area_gba if normalized.total_area_gba is not None else processed.total_area_gba
            processed.gla = normalized.gla if normalized.gla is not None else processed.gla
            processed.land_area = normalized.land_area if normalized.land_area is not None else processed.land_area
            processed.floors = normalized.floors if normalized.floors is not None else processed.floors
            processed.year_built = normalized.year_built or processed.year_built
            processed.occupancy_rate = normalized.occupancy_rate if normalized.occupancy_rate is not None else processed.occupancy_rate
            processed.anchor_tenants = normalized.anchor_tenants or processed.anchor_tenants
            processed.monthly_fixed_rent = normalized.monthly_fixed_rent if normalized.monthly_fixed_rent is not None else processed.monthly_fixed_rent
            processed.monthly_variable_rent = normalized.monthly_variable_rent if normalized.monthly_variable_rent is not None else processed.monthly_variable_rent
            processed.monthly_other_income = normalized.monthly_other_income if normalized.monthly_other_income is not None else processed.monthly_other_income
            processed.monthly_opex = normalized.monthly_opex if normalized.monthly_opex is not None else processed.monthly_opex
            processed.noi_annual = normalized.noi_annual if normalized.noi_annual is not None else processed.noi_annual
            processed.legal_status = normalized.legal_status or processed.legal_status
            processed.encumbrances = normalized.encumbrances or processed.encumbrances
            processed.land_risk_flag = normalized.land_risk_flag if normalized.land_risk_flag is not None else processed.land_risk_flag
            processed.technical_condition = normalized.technical_condition or processed.technical_condition
            processed.power_kw = normalized.power_kw if normalized.power_kw is not None else processed.power_kw
            processed.parking_spaces = normalized.parking_spaces if normalized.parking_spaces is not None else processed.parking_spaces
            processed.object_name = normalized.object_name or processed.object_name
            processed.property_type = normalized.property_type or processed.property_type
            processed.vin = normalized.vin or processed.vin
        
        # Всегда обновляем цену и статус
        processed.current_price = _to_decimal(normalized.current_price)
        new_status = (normalized.auction_status or "").strip()
        if new_status and not (new_status == "unknown" and processed.auction_status not in {None, "", "unknown"}):
            apply_lot_status(session, processed, new_status, normalized.source or "sync")
        processed.last_update = utc_now()
        
        logger.info(f"Updated lot {normalized.external_id}")
                
    session.flush()
    return processed

def upsert_lot_events_from_raw(session: Session, processed: ProcessedLot, raw_payload: dict) -> None:
    if not session.query(LotGeoSnapshot).filter_by(lot_id=processed.id).first():
        enrich_lot_geo(session, processed)
    status = build_status_decision(raw_payload)
    final_status = status["final_status"]
    if final_status == "unknown" and processed.auction_status not in {None, "", "unknown"}:
        final_status = processed.auction_status
        status["trace_reason"] = f"{status['trace_reason']} | Existing normalized status was preserved."
    else:
        apply_lot_status(session, processed, final_status, processed.source or "sync")
    session.add(LotStatusEvent(
        lot_id=processed.id,
        source=processed.source,
        source_status=str(raw_payload.get("status", "unknown")),
        normalized_status=final_status,
        status_confidence=status["confidence"],
        trace_reason=status["trace_reason"]
    ))

# --- Response Builders ---

def build_lots_response(
    session: Session,
    city_slug: str,
    page: int = 1,
    per_page: int = 12,
    search: str = "",
    categories: list[str] | None = None,
    statuses: list[str] | None = None,
    min_price: float = 0,
    max_price: float = 1e10,
    min_discount: float = 0,
    max_discount: float = 100,
    min_risk: int = 0,
    max_risk: int = 10,
    sort_mode: str = "recommended"
) -> dict:
    """Формирует структурированный ответ со списком лотов для API."""
    # Базовый запрос с фильтрацией по региону 
    region_values = get_region_query_values(city_slug)
    query = select(ProcessedLot).where(ProcessedLot.region_slug.in_(region_values)) 

    # Поиск по названию 
    if search: 
        query = query.where(ProcessedLot.title.ilike(f"%{search}%")) 

    # Фильтр по категориям 
    if categories: 
        query = query.where(ProcessedLot.category.in_(categories)) 

    # Фильтр по статусам 
    if statuses: 
        query = query.where(ProcessedLot.auction_status.in_(statuses)) 

    # Ценовой диапазон 
    query = query.where(ProcessedLot.current_price.between(min_price, max_price)) 

    # Дисконт (может быть None) 
    if min_discount > 0: 
        query = query.where(ProcessedLot.discount_percent >= min_discount) 
    if max_discount < 100: 
        query = query.where(ProcessedLot.discount_percent <= max_discount) 

    # Риск 
    query = query.where(ProcessedLot.risk_score.between(min_risk, max_risk)) 

    # Сортировка 
    sort_mapping = { 
        "recommended": desc(ProcessedLot.rating), 
        "price_asc": asc(ProcessedLot.current_price), 
        "price_desc": desc(ProcessedLot.current_price), 
        "discount": desc(ProcessedLot.discount_percent), 
        "newest": desc(ProcessedLot.last_update) 
    } 
    order = sort_mapping.get(sort_mode, desc(ProcessedLot.rating)) 
    query = query.order_by(order) 

    # Подсчёт общего количества 
    total_query = select(func.count()).select_from(query.subquery()) 
    total = session.scalar(total_query) 

    # Пагинация 
    offset = (page - 1) * per_page 
    query = query.offset(offset).limit(per_page) 
    lots = session.scalars(query).all() 

    # Формирование ответа 
    items = [] 
    for lot in lots: 
        items.append({ 
            "id": lot.id, 
            "external_id": lot.external_id, 
            "title": lot.title, 
            "description": (lot.description[:200] + "...") if lot.description and len(lot.description) > 200 else lot.description, 
            "category": lot.category, 
            "region_slug": lot.region_slug, 
            "address": lot.address, 
            "current_price": float(lot.current_price) if lot.current_price else None, 
            "market_price": float(lot.market_price) if lot.market_price else None, 
            "discount_percent": lot.discount_percent, 
            "risk_score": lot.risk_score, 
            "rating": lot.rating, 
            "auction_status": lot.auction_status, 
            "lot_url": lot.lot_url, 
            "building_area": lot.total_area_gba, 
            "land_area": lot.land_area, 
            "land_area_sotki": round(lot.land_area / 100, 2) if lot.land_area else None, 
            "area": lot.area, 
            "last_update": lot.last_update.isoformat() if lot.last_update else None, 
            "needs_human_review": lot.needs_human_review, 
        }) 
    return {"items": items, "total": total}

def get_lot_response(session: Session, city_slug: str, lot_id: int) -> dict | None:
    region_values = get_region_query_values(city_slug)
    lot = session.scalar(
        select(ProcessedLot)
        .where(ProcessedLot.id == lot_id)
        .where(ProcessedLot.region_slug.in_(region_values))
    )
    if not lot:
        return None

    geo = session.scalar(
        select(LotGeoSnapshot)
        .where(LotGeoSnapshot.lot_id == lot.id)
        .order_by(desc(LotGeoSnapshot.observed_at), desc(LotGeoSnapshot.id))
    )
    return {
        "id": lot.id,
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
        "cadastral_numbers": lot.cadastral_numbers,
        "vin": lot.vin,
        "current_price": float(lot.current_price) if lot.current_price else None,
        "start_price": float(lot.start_price) if lot.start_price else None,
        "market_price": float(lot.market_price) if lot.market_price else None,
        "market_price_min": float(lot.market_price_min) if lot.market_price_min else None,
        "market_price_max": float(lot.market_price_max) if lot.market_price_max else None,
        "discount_percent": lot.discount_percent,
        "risk_score": lot.risk_score,
        "rating": lot.rating,
        "auction_status": lot.auction_status,
        "ai_recommendation": lot.ai_recommendation,
        "links_to_analogs": lot.links_to_analogs or [],
        "lot_url": lot.lot_url,
        "source_url": lot.source_url,
        "area": lot.area,
        "building_area": lot.total_area_gba,
        "land_area": lot.land_area,
        "floors": lot.floors,
        "year_built": lot.year_built,
        "legal_status": lot.legal_status,
        "encumbrances": lot.encumbrances,
        "technical_condition": lot.technical_condition,
        "needs_human_review": lot.needs_human_review,
        "review_status": lot.review_status,
        "last_update": lot.last_update.isoformat() if lot.last_update else None,
        "published_at": lot.published_at.isoformat() if lot.published_at else None,
        "geo": None if not geo else {
            "source": geo.geo_source,
            "method": geo.geo_method,
            "confidence": geo.geo_confidence,
            "centroid_lat": geo.centroid_lat,
            "centroid_lon": geo.centroid_lon,
            "geometry_json": geo.geometry_json,
            "trace_reason": geo.trace_reason,
            "metadata_json": geo.metadata_json,
            "observed_at": geo.observed_at.isoformat() if geo.observed_at else None,
        },
    }

def build_stats_response(session: Session, city_slug: str) -> dict:
    """Формирует статистику по региону для API."""
    region_values = get_region_query_values(city_slug)
    base = select(ProcessedLot).where(ProcessedLot.region_slug.in_(region_values)) 
    total = session.scalar(select(func.count()).select_from(base.subquery())) 
    active = session.scalar(select(func.count()).select_from(base.where(ProcessedLot.auction_status == "active").subquery())) 
    with_rating = session.scalar(select(func.count()).select_from(base.where(ProcessedLot.rating.isnot(None)).subquery())) 

    avg_discount = session.scalar(select(func.avg(ProcessedLot.discount_percent)) 
                                  .where(ProcessedLot.region_slug.in_(region_values)) 
                                  .where(ProcessedLot.discount_percent.isnot(None))) 

    return { 
        "total_lots": total, 
        "active_lots": active, 
        "appraised_lots": with_rating, 
        "average_discount": round(float(avg_discount), 2) if avg_discount else None, 
        "region": city_slug 
    }

def get_sync_metrics(session: Session) -> dict:
    from bankrotai.db import RegionSyncState
    return {
        "status": "ok",
        "regions": session.scalar(select(func.count()).select_from(RegionSyncState)) or 0,
    }

def db_get_saved_searches(session: Session, user_id: str) -> list:
    from bankrotai.db import SavedSearch
    rows = session.scalars(
        select(SavedSearch)
        .where(SavedSearch.user_id == user_id)
        .order_by(desc(SavedSearch.created_at))
    ).all()
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "name": row.name,
            "query_params": row.query_params,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]

def get_watchlist_lots(session: Session, watchlist_id: int) -> list:
    from bankrotai.db import WatchlistLot
    rows = session.scalars(
        select(ProcessedLot)
        .join(WatchlistLot, WatchlistLot.lot_id == ProcessedLot.id)
        .where(WatchlistLot.watchlist_id == watchlist_id)
        .order_by(desc(WatchlistLot.added_at))
    ).all()
    return [
        {
            "id": lot.id,
            "title": lot.title,
            "current_price": float(lot.current_price) if lot.current_price else None,
            "rating": lot.rating,
            "auction_status": lot.auction_status,
            "lot_url": lot.lot_url,
        }
        for lot in rows
    ]

def get_or_create_workflow_state(session: Session, lot_id: int, user_id: str) -> Any:
    from bankrotai.db import LotWorkflowState
    state = session.scalar(select(LotWorkflowState).where(LotWorkflowState.lot_id == lot_id, LotWorkflowState.user_id == user_id))
    if not state:
        state = LotWorkflowState(lot_id=lot_id, user_id=user_id)
        session.add(state)
    return state

def get_lot_event_subscriptions(session: Session, lot_id: int, user_id: str) -> list:
    from bankrotai.db import LotEventSubscription
    rows = session.scalars(
        select(LotEventSubscription)
        .where(LotEventSubscription.lot_id == lot_id, LotEventSubscription.user_id == user_id)
        .order_by(desc(LotEventSubscription.created_at))
    ).all()
    return [
        {
            "id": row.id,
            "lot_id": row.lot_id,
            "channel": row.channel,
            "target_id": row.target_id,
            "event_types": row.event_types,
            "is_active": row.is_active,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]

def get_admin_dashboard_metrics(session: Session) -> dict:
    from bankrotai.db import AuditLog, SavedSearch, Subscription, WatchlistLot, WorkflowTask
    return {
        "lots": session.scalar(select(func.count()).select_from(ProcessedLot)) or 0,
        "active_lots": session.scalar(select(func.count()).where(ProcessedLot.auction_status == "active")) or 0,
        "saved_searches": session.scalar(select(func.count()).select_from(SavedSearch)) or 0,
        "subscriptions": session.scalar(select(func.count()).select_from(Subscription)) or 0,
        "watchlist_lots": session.scalar(select(func.count()).select_from(WatchlistLot)) or 0,
        "open_tasks": session.scalar(select(func.count()).where(WorkflowTask.task_status == "open")) or 0,
        "audit_events": session.scalar(select(func.count()).select_from(AuditLog)) or 0,
    }

def log_action(session: Session, user_id: str, action: str, entity_type: str, entity_id: str | None, metadata: dict | None):
    from bankrotai.db import AuditLog
    log = AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=entity_id, metadata_json=metadata)
    session.add(log)
