from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from bankrotai.domain import NormalizedLot

SOURCE_ID = "sberbank-ast.ru"
SOURCE = "sberbank-ast"
PLATFORM_NAME = "Сбербанк-АСТ"
PLATFORM_CODE = "sberast"

API_URL = "https://www.sberbank-ast.ru/api/Processing/main"
BANKRUPTCY_SOURCE = "Продажа имущества (предприятия) банкротов"
BANKRUPTCY_SECTION_ID = "13"
REAL_ESTATE_BRANCH = "Недвижимое имущество"
PAGE_SIZE = 21
DISPLAY_LIMIT = 10_000

_GOODS_RE = re.compile(
    r"^https://utp\.sberbank-ast\.ru/Catalog/NBT/GoodsView/(\d+)/(\d+)/(\d+)/(\d+)/?$",
    re.IGNORECASE,
)
_ID_RE = re.compile(r"^sberbank-ast\.ru:(\d+)-(\d+)-(\d+)-(\d+)$", re.IGNORECASE)
_SBER_CODE_RE = re.compile(r"^(SBR\d+-[A-ZА-Я0-9-]+?)(?:\.(\d+))?$", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_CADASTRAL_RE = re.compile(r"(?<!\d)(\d{2}:\d{2}(?::\d{1,7}){2,4})(?!\d)")
_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", re.IGNORECASE)
_PRICE_RE = re.compile(
    r"(?P<label>начальн\w*\s+(?:\([^)]*\)\s*)?цен\w*|текущ\w*\s+цен\w*|"
    r"размер\s+задатк\w*|шаг\s+(?:аукциона|торгов))"
    r"[^\d]{0,60}(?P<amount>\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)"
    r"\s*(?:₽|руб(?:\.|лей)?)",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})(?:\s+(\d{2}:\d{2})(?::\d{2})?)?")
_PROPERTY_EVIDENCE_RE = re.compile(
    r"(?:лот\s*№|кадастров|площад|местонахожд|адрес|недвижим|земельн|"
    r"квартир|помещени|здани|гараж)",
    re.IGNORECASE,
)


class SberbankAstContractError(ValueError):
    """Sberbank-AST response does not prove the source-specific contract."""


@dataclass(frozen=True, slots=True)
class CataloguePage:
    lots: list[NormalizedLot]
    page: int
    api_total_reported: int
    raw_rows: int
    property_rows: int
    next_page: int | None
    terminal_page: int | None
    terminal_page_proven: bool
    source_total_proven: bool


def normalize_text(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def parse_decimal(value: object) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError, OverflowError):
        return None
    if not 0 < parsed < 1e15:
        return None
    return parsed


def parse_datetime(value: object) -> datetime | None:
    text = normalize_text(value)
    match = _DATE_RE.search(text)
    if not match:
        return None
    stamp = match.group(1) + (f" {match.group(2)}" if match.group(2) else "")
    fmt = "%d.%m.%Y %H:%M" if match.group(2) else "%d.%m.%Y"
    try:
        return datetime.strptime(stamp, fmt)
    except ValueError:
        return None


def split_sberbank_lot_code(value: object) -> tuple[str | None, str | None, str | None]:
    raw = normalize_text(value)
    if not raw:
        return None, None, None
    match = _SBER_CODE_RE.fullmatch(raw)
    if not match:
        return raw, raw, None
    return raw, match.group(1), match.group(2)


def build_catalogue_filter(page: int) -> str:
    if page < 1:
        raise SberbankAstContractError("page must be one-based and positive")
    page_index = page - 1
    return (
        "<query><targetPageCode>UtpESGoodsList</targetPageCode><searchbyorg></searchbyorg>"
        f"<pagesize>{PAGE_SIZE}</pagesize><pagenum>{page_index}</pagenum><sortType></sortType>"
        f"<sortOrder>desc</sortOrder><sourceTerm>{BANKRUPTCY_SOURCE}</sourceTerm>"
        "<searchBarType>anyWord</searchBarType><saleType></saleType><org></org>"
        "<cadastralNumber></cadastralNumber><isOnMapTerm></isOnMapTerm>"
        "<hasRequest></hasRequest><hasDeposit></hasDeposit><orgInnKpp></orgInnKpp></query>"
    )


def build_catalogue_request(page: int) -> tuple[str, dict[str, Any]]:
    return API_URL, {
        "windowCode": "/ESGoodsList",
        "actionType": "MONITOR",
        "actionCode": "default",
        "documentBody": "",
        "parm": "es",
        "filterBody": build_catalogue_filter(page),
        "options": {},
    }


