from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag

from bankrotai.connectors.etp_sources.status_normalization import _status
from bankrotai.connectors.transient_http import request_with_retries
from bankrotai.domain import NormalizedLot


SOURCE_ID = "rus-on.ru"
PLATFORM_NAME = "RUSSIA OnLine"
PLATFORM_CODE = "ruson"
LISTING_URL = "https://rus-on.ru/bankrot/trade_list.php"
DETAIL_URL_TEMPLATE = "https://rus-on.ru/bankrot/lot_view.php?lot_nid={identity}"
FORM_ID = "tradesearch"
PAGE_FIELD = "pagenum"
LOT_URL_RE = re.compile(r"^https://rus-on\.ru/bankrot/lot_view\.php\?[^#]*\blot_nid=(\d+)(?:[&#]|$)", re.I)
MOSCOW = ZoneInfo("Europe/Moscow")
_SPACE_RE = re.compile(r"\s+")
_CURRENCY_RE = re.compile(r"(?:₽|руб(?:\.|лей)?|RUB)", re.IGNORECASE)
_MONEY_FULL_RE = re.compile(
    r"^\s*(\d{1,3}(?:[\s\u00a0]\d{3})+|\d{1,14})(?:[.,](\d{1,2}))?\s*"
    r"(?:₽|руб(?:\.|лей)?|RUB)?\s*$",
    re.IGNORECASE,
)


def _clean(value: str) -> str:
    return _SPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()


def _key(value: str) -> str:
    return _clean(value).casefold().replace("ё", "е").strip(" :|")


def _parse_money(value: str | None) -> float | None:
    if not value:
        return None
    match = _MONEY_FULL_RE.fullmatch(_clean(value))
    if not match:
        return None
    whole = re.sub(r"[\s\u00a0]", "", match.group(1))
    fraction = match.group(2)
    raw = whole + ("." + fraction if fraction is not None else "")
    try:
        amount = float(raw)
    except ValueError:
        return None
    return amount if 0 < amount < 1e15 else None


