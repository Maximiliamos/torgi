from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from bankrotai.connectors.transient_http import request_with_retries
from bankrotai.domain import NormalizedLot

SOURCE_ID = "etpu.ru"
SOURCE = "etpu"
PLATFORM_NAME = "Уральская ЭТП"
PLATFORM_CODE = "etpu"
LISTING_URL = "https://bankrupt.etpu.ru/public/purchases-all/"
ALLOWED_HOSTS = frozenset({"bankrupt.etpu.ru"})
LOT_URL_RE = re.compile(
    r"bankrupt\.etpu\.ru/public/(auctions|contests|public-offers)/lots/view/(\d+)/",
    re.IGNORECASE,
)
EXTERNAL_ID_RE = re.compile(
    r"^etpu\.ru:(auctions|contests|public-offers)-(\d+)$",
    re.IGNORECASE,
)

STATUS_FILTER_FIELD = (
    "ctl00$ctl00$MainExpandableArea$phExpandCollapse$PurchasesSearchCriteria$"
    "vPurchaseLot_purchaseStatusID_Статус"
)
SEARCH_BUTTON_FIELD = (
    "ctl00$ctl00$MainExpandableArea$phExpandCollapse$SearchButton"
)
SEARCH_CRITERIA_MARKER = "$PurchasesSearchCriteria$"

_SPACE_RE = re.compile(r"\s+")
_CADASTRAL_RE = re.compile(r"(?<!\d)(\d{2}:\d{2}(?::\d{1,7}){2,4})(?!\d)")
_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", re.IGNORECASE)
_CLASSIFIER_END_RE = re.compile(
    r"\s+(?:Интервалы\s+снижения\s+цены|Обеспечение\s+заявки|Документы|Изображения)\b",
    re.IGNORECASE,
)

class EtpuAccessError(RuntimeError):
    """The official ETPU endpoint refused a public read-only request."""

class EtpuSchemaError(RuntimeError):
    """The observed ETPU page no longer satisfies the verified source contract."""

@dataclass(frozen=True, slots=True)
class EtpuStatusFilter:
    value: str
    text: str

NON_TERMINAL_STATUS_FILTERS = (
    EtpuStatusFilter("5", "Извещение опубликовано"),
    EtpuStatusFilter("7", "Прием заявок"),
    EtpuStatusFilter("14", "Прием заявок на интервале не активен"),
    EtpuStatusFilter("8", "Определение участников торгов"),
    EtpuStatusFilter("1", "Идут торги"),
    EtpuStatusFilter("15", "Подведение результатов"),
    EtpuStatusFilter("13", "Приостановлен"),
)

@dataclass(frozen=True, slots=True)
class EtpuPropertyFields:
    address: str | None
    cadastral_number: str | None
    area: float | None
    vin: str | None

@dataclass(frozen=True, slots=True)
class EtpuCataloguePage:
    page: int
    items: tuple[NormalizedLot, ...]
    next_page: int | None
    fingerprint: str

@dataclass(frozen=True, slots=True)
class EtpuPostbackRequest:
    action: str
    data: dict[str, str]

def normalize_text(value: str) -> str:
    return _SPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()

def parse_soup(html: str | bytes) -> BeautifulSoup:
    if isinstance(html, bytes):
        try:
            decoded = html.decode("utf-8")
        except UnicodeDecodeError:
            probe = BeautifulSoup(html, "lxml")
            decoded = html.decode(probe.original_encoding or "windows-1251", errors="replace")
        return BeautifulSoup(decoded, "html.parser")
    return BeautifulSoup(html, "html.parser")

def _safe_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        raise EtpuAccessError("etpu.ru: refused unsafe provider URL")
    return value

def _external_id_from_url(value: str) -> str:
    match = LOT_URL_RE.search(value)
    if not match:
        raise EtpuSchemaError("etpu.ru: detail URL does not match verified lot schema")
    return f"{SOURCE_ID}:{match.group(1).lower()}-{match.group(2)}"

def detail_url_for_external_id(external_id: str) -> str:
    match = EXTERNAL_ID_RE.fullmatch(external_id)
    if not match:
        raise EtpuSchemaError("etpu.ru: invalid external lot identity")
    return _safe_url(
        f"https://bankrupt.etpu.ru/public/{match.group(1).lower()}/lots/view/{match.group(2)}/"
    )

