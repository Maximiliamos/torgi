from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import String, asc, cast, desc, func, or_, select, update
from sqlalchemy.orm import Session

from bankrotai.domain import NormalizedLot
from bankrotai.db import (
    LotGeoSnapshot,
    LotStatusHistory,
    LotStatusEvent,
    CanonicalLot,
    ProcessedLot,
    SourceLot,
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


def _raw_value(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    cleaned = str(value).replace("\xa0", " ").replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _canonical_key(normalized: NormalizedLot) -> str:
    cadastral = (normalized.cadastral_number or "").replace(" ", "")
    if cadastral:
        return f"cadastral:{cadastral}"
    if normalized.efresb_message_number:
        return f"efrsb:{normalized.efresb_message_number.strip()}"
    if normalized.bankruptcy_case_number and normalized.procedure_number:
        return f"case:{normalized.bankruptcy_case_number.strip()}:{normalized.procedure_number.strip()}"
    return f"source:{normalized.source_system}:{normalized.external_id}"


def _clean_cadastral(value: Any) -> str | None:
    text = re.sub(r"\s+", "", str(value or ""))
    return text if re.fullmatch(r"\d{1,2}:\d{1,2}:\d{4,10}:\d+", text) else None


def _normalized_lot_cadastral_numbers(normalized: NormalizedLot) -> set[str]:
    raw = normalized.raw_data if isinstance(normalized.raw_data, dict) else {}
    values: list[Any] = [normalized.cadastral_number]
    extra = raw.get("cadastral_numbers")
    if isinstance(extra, (list, tuple, set)):
        values.extend(extra)
    elif extra:
        values.extend(re.findall(r"\d{1,2}\s*:\s*\d{1,2}\s*:\s*\d{4,10}\s*:\s*\d+", str(extra)))
    return {cleaned for value in values if (cleaned := _clean_cadastral(value))}


def _processed_lot_cadastral_numbers(lot: ProcessedLot) -> set[str]:
    values: list[Any] = [lot.cadastral_number]
    if isinstance(lot.cadastral_numbers, list):
        values.extend(lot.cadastral_numbers)
    elif lot.cadastral_numbers:
        values.extend(re.findall(r"\d{1,2}\s*:\s*\d{1,2}\s*:\s*\d{4,10}\s*:\s*\d+", str(lot.cadastral_numbers)))
    return {cleaned for value in values if (cleaned := _clean_cadastral(value))}


def _identity_words(value: str | None) -> set[str]:
    words = re.findall(r"[0-9a-zа-яё]+", (value or "").casefold())
    ignored = {"российская", "федерация", "область", "район", "имущества", "имущество", "расположенное", "адресу"}
    return {word for word in words if len(word) > 1 and word not in ignored}


def _text_containment(left: str | None, right: str | None) -> float:
    left_words = _identity_words(left)
    right_words = _identity_words(right)
    if not left_words or not right_words:
        return 0.0
    return len(left_words & right_words) / min(len(left_words), len(right_words))


def _prices_match(left: Any, right: Any) -> bool:
    if left in (None, "") or right in (None, ""):
        return False
    left_value, right_value = float(left), float(right)
    tolerance = max(1.0, min(abs(left_value), abs(right_value)) * 0.001)
    return abs(left_value - right_value) <= tolerance


def _titles_identify_same_lot(left: str | None, right: str | None) -> bool:
    left_words = _identity_words(left)
    right_words = _identity_words(right)
    if min(len(left_words), len(right_words)) < 3:
        return False
    score = SequenceMatcher(None, (left or "").casefold(), (right or "").casefold()).ratio()
    return score >= 0.92


def _same_cross_source_lot(existing: ProcessedLot, normalized: NormalizedLot) -> bool:
    if (existing.source_system or "").casefold() == (normalized.source_system or "").casefold():
        return False
    shared_cadastral = _processed_lot_cadastral_numbers(existing) & _normalized_lot_cadastral_numbers(normalized)
    if shared_cadastral:
        if existing.current_price is not None and normalized.current_price is not None:
            left, right = float(existing.current_price), float(normalized.current_price)
            if max(abs(left), abs(right), 1.0) and abs(left - right) / max(abs(left), abs(right), 1.0) > 0.05:
                return False
        return True
    if not _prices_match(existing.current_price or existing.start_price, normalized.current_price or normalized.start_price):
        return False
    if _text_containment(existing.address, normalized.address) >= 0.72:
        return True
    return _titles_identify_same_lot(existing.title, normalized.title) and bool(
        existing.region_slug and existing.region_slug == normalized.region_slug
    )


def _find_cross_source_processed_lot(session: Session, normalized: NormalizedLot) -> ProcessedLot | None:
    candidates: list[ProcessedLot] = []
    cadastral_numbers = _normalized_lot_cadastral_numbers(normalized)
    if cadastral_numbers:
        candidates.extend(session.scalars(select(ProcessedLot).where(
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.source_system != normalized.source_system,
            ProcessedLot.cadastral_number.in_(cadastral_numbers),
        )).all())
    price = normalized.current_price or normalized.start_price
    if price is not None:
        tolerance = max(1.0, abs(float(price)) * 0.001)
        candidates.extend(session.scalars(select(ProcessedLot).where(
            ProcessedLot.duplicate_of_id.is_(None),
            ProcessedLot.source_system != normalized.source_system,
            or_(
                ProcessedLot.current_price.between(float(price) - tolerance, float(price) + tolerance),
                ProcessedLot.start_price.between(float(price) - tolerance, float(price) + tolerance),
            ),
        )).all())
    seen: set[int] = set()
    for candidate in candidates:
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        if _same_cross_source_lot(candidate, normalized):
            return candidate
    return None


def _sync_source_lot(
    session: Session,
    processed: ProcessedLot,
    normalized: NormalizedLot,
    canonical_hint: CanonicalLot | None = None,
) -> SourceLot:
    raw = normalized.raw_data or {}
    preferred_key = _canonical_key(normalized)
    source_lot = session.scalar(
        select(SourceLot).where(
            SourceLot.source_system == normalized.source_system,
            SourceLot.external_id == normalized.external_id,
        )
    )
    canonical = canonical_hint or session.scalar(
        select(CanonicalLot).where(CanonicalLot.canonical_key == preferred_key)
    )
    if canonical is None:
        existing_link = session.scalar(
            select(SourceLot).where(SourceLot.processed_lot_id == processed.id).order_by(SourceLot.id)
        )
        if existing_link is not None:
            canonical = session.get(CanonicalLot, existing_link.canonical_lot_id)
    if canonical is None:
        canonical = CanonicalLot(
            canonical_key=preferred_key,
            legacy_processed_lot_id=processed.id if source_lot is None else None,
            title=normalized.title,
            category=normalized.category,
            address=normalized.address,
            cadastral_number=normalized.cadastral_number,
            area=normalized.area,
        )
        session.add(canonical)
        session.flush()
    else:
        canonical.title = normalized.title or canonical.title
        canonical.address = normalized.address or canonical.address
        canonical.cadastral_number = normalized.cadastral_number or canonical.cadastral_number
        canonical.area = normalized.area if normalized.area is not None else canonical.area

    if source_lot is None:
        source_lot = SourceLot(
            canonical_lot_id=canonical.id,
            processed_lot_id=processed.id,
            source_system=normalized.source_system,
            external_id=normalized.external_id,
        )
        session.add(source_lot)
    elif source_lot.canonical_lot_id != canonical.id and preferred_key.startswith(("cadastral:", "efrsb:", "case:")):
        source_lot.canonical_lot_id = canonical.id

    source_lot.source_url = normalized.source_url or normalized.lot_url
    source_lot.platform_name = normalized.platform_name or _raw_value(raw, "etp", "platform_name", "trade_place")
    source_lot.platform_code = normalized.platform_code or _raw_value(raw, "etp_code", "platform_code")
    source_lot.procedure_number = normalized.procedure_number or _raw_value(raw, "procedure_number", "trade_number")
    source_lot.notice_number = normalized.notice_number or _raw_value(raw, "notice_number", "noticeNumber")
    source_lot.efresb_message_number = normalized.efresb_message_number or _raw_value(
        raw, "efresb_message_number", "fedresurs_message_number"
    )
    source_lot.debtor_name = normalized.debtor_name or _raw_value(raw, "debtor", "debtor_name")
    source_lot.organizer_name = normalized.organizer_name or _raw_value(raw, "organizer", "organizer_name")
    source_lot.auction_manager_name = normalized.auction_manager_name or _raw_value(
        raw, "auction_manager", "arbitration_manager"
    )
    source_lot.bankruptcy_case_number = normalized.bankruptcy_case_number or _raw_value(
        raw, "bankruptcy_case_number", "case_number"
    )
    source_lot.deposit_amount = _to_decimal(
        normalized.deposit_amount if normalized.deposit_amount is not None else _to_float(_raw_value(raw, "deposit", "deposit_amount"))
    )
    source_lot.deposit_percent = normalized.deposit_percent or _to_float(_raw_value(raw, "deposit_percent"))
    source_lot.deposit_payment_details = normalized.deposit_payment_details or _raw_value(
        raw, "deposit_payment_details", "deposit_requisites"
    )
    source_lot.deposit_deadline = normalized.deposit_deadline or _to_datetime(_raw_value(raw, "deposit_deadline"))
    source_lot.application_deadline = normalized.application_deadline or _to_datetime(
        _raw_value(raw, "bidd_end_time", "application_deadline")
    )
    source_lot.auction_at = normalized.auction_at or _to_datetime(
        _raw_value(raw, "auction_start_date", "auction_at")
    )
    source_lot.auction_step_amount = _to_decimal(
        normalized.auction_step_amount
        if normalized.auction_step_amount is not None
        else _to_float(_raw_value(raw, "auction_step", "auction_step_amount"))
    )
    source_lot.auction_step_percent = normalized.auction_step_percent or _to_float(
        _raw_value(raw, "auction_step_percent")
    )
    source_lot.auction_type = normalized.auction_type or _raw_value(raw, "trade_type", "auction_type")
    source_lot.public_offer_schedule = normalized.public_offer_schedule or _raw_value(raw, "public_offer_schedule")
    source_lot.next_interval_price = _to_decimal(
        normalized.next_interval_price
        if normalized.next_interval_price is not None
        else _to_float(_raw_value(raw, "next_interval_price"))
    )
    source_lot.next_price_reduction_at = normalized.next_price_reduction_at or _to_datetime(
        _raw_value(raw, "next_price_reduction_at")
    )
    source_lot.document_completeness = normalized.document_completeness or _raw_value(raw, "document_completeness")
    source_lot.inspection_procedure = normalized.inspection_procedure or _raw_value(raw, "inspection_procedure")
    source_lot.organizer_contact = normalized.organizer_contact or _raw_value(raw, "organizer_contact")
    source_lot.raw_data = raw
    source_lot.last_seen_at = utc_now()
    session.flush()
    return source_lot


def _ensure_processed_source_lot(session: Session, processed: ProcessedLot) -> SourceLot:
    existing = session.scalar(select(SourceLot).where(
        SourceLot.source_system == processed.source_system,
        SourceLot.external_id == processed.external_id,
    ))
    if existing is not None:
        if existing.processed_lot_id != processed.id:
            existing.processed_lot_id = processed.id
        return existing

    canonical_key = (
        f"cadastral:{_clean_cadastral(processed.cadastral_number)}"
        if _clean_cadastral(processed.cadastral_number)
        else f"legacy:{processed.id}"
    )
    canonical = session.scalar(select(CanonicalLot).where(CanonicalLot.canonical_key == canonical_key))
    if canonical is None:
        canonical = CanonicalLot(
            canonical_key=canonical_key,
            legacy_processed_lot_id=processed.id,
            title=processed.title,
            category=processed.category,
            address=processed.address,
            cadastral_number=processed.cadastral_number,
            area=processed.area,
        )
        session.add(canonical)
        session.flush()
    existing = SourceLot(
        canonical_lot_id=canonical.id,
        processed_lot_id=processed.id,
        source_system=processed.source_system,
        external_id=processed.external_id,
        source_url=processed.source_url or processed.lot_url,
        last_seen_at=utc_now(),
    )
    session.add(existing)
    session.flush()
    return existing

# --- Pipelines (Geo & Status) ---

def build_geo_decision(city_slug: str, raw_payload: dict[str, Any], fallback_text: str = "") -> dict:
    address = raw_payload.get("address") or raw_payload.get("location") or fallback_text
    
    return {
        "geo_source": "fallback",
        "geo_method": "unresolved",
        "geo_confidence": "none",
        "centroid_lat": None,
        "centroid_lon": None,
        "needs_geo_check": True,
        "trace_reason": "Coordinates were not resolved; no synthetic fallback was assigned",
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
    source_link = session.scalar(select(SourceLot).where(
        SourceLot.source_system == normalized.source_system,
        SourceLot.external_id == normalized.external_id,
    ))
    processed = session.get(ProcessedLot, source_link.processed_lot_id) if source_link else None
    if processed is None:
        processed = session.scalar(
            select(ProcessedLot).where(
                ProcessedLot.source_system == normalized.source_system,
                ProcessedLot.external_id == normalized.external_id,
            )
        )
    duplicate_primary = _find_cross_source_processed_lot(session, normalized) if processed is None else None
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
            duplicate_of_id=duplicate_primary.id if duplicate_primary is not None else None,
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
        
        processed.current_price = _to_decimal(normalized.current_price)
        new_status = (normalized.auction_status or "").strip()
        if new_status and not (new_status == "unknown" and processed.auction_status not in {None, "", "unknown"}):
            apply_lot_status(session, processed, new_status, normalized.source or "sync")
        processed.last_update = utc_now()
        
        logger.info(f"Updated lot {normalized.external_id}")
                
    session.flush()
    canonical_hint = None
    if duplicate_primary is not None:
        primary_link = _ensure_processed_source_lot(session, duplicate_primary)
        canonical_hint = session.get(CanonicalLot, primary_link.canonical_lot_id)
    _sync_source_lot(session, processed, normalized, canonical_hint=canonical_hint)
    return duplicate_primary or processed


def _same_processed_cross_source_lot(left: ProcessedLot, right: ProcessedLot) -> bool:
    if (left.source_system or "").casefold() == (right.source_system or "").casefold():
        return False
    shared_cadastral = _processed_lot_cadastral_numbers(left) & _processed_lot_cadastral_numbers(right)
    if shared_cadastral:
        if left.current_price is not None and right.current_price is not None:
            left_price, right_price = float(left.current_price), float(right.current_price)
            if abs(left_price - right_price) / max(abs(left_price), abs(right_price), 1.0) > 0.05:
                return False
        return True
    if not _prices_match(left.current_price or left.start_price, right.current_price or right.start_price):
        return False
    if _text_containment(left.address, right.address) >= 0.72:
        return True
    return _titles_identify_same_lot(left.title, right.title) and bool(
        left.region_slug and left.region_slug == right.region_slug
    )


def reconcile_cross_source_duplicates(session: Session) -> int:
    """Mark existing cross-source copies and link them to one canonical card."""
    lots = session.scalars(
        select(ProcessedLot)
        .where(ProcessedLot.duplicate_of_id.is_(None))
        .order_by(ProcessedLot.id)
    ).all()
    by_cadastral: dict[str, list[ProcessedLot]] = {}
    by_price: dict[int, list[ProcessedLot]] = {}
    reconciled = 0

    for lot in lots:
        candidates: list[ProcessedLot] = []
        for cadastral in _processed_lot_cadastral_numbers(lot):
            candidates.extend(by_cadastral.get(cadastral, []))
        price = lot.current_price or lot.start_price
        if price is not None:
            candidates.extend(by_price.get(int(round(float(price))), []))

        primary = next(
            (
                candidate
                for candidate in dict.fromkeys(candidates)
                if _same_processed_cross_source_lot(candidate, lot)
            ),
            None,
        )
        if primary is not None:
            lot.duplicate_of_id = primary.id
            primary.address = primary.address or lot.address
            primary.cadastral_number = primary.cadastral_number or lot.cadastral_number
            primary.cadastral_numbers = primary.cadastral_numbers or lot.cadastral_numbers
            primary.region_slug = primary.region_slug or lot.region_slug
            primary.region_name = primary.region_name or lot.region_name
            primary.area = primary.area if primary.area is not None else lot.area
            primary.review_status = primary.review_status or lot.review_status
            primary_link = _ensure_processed_source_lot(session, primary)
            duplicate_link = _ensure_processed_source_lot(session, lot)
            duplicate_link.canonical_lot_id = primary_link.canonical_lot_id
            reconciled += 1
            continue

        for cadastral in _processed_lot_cadastral_numbers(lot):
            by_cadastral.setdefault(cadastral, []).append(lot)
        if price is not None:
            by_price.setdefault(int(round(float(price))), []).append(lot)

    session.flush()
    return reconciled

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
    min_risk: int | None = None,
    max_risk: int | None = None,
    sort_mode: str = "recommended"
) -> dict:
    """Формирует структурированный ответ со списком лотов для API."""
    # Базовый запрос с фильтрацией по региону 
    region_values = get_region_query_values(city_slug)
    query = select(ProcessedLot).where(ProcessedLot.region_slug.in_(region_values)) 

    # Поиск по названию 
    search_term = search.strip()
    if search_term:
        columns = (
            ProcessedLot.title,
            ProcessedLot.description,
            ProcessedLot.address,
            ProcessedLot.cadastral_number,
            cast(ProcessedLot.cadastral_numbers, String),
            ProcessedLot.external_id,
            ProcessedLot.object_name,
        )
        if session.get_bind().dialect.name == "sqlite":
            pattern = f"%{search_term.casefold()}%"
            query = query.where(or_(*(func.unicode_casefold(column).like(pattern) for column in columns)))
        else:
            pattern = f"%{search_term}%"
            query = query.where(or_(*(column.ilike(pattern) for column in columns)))

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
    if min_risk is not None or max_risk is not None:
        lower = 0 if min_risk is None else min_risk
        upper = 10 if max_risk is None else max_risk
        query = query.where(ProcessedLot.risk_score.between(lower, upper))

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