def _money_format(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _clean(value)
    numeric = _CURRENCY_RE.sub("", cleaned).strip()
    if "," in numeric:
        return "decimal_comma"
    if "." in numeric:
        return "decimal_dot"
    return "integer"


def _money_after_label(text: str, label: str) -> str | None:
    match = re.search(
        label + r"\s*[:|]?\s*"
        r"(\d{1,3}(?:[\s\u00a0]\d{3})+|\d{1,14})(?:[.,]\d{1,2})?"
        r"\s*(?:₽|руб(?:\.|лей)?|RUB)?",
        text,
        re.IGNORECASE,
    )
    return _clean(match.group(0)[match.group(0).lower().find(match.group(1).lower()):]) if match else None


def _datetime_after_label(text: str, label: str) -> datetime | None:
    match = re.search(
        label + r"\s*[:|]?\s*"
        r"(\d{2}\.\d{2}\.(?:\d{4}|\d{2})(?:\s+\d{2}:\d{2}(?::\d{2})?)?)",
        text,
        re.IGNORECASE,
    )
    return _parse_datetime(match.group(1)) if match else None


def _status_phrase(text: str) -> str:
    match = re.search(
        r"(торги\s+(?:не\s+состоялись|завершены|отменены|приостановлены|объявлены)"
        r"|при[её]м\s+заявок|идут\s+торги|извещение\s+опубликовано)",
        text,
        re.IGNORECASE,
    )
    return _clean(match.group(1)) if match else ""


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    match = re.search(
        r"(\d{2}\.\d{2}\.(?:\d{4}|\d{2}))(?:\s+(\d{2}:\d{2}(?::\d{2})?))?",
        value,
    )
    if not match:
        return None
    raw = match.group(1) + " " + (match.group(2) or "00:00")
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%y %H:%M:%S", "%d.%m.%y %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def _to_moscow_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(MOSCOW).replace(tzinfo=None)
    return value


def _scalar_fields(soup: BeautifulSoup) -> dict[str, str]:
    fields: dict[str, str] = {}
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        label = _key(cells[0].get_text(" ", strip=True))
        value = _clean(" ".join(cell.get_text(" ", strip=True) for cell in cells[1:]))
        if label and value and label not in fields:
            fields[label] = value
    return fields


def _field(fields: dict[str, str], *labels: str) -> str | None:
    wanted = tuple(_key(label) for label in labels)
    for label in wanted:
        if label in fields:
            return fields[label]
    for key, value in fields.items():
        if any(key.startswith(label) for label in wanted):
            return value
    return None


def _fallback_label(text: str, label: str, *, stop: str) -> str | None:
    match = re.search(
        label + r"\s*[:|]?\s*(.+?)(?=\s+(?:" + stop + r")\s*[:|]?\s*|\Z)",
        text,
        re.IGNORECASE,
    )
    return _clean(match.group(1)) if match else None


def _source_category(categories: str, asset_text: str) -> str:
    explicit = _key(categories)
    asset = _key(asset_text)
    if re.search(r"транспорт|автомоб|авто\b|vehicle", explicit):
        return "vehicle"
    if re.search(r"дебитор|прав[оа]\s+требован", explicit):
        return "receivables"
    if "земель" in explicit and not re.search(r"жил|недвиж", explicit):
        return "land"
    if re.search(r"жил|недвиж|квартир|помещен|здани|гараж|машино.?мест", explicit):
        return "real_estate"
    if re.search(r"автомоб|транспорт|\bvin\b|прицеп|снегоход", asset):
        return "vehicle"
    if re.search(r"прав[оа]\s+требован|дебитор", asset):
        return "receivables"
    if re.search(r"квартир|комнат|жилой\s+дом|недвижим|помещен|здани|гараж|машино.?мест", asset):
        return "real_estate"
    if re.search(r"земельн|участок|кадастров", asset):
        return "land"
    return "other"


def _extract_address(location: str | None, description: str) -> str | None:
    if location:
        cleaned = _clean(location)
        return cleaned[:500] if cleaned else None
    match = re.search(
        r"\bадрес(?:\s*\(местонахождение\))?\s*:\s*(.+?)"
        r"(?=(?:,\s*)?(?:площад|кадастров|вид\s+права|$))",
        description,
        re.IGNORECASE,
    )
    if not match:
        return None
    value = _clean(match.group(1)).strip(" ,.;")
    return value[:500] if value else None


def _public_offer_schedule(soup: BeautifulSoup) -> list[dict[str, Any]]:
    for table in soup.select("table"):
        rows = table.select("tr")
        header_index: int | None = None
        columns: dict[str, int] = {}
        price_header = ""
        for index, row in enumerate(rows):
            cells = row.find_all(["th", "td"], recursive=False)
            labels = [_key(cell.get_text(" ", strip=True)) for cell in cells]
            joined = " | ".join(labels)
            if "цена на периоде" not in joined or "дата начала" not in joined or "дата окончания" not in joined:
                continue
            header_index = index
            for pos, label in enumerate(labels):
                if "дата начала" in label:
                    columns["start"] = pos
                elif "дата окончания" in label:
                    columns["end"] = pos
                elif "цена на периоде" in label:
                    columns["price"] = pos
                    price_header = label
                elif "задат" in label:
                    columns["deposit"] = pos
            break
        if header_index is None or not {"start", "end", "price"} <= columns.keys():
            continue
        result: list[dict[str, Any]] = []
        for row in rows[header_index + 1:]:
            cells = row.find_all(["th", "td"], recursive=False)
            max_index = max(columns.values())
            if len(cells) <= max_index:
                continue
            values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
            local_start = _parse_datetime(values[columns["start"]])
            local_end = _parse_datetime(values[columns["end"]])
            price = _parse_money(values[columns["price"]])
            if local_start is None or local_end is None or price is None:
                continue
            # Source publishes Moscow-local minute-precision inclusive period ends
            # (e.g. 13:59). Keep the public business clock in Moscow time and store
            # an exclusive boundary one minute later to avoid a false price gap.
            end_exclusive = local_end + timedelta(minutes=1)
            item: dict[str, Any] = {
                "starts_at": _to_moscow_naive(local_start).isoformat(),
                "ends_at": _to_moscow_naive(end_exclusive).isoformat(),
                "price": price,
                "source_timezone": "Europe/Moscow",
            }
            if re.search(r"(?:\bруб\.?\b|₽|\bRUB\b)", price_header, re.IGNORECASE):
                item["source_currency"] = "RUB"
            if "deposit" in columns:
                deposit = _parse_money(values[columns["deposit"]])
                if deposit is not None:
                    item["deposit_amount"] = deposit
            result.append(item)
        if result:
            return result
    return []


def _active_period(
    schedule: list[dict[str, Any]],
    observed_at_utc: datetime,
) -> tuple[float, datetime | None, float | None] | None:
    observed = (
        observed_at_utc.replace(tzinfo=timezone.utc)
        if observed_at_utc.tzinfo is None
        else observed_at_utc.astimezone(timezone.utc)
    )
    observed_moscow = observed.astimezone(MOSCOW).replace(tzinfo=None)
    for index, period in enumerate(schedule):
        try:
            starts_at = datetime.fromisoformat(str(period["starts_at"]))
            ends_at = datetime.fromisoformat(str(period["ends_at"]))
            price = float(period["price"])
        except (KeyError, TypeError, ValueError):
            continue
        starts_at = _to_moscow_naive(starts_at)
        ends_at = _to_moscow_naive(ends_at)
        if starts_at <= observed_moscow < ends_at:
            next_price = (
                float(schedule[index + 1]["price"])
                if index + 1 < len(schedule) else None
            )
            return price, ends_at, next_price
    return None


def parse_ruson_detail_fields(
    soup: BeautifulSoup,
    *,
    observed_at_utc: datetime | None = None,
) -> dict[str, Any]:
    """Parse RUSSIA OnLine lot fields without borrowing values from unrelated page sections."""
    fields = _scalar_fields(soup)
    text = _clean(soup.get_text(" ", strip=True))

    title = _field(fields, "Наименование имущества") or _fallback_label(
        text,
        r"Наименование имущества",
        stop=r"C?ведения об имуществе|Является объектом недвижимости|Начальная цена",
    )
    description = _field(
        fields,
        "Cведения об имуществе (предприятии) должника, выставляемом на торги, его составе, характеристиках, описание",
        "Сведения об имуществе (предприятии) должника, выставляемом на торги, его составе, характеристиках, описание",
    ) or title or ""
    location = _field(fields, "Местонахождение имущества", "Место нахождения имущества")
    categories = _field(fields, "Категории лота") or ""
    status_text = _field(fields, "Статус торгов") or _status_phrase(text)

    initial_raw = _field(fields, "Начальная цена")
    if initial_raw is None:
        initial_raw = _money_after_label(text, r"Начальн\w*\s+цен\w*")
    start_price = _parse_money(initial_raw)

    schedule = _public_offer_schedule(soup)
    now_utc = observed_at_utc or datetime.now(timezone.utc).replace(tzinfo=None)
    if now_utc.tzinfo is not None:
        now_utc = now_utc.astimezone(timezone.utc).replace(tzinfo=None)
    active = _active_period(schedule, now_utc)

    source_type = _field(fields, "Тип торгов") or ""
    source_type_key = _key(source_type)
    if "публич" in source_type_key:
        auction_type = "public_offer"
        detail_template = "public_offer"
    elif "аукцион" in source_type_key:
        auction_type = "auction"
        detail_template = "auction"
    else:
        auction_type = None
        detail_template = "unknown"

    heading = next(
        (_clean(node.get_text(" ", strip=True)) for node in soup.select("h1, h2, h3")
         if "торгов" in _key(node.get_text(" ", strip=True))),
        "",
    )
    procedure_match = re.search(r"торгов\s+([0-9]+-[A-ZА-Я]+)", heading, re.IGNORECASE)

    return {
        "title": title,
        "asset_text": _clean(" ".join(part for part in (title or "", description, location or "", categories) if part)),
        "address": _extract_address(location, description),
        "category": _source_category(categories, " ".join((title or "", description))),
        "source_categories": categories or None,
        "status_text": status_text,
        "start_price": start_price,
        "initial_price_format": _money_format(initial_raw),
        "initial_price_had_currency": bool(initial_raw and _CURRENCY_RE.search(initial_raw)),
        "deposit_amount": _parse_money(_field(fields, "Размер задатка")),
        "auction_step_amount": _parse_money(_field(fields, "Шаг аукциона")),
        "application_start_at": _parse_datetime(_field(
            fields,
            "Дата начала представления заявок на участие",
            "Дата начала приема заявок",
            "Дата начала приёма заявок",
        )) or _datetime_after_label(
            text, r"Дата начала (?:представления|при[её]ма) заявок(?: на участие)?",
        ),
        "application_deadline": _parse_datetime(_field(
            fields,
            "Дата окончания представления заявок на участие",
            "Дата окончания приема заявок",
            "Дата окончания приёма заявок",
        )) or _datetime_after_label(
            text, r"Дата окончания (?:представления|при[её]ма) заявок(?: на участие)?",
        ),
        "auction_at": _parse_datetime(_field(fields, "Дата проведения"))
        or _datetime_after_label(text, r"Дата проведения"),
        "auction_type": auction_type,
        "public_offer_schedule": schedule or None,
        "current_price": active[0] if active else None,
        "next_price_reduction_at": active[1] if active else None,
        "next_interval_price": active[2] if active else None,
        "current_price_source": "public_offer_schedule" if active else None,
        "public_offer_currency": next(
            (
                str(period.get("source_currency"))
                for period in schedule
                if isinstance(period, dict) and period.get("source_currency")
            ),
            None,
        ),
        "procedure_number": procedure_match.group(1) if procedure_match else None,
        "detail_template": detail_template,
    }


_CADASTRAL_RE = re.compile(r"(?<!\d)(\d{2}:\d{2}(?::\d{1,7}){2,4})(?!\d)")
_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", re.IGNORECASE)


def build_ruson_detail_lot(
    soup: BeautifulSoup,
    *,
    external_id: str,
    detail_url: str,
    platform_name: str,
    platform_code: str,
    observed_at_utc: datetime | None,
    complete_source_inventory: bool,
    safe_for_archival: bool,
) -> NormalizedLot:
    """Build one verified RUSSIA OnLine lot from source-scoped structured fields."""
    values = parse_ruson_detail_fields(soup, observed_at_utc=observed_at_utc)
    text_value = _clean(soup.get_text(" ", strip=True))
    title = str(values.get("title") or "Лот")
    asset_text = str(values.get("asset_text") or title)
    cadastral = _CADASTRAL_RE.search(asset_text)
    vin = _VIN_RE.search(asset_text)
    area_match = _AREA_RE.search(asset_text)
    area = float(area_match.group(1).replace(",", ".")) if area_match else None
    return NormalizedLot(
        external_id=external_id,
        source="rus-on",
        source_system="rus-on.ru",
        title=title[:500],
        description=text_value[:5000],
        category=str(values.get("category") or "other"),
        region_slug=None,
        region_name=None,
        address=str(values["address"]) if values.get("address") else None,
        cadastral_number=cadastral.group(1) if cadastral else None,
        vin=vin.group(0).upper() if vin else None,
        area=area,
        start_price=values.get("start_price"),
        current_price=values.get("current_price"),
        auction_status=_status(str(values.get("status_text") or "")),
        lot_url=detail_url,
        source_url=detail_url,
        detail_level="detail",
        raw_data={
            "complete_source_inventory": complete_source_inventory,
            "safe_for_archival": safe_for_archival,
            "reconcile_missing": False,
            "ruson_initial_price_format": values.get("initial_price_format"),
            "ruson_initial_price_had_currency": values.get("initial_price_had_currency"),
            "ruson_current_price_source": values.get("current_price_source"),
            "ruson_detail_template": values.get("detail_template"),
            "ruson_source_categories": values.get("source_categories"),
            "ruson_public_offer_currency": values.get("public_offer_currency"),
        },
        platform_name=platform_name,
        platform_code=platform_code,
        procedure_number=values.get("procedure_number"),
        deposit_amount=values.get("deposit_amount"),
        application_start_at=values.get("application_start_at"),
        application_deadline=values.get("application_deadline"),
        auction_at=values.get("auction_at"),
        auction_timezone="Europe/Moscow",
        auction_step_amount=values.get("auction_step_amount"),
        auction_type=values.get("auction_type"),
        public_offer_schedule=values.get("public_offer_schedule"),
        next_interval_price=values.get("next_interval_price"),
        next_price_reduction_at=values.get("next_price_reduction_at"),
    )



def enrich_ruson_lot(lot: NormalizedLot, detail: NormalizedLot) -> NormalizedLot:
    """Copy verified RUSSIA OnLine lifecycle fields after generic identity/price guards."""
    for field_name in (
        "deposit_amount", "application_start_at", "auction_timezone",
        "auction_step_amount", "auction_type", "public_offer_schedule",
        "next_interval_price", "next_price_reduction_at",
    ):
        value = getattr(detail, field_name, None)
        if value is not None:
            setattr(lot, field_name, value)
    raw = dict(lot.raw_data or {})
    raw.update({
        key: value for key, value in (detail.raw_data or {}).items()
        if key.startswith("ruson_")
    })
    lot.raw_data = raw
    return lot


class RusonAccessError(RuntimeError):
    """The official public RUSSIA OnLine endpoint refused read-only access."""


class RusonSchemaError(RuntimeError):
    """The observed RUSSIA OnLine page no longer matches the verified contract."""


class RusonPageRangeError(RusonSchemaError):
    """A direct pagenum request is outside the provider's represented page range."""


@dataclass(frozen=True, slots=True)
class RusonCatalogueItem:
    external_id: str
    detail_url: str
    title: str


@dataclass(slots=True)
class RusonCatalogueResult:
    items: list[RusonCatalogueItem]
    next_cursor: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RusonNextRequest:
    method: str
    url: str
    fields: tuple[tuple[str, str], ...]
    target_page: int


def _parse_soup(html: str | bytes) -> BeautifulSoup:
    if isinstance(html, bytes):
        try:
            decoded = html.decode("utf-8")
        except UnicodeDecodeError:
            probe = BeautifulSoup(html, "lxml")
            decoded = html.decode(probe.original_encoding or "windows-1251", errors="replace")
        return BeautifulSoup(decoded, "html.parser")
    return BeautifulSoup(html, "html.parser")


def _safe_source_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "rus-on.ru"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        raise RusonAccessError("rus-on.ru: refused unsafe provider URL")
    return value


def _lot_identity_from_url(detail_url: str) -> str:
    safe_url = _safe_source_url(detail_url)
    match = LOT_URL_RE.search(safe_url)
    if not match:
        raise RusonSchemaError("rus-on.ru: detail URL does not match lot_nid contract")
    return match.group(1)


def verify_identity(external_id: str, detail_url: str) -> str:
    match = re.fullmatch(r"rus-on\.ru:(\d+)", external_id)
    if not match:
        raise RusonSchemaError("rus-on.ru: invalid external ID")
    identity = match.group(1)
    if _lot_identity_from_url(detail_url) != identity:
        raise RusonSchemaError("rus-on.ru: catalogue/detail lot_nid identity mismatch")
    return identity


def parse_detail_html(
    html: str | bytes,
    *,
    external_id: str,
    detail_url: str | None = None,
    observed_at_utc: datetime | None = None,
) -> NormalizedLot:
    identity = re.fullmatch(r"rus-on\.ru:(\d+)", external_id)
    if not identity:
        raise RusonSchemaError("rus-on.ru: invalid external ID")
    canonical_url = detail_url or DETAIL_URL_TEMPLATE.format(identity=identity.group(1))
    verify_identity(external_id, canonical_url)
    soup = _parse_soup(html)
    text_value = _clean(soup.get_text(" ", strip=True))
    if len(text_value) < 80:
        raise RusonSchemaError("rus-on.ru: detail page has insufficient content")
    lot = build_ruson_detail_lot(
        soup,
        external_id=external_id,
        detail_url=canonical_url,
        platform_name=PLATFORM_NAME,
        platform_code=PLATFORM_CODE,
        observed_at_utc=observed_at_utc,
        complete_source_inventory=False,
        safe_for_archival=False,
    )
    if (
        lot.start_price is None
        and lot.auction_status == "unknown"
        and lot.application_start_at is None
        and lot.application_deadline is None
        and lot.auction_at is None
        and not lot.public_offer_schedule
    ):
        raise RusonSchemaError("rus-on.ru: detail page has no proven lot-level fields")
    return lot


def _catalogue_title(anchor: Tag) -> str:
    title = _clean(anchor.get_text(" ", strip=True)) or _clean(str(anchor.get("title") or ""))
    container = anchor.find_parent("tr") or anchor.find_parent("article") or anchor.find_parent("li")
    if isinstance(container, Tag):
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in container.select("td")]
        candidates = [value for value in cells if len(value) >= 4 and not value.isdigit()]
        if candidates:
            title = max(candidates, key=len)
    return title or "Лот"