def lot_section(value: str) -> str:
    match = re.search(r"Информация\s+о\s+лоте\s+№\s*\d+", value, re.IGNORECASE)
    return value[match.start():] if match else value

def _labelled_money(value: str, label: str) -> float | None:
    match = re.search(
        label + r"\s*,?\s*руб\.?\s*:\s*(\d[\d\s\u00a0]{0,30}(?:[.,]\d{1,2})?)",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None
    raw = re.sub(r"[\s\u00a0]", "", match.group(1)).replace(",", ".")
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return None
    if not amount.is_finite() or not (Decimal(0) < amount < Decimal("1e15")):
        return None
    return float(amount)

def parse_initial_price(value: str) -> float | None:
    return _labelled_money(lot_section(value), r"Начальная\s+цена")

def parse_current_price(value: str) -> float | None:
    return _labelled_money(lot_section(value), r"Текущая\s+цена")

def parse_deposit(value: str) -> float | None:
    section = lot_section(value)
    return (
        _labelled_money(section, r"Размер\s+обеспечения")
        or _labelled_money(section, r"Размер\s+задатка")
    )

def parse_auction_step(value: str) -> float | None:
    return _labelled_money(lot_section(value), r"Шаг(?:\s+аукциона)?")

def parse_title(value: str) -> str | None:
    section = lot_section(value)
    match = re.search(
        r"Наименование\s*:\s*(.{4,1500}?)"
        r"(?=\s+Дата\s+(?:проведения|начала\s+первого\s+интервала)"
        r"(?:\s*\(\s*Московское\s+время\s*\))?\s*:|\s+Статус\s*:)",
        section,
        re.IGNORECASE,
    )
    return normalize_text(match.group(1)) if match else None

def parse_source_status(value: str) -> str | None:
    section = lot_section(value)
    match = re.search(
        r"Статус\s*:\s*("
        r"Извещение\s+опубликовано|"
        r"При[её]м\s+заявок\s+на\s+интервале\s+не\s+активен|"
        r"При[её]м\s+заявок|"
        r"Определение\s+участников\s+торгов|"
        r"Идут\s+торги|"
        r"Подведение\s+результатов|"
        r"Окончен|"
        r"Не\s+состоялся|"
        r"Отмен[её]н\s+организатором|"
        r"Приостановлен"
        r")\b",
        section,
        re.IGNORECASE,
    )
    return normalize_text(match.group(1)) if match else None

def parse_status(value: str) -> str:
    raw = parse_source_status(value)
    if raw is None:
        return "unknown"
    lowered = raw.lower().replace("ё", "е")
    if lowered == "извещение опубликовано":
        return "scheduled"
    if lowered in {"прием заявок", "идут торги"}:
        return "active"
    if lowered == "приостановлен":
        return "paused"
    if lowered in {"окончен", "не состоялся", "отменен организатором"}:
        return "closed"
    return "unknown"

def parse_procedure_id(value: str) -> str | None:
    match = re.search(
        r"Информация\s+(?:об\s+аукционе|о\s+конкурсе|о\s+публичном\s+предложении)"
        r"\s*№\s*([0-9]{3,20})",
        value,
        re.IGNORECASE,
    )
    return match.group(1) if match else None

def _moscow_datetime(value: str, *labels: str) -> datetime | None:
    section = lot_section(value)
    for label in labels:
        match = re.search(
            label
            + r"\s*\(\s*Московское\s+время\s*\)\s*:\s*"
            + r"(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}(?::\d{2})?)",
            section,
            re.IGNORECASE,
        )
        if not match:
            continue
        raw = match.group(1)
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
    return None

def parse_dates(value: str) -> dict[str, datetime | None]:
    return {
        "application_start_at": _moscow_datetime(
            value,
            r"Дата\s+начала\s+представления\s+заявок(?:\s+на\s+участие)?",
            r"Дата\s+начала\s+при[её]ма\s+заявок(?:\s+на\s+участие)?",
        ),
        "application_deadline": _moscow_datetime(
            value,
            r"Дата\s+окончания\s+представления\s+заявок(?:\s+на\s+участие)?",
            r"Дата\s+окончания\s+при[её]ма\s+заявок(?:\s+на\s+участие)?",
            r"Дата\s+окончания\s+последнего\s+интервала",
        ),
        "auction_at": _moscow_datetime(
            value,
            r"Дата\s+проведения",
            r"Дата\s+начала\s+первого\s+интервала",
        ),
    }

def classifier_section(value: str) -> str | None:
    section = lot_section(value)
    marker = re.search(r"Классификатор\s+ЕФРСБ\s*:\s*", section, re.IGNORECASE)
    if not marker:
        return None
    tail = section[marker.end():]
    end = _CLASSIFIER_END_RE.search(tail)
    raw = tail[:end.start()] if end else tail[:2000]
    cleaned = normalize_text(raw)
    return cleaned[:2000] or None

def classifier_flags(value: str) -> tuple[str, ...]:
    classifier = classifier_section(value)
    if not classifier:
        return ()
    lowered = classifier.lower()
    flags: list[str] = []

    def add(flag: str, *patterns: str) -> None:
        if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in patterns):
            flags.append(flag)

    add("lease", r"\bправ(?:о|а)\s+аренды\b")
    add(
        "shares",
        r"\bправа\s+долевой\s+собственности\b",
        r"\bдол[яи]\s+в\s+уставн",
        r"\bакци(?:я|и|й)\b",
    )
    add("receivables", r"\bправа?\s+требован", r"\bдебитор")
    add(
        "vehicle",
        r"\bавтомобил",
        r"\bсредства\s+транспортн",
        r"\bтранспортн\w*\s+средств",
        r"\bприцеп",
        r"\bполуприцеп",
        r"\bмотоцикл",
        r"\bавтобус",
    )
    add(
        "equipment",
        r"\bоборудован",
        r"\bмашины\b",
        r"\bспециализированная\s+техника\b",
        r"\bэлектродвигател",
        r"\bгенератор",
        r"\bтрансформатор",
        r"\bинструмент\b",
    )
    add("land", r"\bземельные\s+участки\b")
    add(
        "real_estate",
        r"\bжилые\s+здания",
        r"\bздания\s*\(помещения\)",
        r"\bздания\s*\(кроме\s+жилых\)",
        r"\bздания\s+и\s+сооружения",
        r"\bздания\s+для\b",
        r"\bсооружен",
        r"\bпомещен",
        r"\bнезавершенн\w*\s+строительств",
        r"\bнедвижим",
    )
    if not flags:
        flags.append("other")
    return tuple(flags)