def terminal_page_for_total(total: int) -> int | None:
    if total <= 0:
        raise SberbankAstContractError("API Total must be positive")
    if total >= DISPLAY_LIMIT:
        return None
    return max(1, math.ceil(total / PAGE_SIZE))


def is_terminal_page(*, page: int, total: int) -> bool:
    terminal = terminal_page_for_total(total)
    return terminal is not None and page == terminal


def page_digest(lots: list[NormalizedLot]) -> str:
    payload = "\n".join(lot.external_id for lot in lots)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_goodsview_url(url: str) -> tuple[str, tuple[str, str, str, str]]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "utp.sberbank-ast.ru":
        raise SberbankAstContractError("GoodsView URL must use the trusted HTTPS host")
    if parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port is not None:
        raise SberbankAstContractError("GoodsView URL contains unsupported URL components")
    match = _GOODS_RE.fullmatch(url)
    if not match:
        raise SberbankAstContractError("GoodsView URL does not match the verified four-part identity")
    groups = (match.group(1), match.group(2), match.group(3), match.group(4))
    return f"{SOURCE_ID}:{'-'.join(groups)}", groups


def detail_url_from_external_id(external_id: str) -> str:
    match = _ID_RE.fullmatch(external_id)
    if not match:
        raise SberbankAstContractError("invalid Sberbank-AST external lot identity")
    return "https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/" + "/".join(match.groups())


def verify_identity(*, external_id: str, detail_url: str) -> None:
    derived, _ = verify_goodsview_url(detail_url)
    if derived != external_id:
        raise SberbankAstContractError("GoodsView identity does not match catalogue identity")


def parse_initial_price(raw: dict[str, Any]) -> float | None:
    amount = raw.get("purchAmountRUB")
    if amount is not None:
        return parse_decimal(amount)
    currency = normalize_text(raw.get("purchCurrency") or raw.get("CurrencyCode")).upper()
    if currency not in {"RUB", "643"}:
        return None
    return parse_decimal(raw.get("purchAmount"))


def normalize_status(value: object) -> str:
    """Map Sberbank lifecycle labels onto TORGI canonical status values."""
    text = normalize_text(value).lower()
    if not text:
        return "unknown"

    # Final trade state wins even when old application dates/labels remain on the card.
    if any(marker in text for marker in (
        "торги отмен", "аннулир", "не состоял", "торги заверш", "торги окончен",
    )):
        return "closed"
    if "приостанов" in text:
        return "paused"
    if re.search(
        r"(?:при[её]м\s+заявок\s+заверш|заверш[её]н\s+при[её]м\s+заявок)",
        text,
    ):
        return "unknown"
    if any(marker in text for marker in (
        "торги объявлен", "торги назначен",
        "ожидается начало приема заявок", "ожидается начало приёма заявок",
    )):
        return "scheduled"
    if (
        re.search(r"при[её]м\s+заявок(?!\s+заверш)", text)
        or "идут торги" in text
        or "проведение торгов" in text
        or "заявки принимаются" in text
    ):
        return "active"
    return "unknown"


def classify_property(raw: dict[str, Any]) -> str | None:
    return "real_estate" if normalize_text(raw.get("BranchGroup")) == REAL_ESTATE_BRANCH else None