def parse_catalogue_page(html: str | bytes, *, page: int = 1) -> RusonCatalogueResult:
    if page < 1:
        raise ValueError("page must be >= 1")
    soup = _parse_soup(html)
    items: list[RusonCatalogueItem] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = urljoin(LISTING_URL, str(anchor.get("href") or "").strip())
        try:
            identity = _lot_identity_from_url(href)
        except (RusonAccessError, RusonSchemaError):
            continue
        external_id = f"{SOURCE_ID}:{identity}"
        if external_id in seen:
            continue
        seen.add(external_id)
        items.append(RusonCatalogueItem(
            external_id=external_id,
            detail_url=href,
            title=_catalogue_title(anchor)[:500],
        ))
    next_page = parse_next_page(html, page=page)
    if next_page is not None and next_page != page + 1:
        raise RusonSchemaError(
            f"rus-on.ru: non-sequential pagination target {next_page} after page {page}"
        )
    return RusonCatalogueResult(
        items=items,
        next_cursor=str(next_page) if next_page is not None else None,
        metadata={
            "source": SOURCE_ID,
            "page": page,
            "items_on_page": len(items),
            "terminal_page": next_page is None,
            "complete_source_inventory": False,
            "safe_for_archival": False,
            "reconcile_missing": False,
        },
    )