def classify_property(value: str) -> str | None:
    if classifier_section(value) is None:
        return None
    flags = classifier_flags(value)
    for preferred in (
        "lease",
        "shares",
        "receivables",
        "real_estate",
        "land",
        "vehicle",
        "equipment",
        "other",
    ):
        if preferred in flags:
            return preferred
    return None

def parse_property_fields(value: str) -> EtpuPropertyFields:
    section = lot_section(value)
    cadastral = _CADASTRAL_RE.search(section)
    vin = _VIN_RE.search(section)
    area_match = _AREA_RE.search(section)
    address_match = re.search(
        r"(?:расположенн\w*\s+по\s+адресу|имущество\s+находится\s+по\s+адресу)"
        r"\s*:\s*(.{5,350}?)(?=;|\s+Классификатор\s+ЕФРСБ|\s+Начальная\s+цена|$)",
        section,
        re.IGNORECASE,
    )
    return EtpuPropertyFields(
        address=normalize_text(address_match.group(1)) if address_match else None,
        cadastral_number=cadastral.group(1) if cadastral else None,
        area=float(area_match.group(1).replace(",", ".")) if area_match else None,
        vin=vin.group(0).upper() if vin else None,
    )

def _parse_interval_schedule(soup: BeautifulSoup) -> list[dict[str, str]] | None:
    for table in soup.select("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [normalize_text(cell.get_text(" ", strip=True)) for cell in rows[0].find_all(["th", "td"])]
        lowered = [item.lower() for item in headers]
        if not any("дата начала интервала" in item for item in lowered):
            continue
        if not any("цена на интервале" in item for item in lowered):
            continue
        result: list[dict[str, str]] = []
        for row in rows[1:]:
            cells = [normalize_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["td", "th"])]
            if len(cells) != len(headers) or not any(cells):
                continue
            result.append(dict(zip(headers, cells)))
        return result or None
    return None