def parse_catalogue_row(raw: dict[str, Any], *, page: int) -> NormalizedLot | None:
    object_source = normalize_text(raw.get("ObjectSource"))
    section_id = normalize_text(raw.get("SourceTradeSectionId"))
    if object_source != BANKRUPTCY_SOURCE or section_id != BANKRUPTCY_SECTION_ID:
        raise SberbankAstContractError("bankruptcy filter is not confirmed by the returned row")

    category = classify_property(raw)
    if category is None:
        return None

    href = normalize_text(raw.get("objectHref"))
    external_id, identity_parts = verify_goodsview_url(href)
    title = normalize_text(raw.get("title") or raw.get("BidName") or raw.get("purchName"))
    if not title:
        raise SberbankAstContractError("verified real-estate bankruptcy row has no title")

    text_value = normalize_text(" ".join(
        str(raw.get(key) or "") for key in ("title", "purchName", "Address", "cadastralNumber")
    ))
    cadastral = _CADASTRAL_RE.search(text_value)
    vin = _VIN_RE.search(text_value)
    source_lot_code, procedure_code, lot_number = split_sberbank_lot_code(raw.get("purchCode"))

    return NormalizedLot(
        external_id=external_id,
        source=SOURCE,
        source_system=SOURCE_ID,
        title=title[:500],
        description=text_value[:5000],
        category=category,
        region_slug=None,
        region_name=normalize_text(raw.get("RegionNameTerm")) or None,
        address=normalize_text(raw.get("Address")) or None,
        cadastral_number=cadastral.group(1) if cadastral else None,
        vin=vin.group(0).upper() if vin else None,
        area=None,
        start_price=parse_initial_price(raw),
        current_price=None,
        auction_status=normalize_status(raw.get("PurchaseStageTerm") or raw.get("purchStateNameTerm")),
        lot_url=href,
        source_url=href,
        detail_level="search",
        raw_data={
            "listing_page": page,
            "complete_source_inventory": False,
            "safe_for_archival": False,
            "reconcile_missing": False,
            "bankruptcy_filter_verified": True,
            "bankruptcy_source_term": object_source,
            "source_trade_section_id": section_id,
            "property_scope_verified": True,
            "property_branch_group": REAL_ESTATE_BRANCH,
            "structured_category_verified": True,
            "goods_view_identity": "/".join(identity_parts),
            "individual_lot_identity_verified": True,
            "detail_contract": "GoodsView",
            "source_lot_code": source_lot_code,
            "source_procedure_code": procedure_code,
            "source_lot_number": lot_number,
        },
        platform_name=PLATFORM_NAME,
        platform_code=PLATFORM_CODE,
        procedure_number=procedure_code,
        application_deadline=parse_datetime(raw.get("RequestStopDate")),
        auction_at=None,
    )


def parse_catalogue_page(payload: dict[str, Any], *, page: int) -> CataloguePage:
    if page < 1:
        raise SberbankAstContractError("page must be one-based and positive")
    raw_total = payload.get("Total")
    if raw_total is None:
        raise SberbankAstContractError("invalid API Total")
    try:
        total = int(raw_total)
    except (TypeError, ValueError) as exc:
        raise SberbankAstContractError("invalid API Total") from exc
    if total <= 0:
        raise SberbankAstContractError("API Total must be positive")

    items = payload.get("PurchaseList")
    if not isinstance(items, list):
        raise SberbankAstContractError("PurchaseList is missing from API response")
    if not items:
        raise SberbankAstContractError("empty PurchaseList cannot prove the requested catalogue page")

    lots: list[NormalizedLot] = []
    seen: set[str] = set()
    for item in items:
        raw = item.get("_source") if isinstance(item, dict) else None
        if not isinstance(raw, dict):
            raise SberbankAstContractError("malformed PurchaseList row")
        lot = parse_catalogue_row(raw, page=page)
        if lot is None:
            continue
        if lot.external_id in seen:
            raise SberbankAstContractError(f"duplicate GoodsView identity on page {page}")
        seen.add(lot.external_id)
        lots.append(lot)

    source_total_proven = total < DISPLAY_LIMIT
    terminal = terminal_page_for_total(total)
    if len(items) > PAGE_SIZE:
        raise SberbankAstContractError("PurchaseList exceeds requested page size")
    if not source_total_proven and page * PAGE_SIZE >= DISPLAY_LIMIT:
        raise SberbankAstContractError(
            f"{DISPLAY_LIMIT} is a public display cap, not a proven source end"
        )

    if source_total_proven:
        if terminal is None or page > terminal:
            raise SberbankAstContractError("catalogue page is beyond the proven terminal page")
        expected_rows = PAGE_SIZE if page < terminal else total - (terminal - 1) * PAGE_SIZE
        if len(items) != expected_rows:
            raise SberbankAstContractError(
                "PurchaseList row count conflicts with API Total/page contract"
            )
        next_page = page + 1 if page < terminal else None
    else:
        if len(items) != PAGE_SIZE:
            raise SberbankAstContractError(
                "capped catalogue returned a short page before a proven source end"
            )
        next_page = page + 1

    return CataloguePage(
        lots=lots,
        page=page,
        api_total_reported=total,
        raw_rows=len(items),
        property_rows=len(lots),
        next_page=next_page,
        terminal_page=terminal,
        terminal_page_proven=source_total_proven and page == terminal,
        source_total_proven=source_total_proven,
    )