def pagination_targets(html: str | bytes) -> set[int]:
    soup = _parse_soup(html)
    targets: set[int] = set()
    for node in soup.select("[onclick]"):
        onclick = str(node.get("onclick") or "")
        match = re.search(r"pagenum_send\(\s*(\d+)\s*\)", onclick)
        if match:
            targets.add(int(match.group(1)))
    return targets


def parse_next_page(html: str | bytes, *, page: int) -> int | None:
    targets = pagination_targets(html)
    higher = sorted(target for target in targets if target > page)
    return higher[0] if higher else None


def is_terminal_page(html: str | bytes, *, page: int) -> bool:
    return parse_next_page(html, page=page) is None


def _form_fields(form: Tag, target_page: int) -> tuple[tuple[str, str], ...]:
    fields: list[tuple[str, str]] = []
    page_seen = False
    for node in form.select("input[name]"):
        field_type = str(node.get("type") or "").lower()
        if field_type in {"submit", "button", "image", "file"}:
            continue
        if field_type in {"checkbox", "radio"} and not node.has_attr("checked"):
            continue
        name = str(node.get("name") or "")
        if not name:
            continue
        value = str(node.get("value") or "")
        if name == PAGE_FIELD:
            value = str(target_page)
            page_seen = True
        fields.append((name, value))
    for select in form.select("select[name]"):
        name = str(select.get("name") or "")
        if not name:
            continue
        options = select.select("option[selected]") or select.select("option")[:1]
        for option in options:
            fields.append((name, str(option.get("value") or option.get_text(" ", strip=True))))
    if not page_seen:
        fields.append((PAGE_FIELD, str(target_page)))
    return tuple(fields)