def parse_detail(html: str | bytes, *, detail_url: str, external_id: str | None = None) -> NormalizedLot:
    soup = parse_soup(html)
    text_value = normalize_text(soup.get_text(" ", strip=True))
    if len(text_value) < 80:
        raise EtpuSchemaError("etpu.ru: detail page has insufficient content")
    derived_id = _external_id_from_url(detail_url)
    if external_id is not None and external_id != derived_id:
        raise EtpuSchemaError("etpu.ru: detail identity mismatch")
    external_id = external_id or derived_id

    prices = (parse_initial_price(text_value), parse_current_price(text_value))
    dates = parse_dates(text_value)
    properties = parse_property_fields(text_value)
    classifier = classifier_section(text_value)
    flags = classifier_flags(text_value)
    source_status = parse_source_status(text_value)
    procedure_id = parse_procedure_id(text_value)
    title = parse_title(text_value) or "Лот"
    sale_type = LOT_URL_RE.search(detail_url)
    kind = sale_type.group(1).lower() if sale_type else ""

    case_match = re.search(
        r"номер дела о банкротстве\s*[:№]?\s*([A-ZА-Я0-9/.-]{4,50})",
        text_value,
        re.IGNORECASE,
    )
    return NormalizedLot(
        external_id=external_id,
        source=SOURCE,
        source_system=SOURCE_ID,
        title=title[:500],
        description=text_value[:5000],
        category=classify_property(text_value) or "other",
        region_slug=None,
        region_name=None,
        address=properties.address,
        cadastral_number=properties.cadastral_number,
        vin=properties.vin,
        area=properties.area,
        start_price=prices[0],
        current_price=prices[1],
        auction_status=parse_status(text_value),
        lot_url=detail_url,
        source_url=detail_url,
        detail_level="detail",
        raw_data={
            "complete_source_inventory": False,
            "safe_for_archival": False,
            "reconcile_missing": False,
            "efrsb_classifier": classifier,
            "efrsb_classifier_flags": list(flags),
            "structured_property_classification": classifier is not None,
            "detail_initial_price_verified": prices[0] is not None,
            "detail_current_price_verified": prices[1] is not None,
            "price_contract": "etpu_labelled_rub_fields",
            "source_status_text": source_status,
            "trade_id": procedure_id,
            "lot_id": external_id.rsplit("-", 1)[-1],
        },
        platform_name=PLATFORM_NAME,
        platform_code=PLATFORM_CODE,
        procedure_number=procedure_id,
        bankruptcy_case_number=case_match.group(1) if case_match else None,
        deposit_amount=parse_deposit(text_value),
        application_start_at=dates["application_start_at"],
        application_deadline=dates["application_deadline"],
        auction_at=dates["auction_at"],
        auction_timezone="Europe/Moscow",
        auction_step_amount=parse_auction_step(text_value),
        auction_type={
            "auctions": "auction",
            "contests": "contest",
            "public-offers": "public_offer",
        }.get(kind),
        public_offer_schedule=_parse_interval_schedule(soup) if kind == "public-offers" else None,
    )

def _row_container(anchor: Tag) -> Tag:
    row = anchor.find_parent("tr")
    if isinstance(row, Tag):
        return row
    for name in ("article", "li"):
        parent = anchor.find_parent(name)
        if isinstance(parent, Tag):
            return parent
    parent = anchor.find_parent(class_=re.compile(r"(?:lot|trade|item|card|result)", re.IGNORECASE))
    if isinstance(parent, Tag):
        return parent
    return anchor.parent if isinstance(anchor.parent, Tag) else anchor

def _parse_table_amount(raw: str) -> float | None:
    cleaned = normalize_text(raw).replace(" ", "").replace("\xa0", "")
    cleaned = re.sub(r"(?:₽|руб(?:\.|лей)?)$", "", cleaned, flags=re.IGNORECASE)
    if not re.fullmatch(r"\d{1,14}(?:[.,]\d{1,2})?", cleaned):
        return None
    amount = float(cleaned.replace(",", "."))
    return amount if 0 < amount < 1e15 else None

