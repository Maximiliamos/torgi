from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParserConfig:
    container_selector: str = "div.lot"
    title_selector: str = "a"
    base_url: str = "https://tbankrot.ru"


@dataclass
class ParsedLotData:
    external_id: str
    title: str
    url: str
    price_text: str = ""
    current_price: float | None = None
    description: str = ""
    status: str = "unknown"
    address: str = ""
    cadastral_number: str = ""
    cadastral_numbers: list[str] = field(default_factory=list)
    area: float | None = None
    building_area: float | None = None
    room_area: float | None = None
    land_area: float | None = None
    floors: int | None = None
    year_built: int | None = None
    year_commissioning: int | None = None
    is_cultural_heritage: bool = False
    raw_payload: dict[str, Any] = field(default_factory=dict)


def parse_money(text: str | None) -> float | None:
    if not text:
        return None
    clean = (
        text.replace("\xa0", " ")
        .replace("\u202f", " ")
        .replace("₽", "")
        .replace("руб.", "")
        .replace("руб", "")
        .strip()
    )
    match = re.search(r"\d[\d\s]*(?:[,.]\d+)?", clean)
    if not match:
        return None
    raw = match.group(0).replace(" ", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


@dataclass
class TorgiGovSearchFilters:
    search_text: str = ""
    type_transaction: str | None = None
    price_min: float | None = None
    price_max: float | None = None
    subject_rf: str | None = None
    fias: str | None = None
    ownership_form: str | None = None
    category_code: str | None = None
    lot_status: str | None = None
    currency_code: str | None = None
    publish_date_from: str | None = None
    publish_date_to: str | None = None
    bidd_end_time_from: str | None = None
    bidd_end_time_to: str | None = None
    auction_start_date_from: str | None = None
    auction_start_date_to: str | None = None
    notice_number: str | None = None
    etp_code: str | None = None
    bidd_type: str | None = None
    bidd_form: str | None = None
    notice_status: str | None = None
    organizer_name: str | None = None
    organizer_inn: str | None = None
    right_holder_name: str | None = None
    right_holder_inn: str | None = None
    attachment_text: str | None = None
    match_phrase: bool = False
    is_msp: bool = False
    page: int = 1
    page_size: int = 20


@dataclass
class TBankrotSearchFilters:
    search_text: str = ""
    region: str | None = None
    price_min: float | None = None
    price_max: float | None = None
    lot_number: str | None = None
    trade_type: str | None = None
    photo_only: bool = False
    debtor: str | None = None
    auction_manager: str | None = None
    organizer: str | None = None
    stop_words: str | None = None
    show_closed: bool = False
    show_paused: bool = False
    page: int = 1
    page_size: int = 20