def build_page_request(html: str | bytes, *, target_page: int) -> RusonNextRequest:
    if target_page < 2:
        raise ValueError("target_page must be >= 2")
    soup = _parse_soup(html)
    form = soup.find("form", id=FORM_ID)
    if not isinstance(form, Tag):
        raise RusonSchemaError("rus-on.ru: pagination form disappeared")
    method = str(form.get("method") or "get").strip().lower()
    if method not in {"get", "post"}:
        raise RusonSchemaError("rus-on.ru: unsupported pagination form method")
    action = _safe_source_url(urljoin(LISTING_URL, str(form.get("action") or LISTING_URL)))
    if method == "get":
        parsed = urlparse(action)
        action = parsed._replace(query="", fragment="").geturl()
    return RusonNextRequest(
        method=method,
        url=action,
        fields=_form_fields(form, target_page),
        target_page=target_page,
    )


def build_next_request(html: str | bytes, *, page: int) -> RusonNextRequest:
    target_page = parse_next_page(html, page=page)
    if target_page is None:
        raise RusonSchemaError(f"rus-on.ru: page {page} is terminal")
    if target_page != page + 1:
        raise RusonSchemaError("rus-on.ru: pagination skipped a sequential page")
    return build_page_request(html, target_page=target_page)


def pagination_shape(html: str | bytes) -> dict[str, Any]:
    soup = _parse_soup(html)
    form = soup.find("form", id=FORM_ID)
    controls: list[dict[str, Any]] = []
    for node in soup.select("[onclick]"):
        onclick = str(node.get("onclick") or "")
        match = re.search(r"pagenum_send\(\s*(\d+)\s*\)", onclick)
        if match:
            controls.append({
                "text": _clean(node.get_text(" ", strip=True))[:80],
                "page": int(match.group(1)),
            })
    page_input = form.find("input", attrs={"name": PAGE_FIELD}) if isinstance(form, Tag) else None
    return {
        "form_found": isinstance(form, Tag),
        "form_method": str(form.get("method") or "get").lower() if isinstance(form, Tag) else None,
        "form_action": str(form.get("action") or "") if isinstance(form, Tag) else None,
        "pagenum_value": str(page_input.get("value") or "") if isinstance(page_input, Tag) else None,
        "pagination_controls": controls,
        "control_count": len(controls),
    }