def _table_cells_by_heading(row: Tag) -> dict[str, Tag]:
    if row.name != "tr":
        return {}
    table = row.find_parent("table")
    if not isinstance(table, Tag):
        return {}
    cells = row.find_all("td", recursive=False)
    headings: list[str] = []
    for heading_row in table.select("tr"):
        headers = heading_row.find_all("th", recursive=False)
        if headers:
            headings = [normalize_text(item.get_text(" ", strip=True)).lower() for item in headers]
            break
    if len(headings) != len(cells):
        return {}
    return dict(zip(headings, cells))

def _table_prices(row: Tag) -> tuple[float | None, float | None]:
    cells = _table_cells_by_heading(row)
    initial = current = None
    for heading, cell in cells.items():
        if re.search(r"начальн\w*\s+цен\w*|цена\s+лот\w*", heading):
            initial = _parse_table_amount(cell.get_text(" ", strip=True))
        elif re.search(r"текущ\w*\s+цен\w*", heading):
            current = _parse_table_amount(cell.get_text(" ", strip=True))
    return initial, current

def _status_key(value: str) -> str:
    return normalize_text(value).lower().replace("ё", "е")

def _catalogue_source_status(row: Tag, context: str) -> str | None:
    for heading, cell in _table_cells_by_heading(row).items():
        if heading == "статус" or heading.startswith("статус "):
            value = normalize_text(cell.get_text(" ", strip=True))
            return value or None
    for status_filter in (
        *NON_TERMINAL_STATUS_FILTERS,
        EtpuStatusFilter("2", "Окончен"),
        EtpuStatusFilter("3", "Не состоялся"),
        EtpuStatusFilter("4", "Отменён организатором"),
    ):
        if _status_key(status_filter.text) in _status_key(context):
            return status_filter.text
    return None

def _catalogue_status(value: str | None) -> str:
    if not value:
        return "unknown"
    lowered = _status_key(value)
    if lowered in {"прием заявок", "идут торги"}:
        return "active"
    if lowered == "извещение опубликовано":
        return "scheduled"
    if lowered == "приостановлен":
        return "paused"
    if lowered in {"окончен", "не состоялся", "отменен организатором"}:
        return "closed"
    return "unknown"

def parse_catalogue_page(
    html: str | bytes,
    *,
    page: int,
    expected_status: EtpuStatusFilter | None = None,
    allow_empty: bool = False,
) -> tuple[NormalizedLot, ...]:
    soup = parse_soup(html)
    candidates: dict[str, tuple[Tag, re.Match[str]]] = {}
    for anchor in soup.select("a[href]"):
        href = urljoin(LISTING_URL, str(anchor.get("href") or "").strip())
        match = LOT_URL_RE.search(href)
        if not match:
            continue
        _safe_url(href)
        candidates[href] = (anchor, match)

    lots: list[NormalizedLot] = []
    seen: set[str] = set()
    for href, (anchor, _match) in candidates.items():
        external_id = _external_id_from_url(href)
        if external_id in seen:
            continue
        seen.add(external_id)
        container = _row_container(anchor)
        context = normalize_text(container.get_text(" ", strip=True))
        title = normalize_text(anchor.get_text(" ", strip=True))
        if container.name == "tr":
            choices = [
                normalize_text(cell.get_text(" ", strip=True))
                for cell in container.find_all("td", recursive=False)
            ]
            viable = [item for item in choices if 4 <= len(item) <= 1000 and not item.isdigit()]
            if viable:
                title = max(viable, key=len)
        if len(title) < 4 or title.isdigit():
            title = f"ETPU lot {external_id.rsplit('-', 1)[-1]}"
        initial, current = _table_prices(container)
        source_status = _catalogue_source_status(container, context)
        if expected_status is not None and (
            source_status is None
            or _status_key(source_status) != _status_key(expected_status.text)
        ):
            raise EtpuSchemaError(
                "etpu.ru: filtered catalogue row status does not match requested "
                f"status {expected_status.value}:{expected_status.text}; "
                f"external_id={external_id}; observed_status={source_status!r}"
            )
        cadastre = _CADASTRAL_RE.search(context)
        vin = _VIN_RE.search(context)
        area_match = _AREA_RE.search(context)
        lots.append(
            NormalizedLot(
                external_id=external_id,
                source=SOURCE,
                source_system=SOURCE_ID,
                title=title[:500],
                description=context[:5000],
                category="other",
                region_slug=None,
                region_name=None,
                address=None,
                cadastral_number=cadastre.group(1) if cadastre else None,
                vin=vin.group(0).upper() if vin else None,
                area=float(area_match.group(1).replace(",", ".")) if area_match else None,
                start_price=initial,
                current_price=current,
                auction_status=_catalogue_status(source_status),
                lot_url=href,
                source_url=href,
                detail_level="search",
                raw_data={
                    "listing_page": page,
                    "complete_source_inventory": False,
                    "safe_for_archival": False,
                    "reconcile_missing": False,
                    "individual_lot_identity_verified": True,
                    "source_status_text": source_status,
                    "status_filter_value": expected_status.value if expected_status else None,
                },
                platform_name=PLATFORM_NAME,
                platform_code=PLATFORM_CODE,
            )
        )
    if not lots and not allow_empty:
        raise EtpuSchemaError(f"etpu.ru: no lot links on catalogue page {page}")
    return tuple(lots)

