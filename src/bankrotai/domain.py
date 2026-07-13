from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bankrotai.db import ProcessedLot


@dataclass(slots=True)
class NormalizedLot:
    external_id: str
    source: str
    source_system: str
    title: str
    description: str
    category: str
    region_slug: str | None
    region_name: str | None
    address: str | None
    cadastral_number: str | None
    vin: str | None
    area: float | None
    start_price: float | None
    current_price: float | None
    auction_status: str
    lot_url: str | None
    source_url: str | None
    detail_level: str
    raw_data: dict
    published_at: datetime | None = None
    
    # Новые поля для инвестиционной оценки
    object_name: str | None = None
    property_type: str | None = None
    total_area_gba: float | None = None
    gla: float | None = None
    land_area: float | None = None
    floors: int | None = None
    year_built: int | None = None
    occupancy_rate: float | None = None
    anchor_tenants: str | None = None
    monthly_fixed_rent: float | None = None
    monthly_variable_rent: float | None = None
    monthly_other_income: float | None = None
    monthly_opex: float | None = None
    noi_annual: float | None = None
    legal_status: str | None = None
    encumbrances: str | None = None
    land_risk_flag: bool = False
    technical_condition: str | None = None
    power_kw: float | None = None
    parking_spaces: int | None = None

    @classmethod
    def from_processed_lot(cls, lot: 'ProcessedLot') -> 'NormalizedLot':
        """Создаёт NormalizedLot из объекта БД ProcessedLot."""
        return cls(
            external_id=lot.external_id,
            source=lot.source,
            source_system=lot.source_system,
            title=lot.title,
            description=lot.description,
            category=lot.category,
            region_slug=lot.region_slug,
            region_name=lot.region_name,
            address=lot.address,
            cadastral_number=lot.cadastral_number,
            vin=lot.vin,
            area=lot.area,
            start_price=float(lot.start_price or 0),
            current_price=float(lot.current_price or 0),
            auction_status=lot.auction_status,
            lot_url=lot.lot_url,
            source_url=lot.source_url,
            detail_level=lot.detail_level,
            raw_data={},
            published_at=lot.published_at,
            object_name=lot.object_name,
            property_type=lot.property_type,
            total_area_gba=lot.total_area_gba,
            gla=lot.gla,
            land_area=lot.land_area,
            floors=lot.floors,
            year_built=lot.year_built,
            occupancy_rate=lot.occupancy_rate,
            anchor_tenants=lot.anchor_tenants,
            monthly_fixed_rent=lot.monthly_fixed_rent,
            monthly_variable_rent=lot.monthly_variable_rent,
            monthly_other_income=lot.monthly_other_income,
            monthly_opex=lot.monthly_opex,
            noi_annual=lot.noi_annual,
            legal_status=lot.legal_status,
            encumbrances=lot.encumbrances,
            land_risk_flag=lot.land_risk_flag,
            technical_condition=lot.technical_condition,
            power_kw=lot.power_kw,
            parking_spaces=lot.parking_spaces,
        )

@dataclass(slots=True)
class MarketAssessment:
    market_price: float
    min_price: float
    max_price: float
    confidence: str
    explanation: str
    liquidity_score: int = 5
    investment_rating: str = "C"
    data_quality_notes: str = ""
    links: list[str] = field(default_factory=list)

@dataclass(slots=True)
class RiskAssessment:
    risk_score: int
    recommendation: str
    time_to_sell: str

@dataclass(slots=True)
class LotEvaluation:
    market: MarketAssessment
    risk: RiskAssessment