class RusonReadonlyConnector:
    """Standalone read-only source adapter used for proof before integration wiring."""

    RETRY_DELAYS_SECONDS = (1.0, 2.0, 4.0)

    def __init__(self, *, session: requests.Session | None = None, timeout: float = 25.0) -> None:
        self.source_id = SOURCE_ID
        self.session = session or requests.Session()
        self.timeout = timeout
        self._last_response: requests.Response | None = None
        self._last_page = 0
        self._page_fingerprints: dict[int, str] = {}
        self.session.headers.setdefault(
            "User-Agent",
            "TORGI/1.0 (+https://github.com/Maximiliamos/torgi; public read-only connector)",
        )

    def _check_response(self, response: requests.Response) -> requests.Response:
        status = int(getattr(response, "status_code", 0) or 0)
        if 300 <= status < 400:
            raise RusonAccessError("rus-on.ru: provider redirect was refused")
        if status in {401, 403, 429}:
            raise RusonAccessError(f"rus-on.ru: provider returned HTTP {status}")
        response.raise_for_status()
        response_url = str(getattr(response, "url", "") or "")
        if response_url:
            _safe_source_url(response_url)
        return response

    def _read(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        response = request_with_retries(
            self.session,
            method,
            _safe_source_url(url),
            delays=self.RETRY_DELAYS_SECONDS,
            **kwargs,
        )
        return self._check_response(response)

    def _first_page(self) -> requests.Response:
        response = self._read(
            "get", LISTING_URL, timeout=self.timeout, allow_redirects=False
        )
        self._last_response = response
        self._last_page = 1
        return response

    def _request_page(self, page: int) -> requests.Response:
        if page < 1:
            raise ValueError("page must be >= 1")
        if page == 1:
            return self._first_page()

        if self._last_response is not None and self._last_page == page - 1:
            request = build_next_request(self._last_response.content, page=self._last_page)
        else:
            template = self._first_page()
            request = build_page_request(template.content, target_page=page)

        kwargs: dict[str, Any] = {
            "timeout": self.timeout,
            "allow_redirects": False,
        }
        if request.method == "get":
            kwargs["params"] = list(request.fields)
        else:
            kwargs["data"] = list(request.fields)
        response = self._read(request.method, request.url, **kwargs)
        self._last_response = response
        self._last_page = page
        return response

    async def search(self, filters: Any = None, cursor: str | None = None) -> RusonCatalogueResult:
        page = 1
        if cursor is not None:
            page = int(cursor)
        elif isinstance(filters, dict) and filters.get("page") is not None:
            page = int(filters["page"])
        response = await asyncio.to_thread(self._request_page, page)
        fingerprint = hashlib.sha256(response.content).hexdigest()
        if any(
            old == fingerprint
            for number, old in self._page_fingerprints.items()
            if number < page
        ):
            raise RusonPageRangeError(
                f"rus-on.ru: repeated catalogue page content at page {page}"
            )
        self._page_fingerprints[page] = fingerprint
        parsed = parse_catalogue_page(response.content, page=page)
        if not parsed.items:
            error_type = RusonPageRangeError if page > 1 else RusonSchemaError
            raise error_type(
                f"rus-on.ru: no lot_nid links matched the catalogue on page {page}"
            )
        parsed.metadata["response_url"] = str(getattr(response, "url", "") or LISTING_URL)
        return parsed

    async def fetch_lot(self, external_id: str) -> NormalizedLot:
        match = re.fullmatch(r"rus-on\.ru:(\d+)", external_id)
        if not match:
            raise RusonSchemaError("rus-on.ru: invalid external ID")
        detail_url = DETAIL_URL_TEMPLATE.format(identity=match.group(1))
        response = await asyncio.to_thread(
            self._read,
            "get",
            detail_url,
            timeout=self.timeout,
            allow_redirects=False,
        )
        return parse_detail_html(
            response.content,
            external_id=external_id,
            detail_url=detail_url,
        )

    def inspect_pagination_shape(self) -> dict[str, Any]:
        response = self._first_page()
        result = pagination_shape(response.content)
        result["http_status"] = int(response.status_code)
        result["response_url"] = str(getattr(response, "url", "") or LISTING_URL)
        return result