def parse_filter_contract(html: str | bytes) -> dict[str, Any]:
    """Return only public, non-secret catalogue filter names and option values."""
    soup = parse_soup(html)
    form = soup.find("form")
    if not isinstance(form, Tag):
        raise EtpuSchemaError("etpu.ru: catalogue filter form disappeared")

    selects: list[dict[str, Any]] = []
    for node in form.select("select"):
        name = str(node.get("name") or "")
        if not name or SEARCH_CRITERIA_MARKER not in name:
            continue
        options = []
        for option in node.select("option"):
            options.append({
                "value": str(option.get("value") or ""),
                "text": normalize_text(option.get_text(" ", strip=True)),
                "selected": option.has_attr("selected"),
            })
        selects.append({
            "name": name,
            "id": str(node.get("id") or ""),
            "options": options,
        })

    controls: list[dict[str, str | bool]] = []
    for node in form.select("input[name]"):
        field_type = str(node.get("type") or "text").lower()
        name = str(node.get("name") or "")
        if field_type == "hidden" or (
            SEARCH_CRITERIA_MARKER not in name and name != SEARCH_BUTTON_FIELD
        ):
            continue
        controls.append({
            "name": str(node.get("name") or ""),
            "id": str(node.get("id") or ""),
            "type": field_type,
            "value": str(node.get("value") or ""),
            "checked": node.has_attr("checked"),
        })

    return {
        "action": str(form.get("action") or ""),
        "method": str(form.get("method") or "get").lower(),
        "selects": selects,
        "controls": controls,
    }

def _selected_option_value(select: Tag) -> str:
    selected = select.find("option", selected=True)
    if not isinstance(selected, Tag):
        selected = select.find("option")
    return str(selected.get("value") or "") if isinstance(selected, Tag) else ""

def _successful_search_form_data(form: Tag) -> dict[str, str]:
    """Collect only browser-successful ASP.NET state and public search controls."""
    data = {
        str(node.get("name")): str(node.get("value") or "")
        for node in form.select("input[type=hidden][name]")
    }
    for node in form.select("input[name]"):
        name = str(node.get("name") or "")
        field_type = str(node.get("type") or "text").lower()
        if not name or SEARCH_CRITERIA_MARKER not in name:
            continue
        if field_type in {"submit", "button", "image", "file", "password"}:
            continue
        if field_type in {"checkbox", "radio"} and not node.has_attr("checked"):
            continue
        data[name] = str(node.get("value") or "")
    for node in form.select("select[name]"):
        name = str(node.get("name") or "")
        if name and SEARCH_CRITERIA_MARKER in name:
            data[name] = _selected_option_value(node)
    return data

def _status_select(form: Tag) -> Tag:
    node = form.find("select", attrs={"name": STATUS_FILTER_FIELD})
    if not isinstance(node, Tag):
        raise EtpuSchemaError("etpu.ru: official status filter disappeared")
    return node