def _label_money(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for match in _PRICE_RE.finditer(text):
        label = match.group("label").lower()
        amount = parse_decimal(match.group("amount"))
        if amount is None:
            continue
        if "началь" in label:
            key = "initial"
        elif "текущ" in label:
            key = "current"
        elif "задат" in label:
            key = "deposit"
        else:
            key = "step"
        values.setdefault(key, amount)
    return values


def _datetime_after(text: str, label_pattern: str) -> datetime | None:
    match = re.search(
        label_pattern + r"[^\d]{0,50}(\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)",
        text,
        re.IGNORECASE,
    )
    return parse_datetime(match.group(1)) if match else None


def _label_value(text: str, label_pattern: str) -> str | None:
    match = re.search(
        label_pattern
        + r"\s*[:№]?\s*(.{5,300}?)(?=\s+(?:кадастров|площад|начальн|текущ|"
        + r"задат|шаг|статус|дата|лот\b|$))",
        text,
        re.IGNORECASE,
    )
    return normalize_text(match.group(1)) if match else None


def parse_detail(
    html: str | bytes,
    *,
    detail_url: str,
    external_id: str,
) -> NormalizedLot:
    verify_identity(external_id=external_id, detail_url=detail_url)
    soup = BeautifulSoup(html, "html.parser")
    text = normalize_text(soup.get_text(" ", strip=True))
    if len(text) < 250 or not _PROPERTY_EVIDENCE_RE.search(text):
        raise SberbankAstContractError("GoodsView page does not prove an individual property card")

    headings = [
        normalize_text(node.get_text(" ", strip=True))
        for node in soup.select("h1, h2, h3")
    ]
    title_candidates = [item for item in headings if len(item) >= 8 and "информац" not in item.lower()]
    title = max(title_candidates, key=len, default="")
    if not title:
        title = _label_value(
            text,
            r"(?:наименование имущества|предмет договора \(лота\)|предмет торгов|наименование)",
        ) or "Лот"

    cadastral = _CADASTRAL_RE.search(text)
    area_match = _AREA_RE.search(text)
    vin = _VIN_RE.search(text)
    money = _label_money(text)

    raw_code_match = re.search(r"\bSBR\d+-[A-ZА-Я0-9-]+(?:\.\d+)?\b", text, re.IGNORECASE)
    raw_lot_code, procedure_code, lot_number = split_sberbank_lot_code(
        raw_code_match.group(0) if raw_code_match else None
    )
    case_match = re.search(
        r"номер дела о банкротстве\s*[:№]?\s*([A-ZА-Я0-9/.-]{4,50})",
        text,
        re.IGNORECASE,
    )
    address = _label_value(text, r"(?:адрес(?: объекта)?|местонахождение(?: имущества)?)")

    application_start = _datetime_after(
        text,
        r"(?:начало|дата начала)\s+(?:приема|приёма|представления)\s+заявок",
    )
    application_deadline = _datetime_after(
        text,
        r"(?:окончание|дата окончания)\s+(?:приема|приёма|представления)\s+заявок",
    )
    auction_at = _datetime_after(text, r"дата\s+(?:проведения|торгов|аукциона)")

    lowered = text.lower()
    auction_type = "public_offer" if "публичн" in lowered and "предлож" in lowered else None

    return NormalizedLot(
        external_id=external_id,
        source=SOURCE,
        source_system=SOURCE_ID,
        title=title[:500],
        description=text[:5000],
        category="real_estate",
        region_slug=None,
        region_name=None,
        address=address,
        cadastral_number=cadastral.group(1) if cadastral else None,
        vin=vin.group(0).upper() if vin else None,
        area=float(area_match.group(1).replace(",", ".")) if area_match else None,
        start_price=money.get("initial"),
        current_price=money.get("current"),
        auction_status=normalize_status(text),
        lot_url=detail_url,
        source_url=detail_url,
        detail_level="detail",
        raw_data={
            "complete_source_inventory": False,
            "safe_for_archival": False,
            "reconcile_missing": False,
            "bankruptcy_filter_verified": True,
            "property_scope_verified": True,
            "structured_category_verified": True,
            "individual_lot_identity_verified": True,
            "detail_contract": "GoodsView",
            "source_lot_code": raw_lot_code,
            "source_procedure_code": procedure_code,
            "source_lot_number": lot_number,
        },
        platform_name=PLATFORM_NAME,
        platform_code=PLATFORM_CODE,
        procedure_number=procedure_code,
        bankruptcy_case_number=case_match.group(1) if case_match else None,
        deposit_amount=money.get("deposit"),
        application_start_at=application_start,
        application_deadline=application_deadline,
        auction_at=auction_at,
        auction_step_amount=money.get("step"),
        auction_type=auction_type,
        public_offer_schedule=None,
    )


# Compatibility alias for the future integration hook. New source-specific tests
# should call parse_catalogue_page()/parse_detail() directly.
def parse_sberbank_json(_profile: Any, payload: dict[str, Any], *, page: int) -> list[NormalizedLot]:
    return parse_catalogue_page(payload, page=page).lots