def _verify_status_option(form: Tag, status_filter: EtpuStatusFilter) -> None:
    select = _status_select(form)
    options = {
        str(option.get("value") or ""): normalize_text(option.get_text(" ", strip=True))
        for option in select.select("option")
    }
    if options.get(status_filter.value) != status_filter.text:
        raise EtpuSchemaError(
            "etpu.ru: official status filter option changed for "
            f"{status_filter.value}:{status_filter.text}"
        )

def selected_status_filter(html: str | bytes) -> EtpuStatusFilter | None:
    soup = parse_soup(html)
    form = soup.find("form")
    if not isinstance(form, Tag):
        raise EtpuSchemaError("etpu.ru: catalogue filter form disappeared")
    select = _status_select(form)
    selected = select.find("option", selected=True)
    if not isinstance(selected, Tag):
        return None
    return EtpuStatusFilter(
        str(selected.get("value") or ""),
        normalize_text(selected.get_text(" ", strip=True)),
    )

def build_filter_request(
    html: str | bytes,
    *,
    current_url: str,
    status_filter: EtpuStatusFilter,
) -> EtpuPostbackRequest:
    soup = parse_soup(html)
    form = soup.find("form")
    if not isinstance(form, Tag):
        raise EtpuSchemaError("etpu.ru: catalogue filter form disappeared")
    method = str(form.get("method") or "get").strip().lower()
    if method != "post":
        raise EtpuSchemaError("etpu.ru: official search form is no longer POST")
    _verify_status_option(form, status_filter)
    data = _successful_search_form_data(form)
    data[STATUS_FILTER_FIELD] = status_filter.value
    data[SEARCH_BUTTON_FIELD] = "Искать торги"
    action = urljoin(current_url or LISTING_URL, str(form.get("action") or ""))
    return EtpuPostbackRequest(action=_safe_url(action), data=data)

def _postback_links(soup: BeautifulSoup) -> list[Tag]:
    return [
        node
        for node in soup.select("a[href]")
        if "__doPostBack" in str(node.get("href") or "")
    ]

def parse_next_page(html: str | bytes, *, page: int) -> int | None:
    soup = parse_soup(html)
    links = _postback_links(soup)
    expected = str(page + 1)
    if any(normalize_text(node.get_text(" ", strip=True)) == expected for node in links):
        return page + 1
    if any(normalize_text(node.get_text(" ", strip=True)) in {">>", "»"} for node in links):
        return page + 1
    return None

def is_terminal_page(html: str | bytes, *, page: int) -> bool:
    return parse_next_page(html, page=page) is None

def build_next_request(
    html: str | bytes,
    *,
    current_url: str,
    target_page: int,
    sequential_previous: bool,
) -> EtpuPostbackRequest:
    soup = parse_soup(html)
    links = _postback_links(soup)
    link = next(
        (
            node
            for node in links
            if normalize_text(node.get_text(" ", strip=True)) == str(target_page)
        ),
        None,
    )
    block_boundary = target_page > 1 and (target_page - 1) % 10 == 0
    if link is None and sequential_previous and block_boundary:
        link = next(
            (
                node
                for node in links
                if normalize_text(node.get_text(" ", strip=True)) in {">>", "»"}
            ),
            None,
        )
    if not isinstance(link, Tag):
        raise EtpuSchemaError(f"etpu.ru: page {target_page} postback target disappeared")
    postback = re.search(
        r"__doPostBack\('([^']*)','([^']*)'\)",
        str(link.get("href") or ""),
    )
    if not postback:
        raise EtpuSchemaError(f"etpu.ru: invalid page {target_page} postback")
    form = link.find_parent("form") or soup.find("form")
    root = form if isinstance(form, Tag) else soup
    data = _successful_search_form_data(root)
    data.pop(SEARCH_BUTTON_FIELD, None)
    data["__EVENTTARGET"], data["__EVENTARGUMENT"] = postback.groups()
    action = str(form.get("action") or "") if isinstance(form, Tag) else ""
    return EtpuPostbackRequest(
        action=_safe_url(urljoin(current_url or LISTING_URL, action) if action else current_url or LISTING_URL),
        data=data,
    )

class EtpuClient:
    RETRY_DELAYS_SECONDS = (1.0, 2.0, 4.0)

    def __init__(
        self,
        *,
        session: requests.Session | Any | None = None,
        timeout: float = 30.0,
        status_filter: EtpuStatusFilter | None = None,
    ) -> None:
        self.session = session or requests.Session()
        if hasattr(self.session, "headers"):
            self.session.headers.setdefault(
                "User-Agent",
                "BankrotAI-ETPU-Readonly-Audit/1.0 (+https://github.com/Maximiliamos/torgi)",
            )
        self.timeout = timeout
        self.status_filter = status_filter
        self._last_response: Any | None = None
        self._last_page: int | None = None
        self._fingerprints: dict[int, str] = {}

    def _read(self, method: str, url: str, **kwargs: Any) -> Any:
        response = request_with_retries(
            self.session,
            method,
            _safe_url(url),
            delays=self.RETRY_DELAYS_SECONDS,
            **kwargs,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        if 300 <= status < 400:
            raise EtpuAccessError(f"etpu.ru: unexpected redirect HTTP {status}")
        if status in {401, 403, 429}:
            raise EtpuAccessError(f"etpu.ru: provider returned HTTP {status}")
        response.raise_for_status()
        response_url = str(getattr(response, "url", url) or url)
        _safe_url(response_url)
        return response

    def fetch_filter_contract(self) -> dict[str, Any]:
        response = self._read(
            "get",
            LISTING_URL,
            timeout=self.timeout,
            allow_redirects=False,
        )
        return parse_filter_contract(response.content)

    def _filtered_first_page(self) -> Any:
        initial = self._read(
            "get",
            LISTING_URL,
            timeout=self.timeout,
            allow_redirects=False,
        )
        if self.status_filter is None:
            return initial
        request = build_filter_request(
            initial.content,
            current_url=str(getattr(initial, "url", LISTING_URL) or LISTING_URL),
            status_filter=self.status_filter,
        )
        response = self._read(
            "post",
            request.action,
            data=request.data,
            timeout=self.timeout,
            allow_redirects=False,
        )
        selected = selected_status_filter(response.content)
        if selected is None or (
            selected.value != self.status_filter.value
            or _status_key(selected.text) != _status_key(self.status_filter.text)
        ):
            raise EtpuSchemaError(
                "etpu.ru: provider did not retain requested official status filter"
            )
        return response

    def fetch_catalogue_page(self, page: int) -> EtpuCataloguePage:
        if page < 1:
            raise EtpuSchemaError("etpu.ru: page must be >= 1")
        if page == 1:
            response = self._filtered_first_page()
        else:
            previous = self._last_response if self._last_page == page - 1 else None
            sequential = previous is not None
            previous = previous or self._filtered_first_page()
            request = build_next_request(
                previous.content,
                current_url=str(getattr(previous, "url", LISTING_URL) or LISTING_URL),
                target_page=page,
                sequential_previous=sequential,
            )
            response = self._read(
                "post",
                request.action,
                data=request.data,
                timeout=self.timeout,
                allow_redirects=False,
            )

        fingerprint = hashlib.sha256(response.content).hexdigest()
        if page > 1 and fingerprint in {
            value for number, value in self._fingerprints.items() if number < page
        }:
            raise EtpuSchemaError(f"etpu.ru: repeated catalogue page content at page {page}")
        self._fingerprints[page] = fingerprint
        self._last_response, self._last_page = response, page
        if self.status_filter is not None:
            selected = selected_status_filter(response.content)
            if selected is None or (
                selected.value != self.status_filter.value
                or _status_key(selected.text) != _status_key(self.status_filter.text)
            ):
                raise EtpuSchemaError(
                    "etpu.ru: requested status filter was lost during pagination"
                )
        items = parse_catalogue_page(
            response.content,
            page=page,
            expected_status=self.status_filter,
            allow_empty=self.status_filter is not None,
        )
        return EtpuCataloguePage(
            page=page,
            items=items,
            next_page=parse_next_page(response.content, page=page),
            fingerprint=fingerprint,
        )

    def fetch_detail(self, external_id: str) -> NormalizedLot:
        url = detail_url_for_external_id(external_id)
        response = self._read("get", url, timeout=self.timeout, allow_redirects=False)
        return parse_detail(response.content, detail_url=url, external_id=external_id)

