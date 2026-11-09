"""Vertrades (arest.vertrades.ru) fail-closed source contract.

All Vertrades-specific catalogue, pagination, identity, per-lot detail, price,
status and enrichment rules live here so the source can be reviewed without
changing shared ETP parser code. Production wiring is intentionally external.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urljoin, urlparse, urlsplit

from bs4 import BeautifulSoup

from bankrotai.domain import NormalizedLot

if TYPE_CHECKING:
    from bankrotai.connectors.etp_sources.common import HtmlEtpConnector

SOURCE_ID = "vertrades.ru"
CATALOGUE_URL = "https://arest.vertrades.ru/bidding"
DETAIL_HOST = "arest.vertrades.ru"
_MAX_HTML = 2_000_000
_PROPERTY = re.compile(
    r"(?:недвижим|земельн|участок|квартир|комнат|помещени|здани|"
    r"гараж|машино.?мест|жилой дом|склад|торговый центр|сооружени|"
    r"доля в праве на|апартамент)",
    re.I,
)
_CADASTRE = re.compile(r"(?<!\d)(\d{2}:\d{1,3}:\d{5,9}:\d{1,9})(?!\d)")
_DECIMAL = re.compile(r"^\s*(\d[\d \u00a0\u202f]*)(?:[.,](\d{1,2}))?\s*$")
_TRADE = re.compile(r"^(\d{1,9})\s*[-–—]\s*(АП|АЗ)$", re.I)
_HEADER = re.compile(r"^Сведения\s+по\s+лоту\s*№\s*(\d{1,6})$", re.I)
_PROCEDURE = re.compile(
    r"идентификационный\s+номер\s*:?\s*([0-9]{1,12}\s*[-–—]\s*[А-ЯA-Z]{1,12})",
    re.I,
)
_AREA = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", re.I)
_ADDRESS = re.compile(
    r"Адрес\s*:\s*(.+?)(?=\s+(?:Начальная\s+цена|Задаток|Ограничения|"
    r"Обременения|Собственник|Продается|Продаётся)\s*:|$)",
    re.I,
)
_STATUS_LABEL = re.compile(r"^Статус\s+торгов\s+по\s+лоту\b", re.I)
_TITLE_LABEL = re.compile(
    r"^Краткие\s+сведения\s+об\s+имуществе\s*\(наименование\s+лота\)",
    re.I,
)
_DESCRIPTION_LABEL = re.compile(
    r"^[CС]ведения\s+об\s+арестованном\s+имуществе,\s*реализуемом\s+на\s+торгах",
    re.I,
)
_PRICE_LABEL = re.compile(r"^Начальная\s+цена\s+продажи\s+имущества\b", re.I)
_DEPOSIT_LABEL = re.compile(r"^Размер\s+задатка\b", re.I)
_DATE = r"(\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)"
_FIELD_BOUNDARY = re.compile(
    r"^(?:Валюта|Обременения|Начальная\s+цена\s+продажи\s+имущества|"
    r"Величина\s+повышения\s+начальной\s+цены|Размер\s+задатка|"
    r"Сведения\s+по\s+лоту\s*№)",
    re.I,
)


class VertradesCardError(ValueError):
    """Provider HTML does not prove the requested individual lot."""


@dataclass(frozen=True, slots=True)
class VertradesLotSection:
    external_id: str
    provider_id: str
    procedure_number: str
    lot_number: int
    title: str
    description: str
    initial_price_rub: Decimal
    status_text: str
    deposit_rub: Decimal | None
    address: str | None
    cadastral_number: str | None
    area_sqm: float | None
    application_start_at: datetime | None
    application_deadline: datetime | None
    auction_at: datetime | None


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _safe_url(href: str) -> str:
    absolute = urljoin(CATALOGUE_URL, href.strip())
    parsed = urlsplit(absolute)
    if (
        parsed.scheme != "https"
        or parsed.hostname != DETAIL_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise VertradesCardError("external_or_unsafe_provider_url")
    return absolute


def _catalogue_money(value: str) -> float | None:
    match = _DECIMAL.fullmatch(value.strip())
    if not match:
        return None
    raw = re.sub(r"[\s\u00a0\u202f]", "", match.group(1))
    if match.group(2):
        raw += "." + match.group(2)
    try:
        amount = float(raw)
    except ValueError:
        return None
    return amount if 0 < amount < 1e15 else None


def _provider_id(detail_url: str) -> str:
    parsed = urlparse(detail_url)
    values = parse_qs(parsed.query).get("id", [])
    if (
        parsed.scheme != "https"
        or parsed.hostname != DETAIL_HOST
        or parsed.path != "/showTrades"
        or len(values) != 1
        or not re.fullmatch(r"[1-9]\d{0,11}", values[0])
    ):
        raise VertradesCardError("invalid_shared_procedure_detail_url")
    return values[0]


def _vertrades_status(value: str) -> str:
    """Normalize only source statuses that have explicit evidence."""
    from bankrotai.connectors.etp_sources.status_normalization import _status

    normalized = _status(value)
    if normalized != "unknown":
        return normalized
    if re.search(r"торги\s+по\s+лоту\s+отмен[её]н", value, re.I):
        return "closed"
    if re.search(r"заверш[её]нн?ые\s+торги", value, re.I):
        return "closed"
    if re.search(r"торги\s+заверш[её]н", value, re.I):
        return "closed"
    return "unknown"


def catalogue_page_url(page: int) -> str:
    if page < 1:
        raise ValueError("Vertrades page must be >= 1")
    return CATALOGUE_URL if page == 1 else f"{CATALOGUE_URL}?page={page}"


def has_next_catalogue_page(html: str | bytes, page: int) -> bool:
    """Treat only a same-origin explicit page+1 control as proof of continuation."""
    if page < 1:
        raise ValueError("Vertrades page must be >= 1")
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.select("a[href]"):
        try:
            url = _safe_url(str(anchor.get("href") or ""))
        except VertradesCardError:
            continue
        parsed = urlparse(url)
        if parsed.path != "/bidding":
            continue
        values = parse_qs(parsed.query).get("page", [])
        if len(values) == 1 and values[0].isdigit() and int(values[0]) == page + 1:
            return True
    return False


def parse_filter_form_contract(html: str | bytes) -> dict[str, object]:
    """Return structural form metadata only; never field values."""
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for form in soup.find_all("form"):
        text = " ".join(form.stripped_strings).lower()
        if "№ торгов" in text or "наименование лота" in text:
            candidates.append(form)
    if len(candidates) != 1:
        raise VertradesCardError("catalogue_filter_form_missing_or_ambiguous")
    form = candidates[0]
    method = str(form.get("method") or "get").strip().lower()
    if method not in {"get", "post"}:
        raise VertradesCardError("catalogue_filter_form_method_unexpected")
    action = _safe_url(str(form.get("action") or "/bidding"))
    parsed = urlparse(action)
    if parsed.path != "/bidding":
        raise VertradesCardError("catalogue_filter_form_action_unexpected")
    names = sorted({
        str(node.get("name")).strip()
        for node in form.select("[name]")
        if str(node.get("name") or "").strip()
    })
    if not names:
        raise VertradesCardError("catalogue_filter_form_has_no_named_fields")
    return {"method": method, "action_path": parsed.path, "field_names": names}


def parse_catalogue_page(
    html: str | bytes, *, page: int = 1, limit: int = 100,
) -> list[NormalizedLot]:
    """Parse property rows from the mixed official arrested-property register."""
    if not 1 <= limit <= 100:
        raise ValueError("Vertrades catalogue limit must be 1..100")
    if page < 1:
        raise ValueError("Vertrades page must be >= 1")
    soup = BeautifulSoup(html, "html.parser")
    lots: list[NormalizedLot] = []
    seen: set[str] = set()
    for tr in soup.select("tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 10:
            continue
        trade_text = _clean(cells[0].get_text(" ", strip=True))
        lot_text = _clean(cells[1].get_text(" ", strip=True))
        title = _clean(cells[3].get_text(" ", strip=True))
        trade_match = _TRADE.fullmatch(trade_text)
        if not trade_match or not re.fullmatch(r"[1-9]\d{0,4}", lot_text):
            continue
        if not _PROPERTY.search(title):
            continue
        links = cells[3].select("a[href]") + cells[0].select("a[href]")
        detail_url = None
        provider_id = None
        for anchor in links:
            try:
                candidate = _safe_url(str(anchor.get("href") or ""))
                candidate_provider = _provider_id(candidate)
            except VertradesCardError:
                continue
            detail_url = candidate
            provider_id = candidate_provider
            break
        if not detail_url or not provider_id:
            continue
        trade = f"{trade_match.group(1)}-{trade_match.group(2).upper()}"
        external_id = f"{SOURCE_ID}:{provider_id}:{int(lot_text)}"
        if external_id in seen:
            raise VertradesCardError("duplicate_individual_lot_identity_on_page")
        seen.add(external_id)
        price = _catalogue_money(cells[4].get_text(" ", strip=True))
        if price is None:
            raise VertradesCardError("missing_or_invalid_catalogue_initial_price")
        source_status = _clean(cells[-1].get_text(" ", strip=True))
        normalized_status = _vertrades_status(source_status)
        cadastre = _CADASTRE.search(title)
        from bankrotai.connectors.etp_sources.common import _category
        lots.append(NormalizedLot(
            external_id=external_id,
            source="vertrades",
            source_system=SOURCE_ID,
            title=title[:500],
            description=title[:5000],
            category=_category(title),
            region_slug=None,
            region_name=None,
            address=None,
            cadastral_number=cadastre.group(1) if cadastre else None,
            vin=None,
            area=None,
            start_price=price,
            current_price=None,
            auction_status=normalized_status,
            lot_url=detail_url,
            source_url=detail_url,
            detail_level="search",
            raw_data={
                "listing_page": page,
                "trade_id": trade,
                "lot_number": str(int(lot_text)),
                "catalogue_source_status_text": source_status,
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
            },
            platform_name="Вердиктъ",
            platform_code="vertrades",
        ))
        if len(lots) >= limit:
            break
    return lots


def _lines(html: str | bytes) -> list[str]:
    if len(html) > _MAX_HTML:
        raise VertradesCardError("vertrades_detail_page_too_large")
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style"]):
        node.decompose()
    return [_clean(x) for x in soup.get_text("\n").splitlines() if _clean(x)]


def _one_labeled(
    lines: list[str], label: re.Pattern[str], *, required: bool,
    multiline: bool = False,
) -> str | None:
    values: list[str] = []
    for index, line in enumerate(lines):
        match = label.match(line)
        if not match:
            continue
        inline = _clean(line[match.end():].lstrip(" :-–—"))
        value = inline
        cursor = index + 1
        if not value and cursor < len(lines):
            value = lines[cursor]
            cursor += 1
        if value and multiline:
            parts = [value]
            while cursor < len(lines) and not _FIELD_BOUNDARY.match(lines[cursor]):
                parts.append(lines[cursor])
                cursor += 1
            value = _clean(" ".join(parts))
        if value:
            values.append(_clean(value))
    unique = list(dict.fromkeys(values))
    if len(unique) > 1:
        raise VertradesCardError("ambiguous_individual_lot_field")
    if not unique:
        if required:
            raise VertradesCardError("missing_individual_lot_field")
        return None
    return unique[0]


def _money(value: str | None, *, required: bool) -> Decimal | None:
    if not value:
        if required:
            raise VertradesCardError("missing_individual_lot_money")
        return None
    match = re.search(r"(?<!\d)(\d[\d\s\u00a0\u202f]*)(?:[.,](\d{1,2}))?(?!\d)", value)
    if not match:
        if required:
            raise VertradesCardError("invalid_individual_lot_money")
        return None
    whole = re.sub(r"[\s\u00a0\u202f]", "", match.group(1))
    cents = (match.group(2) or "0").ljust(2, "0")
    try:
        amount = Decimal(f"{whole}.{cents}")
    except InvalidOperation as exc:
        raise VertradesCardError("invalid_individual_lot_money") from exc
    if not amount.is_finite() or not (Decimal("0") < amount < Decimal("1e15")):
        raise VertradesCardError("invalid_individual_lot_money")
    return amount


def _datetime_after(text: str, label: str) -> datetime | None:
    match = re.search(label + r"\s*:?\s*" + _DATE, text, re.I)
    if not match:
        return None
    raw = match.group(1)
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _procedure_number(text: str) -> str:
    found = [
        re.sub(r"[-–—]", "-", _clean(m.group(1)).replace(" ", "")).upper()
        for m in _PROCEDURE.finditer(text)
    ]
    found = list(dict.fromkeys(found))
    if len(found) != 1:
        raise VertradesCardError("missing_or_ambiguous_procedure_number")
    return found[0]


def get_vertrades_lot(
    html: str | bytes, *, provider_id: str, lot_number: int,
) -> VertradesLotSection:
    if not re.fullmatch(r"[1-9]\d{0,11}", provider_id):
        raise VertradesCardError("invalid_provider_id")
    if not 1 <= lot_number <= 999_999:
        raise VertradesCardError("invalid_lot_number")

    lines = _lines(html)
    full_text = " ".join(lines)
    procedure = _procedure_number(full_text)
    headings = [
        (idx, int(match.group(1)))
        for idx, line in enumerate(lines)
        if (match := _HEADER.fullmatch(line))
    ]
    if not headings:
        raise VertradesCardError("no_numbered_lot_sections")
    counts: dict[int, int] = {}
    for _, number in headings:
        counts[number] = counts.get(number, 0) + 1
    if counts.get(lot_number, 0) != 1:
        raise VertradesCardError(
            "duplicate_requested_lot_section"
            if counts.get(lot_number, 0) > 1
            else "requested_lot_missing_in_trade_card"
        )

    target_index = next(i for i, (_, number) in enumerate(headings) if number == lot_number)
    start = headings[target_index][0]
    end = headings[target_index + 1][0] if target_index + 1 < len(headings) else len(lines)
    section = lines[start:end]

    status = _one_labeled(section, _STATUS_LABEL, required=True)
    title = _one_labeled(section, _TITLE_LABEL, required=True)
    description = _one_labeled(
        section, _DESCRIPTION_LABEL, required=False, multiline=True,
    ) or title
    price = _money(_one_labeled(section, _PRICE_LABEL, required=True), required=True)
    deposit = _money(_one_labeled(section, _DEPOSIT_LABEL, required=False), required=False)
    assert status is not None and title is not None and price is not None

    asset_text = _clean(title + " " + description)
    cadastre = _CADASTRE.search(asset_text)
    area = _AREA.search(asset_text)
    address = _ADDRESS.search(description)

    return VertradesLotSection(
        external_id=f"{SOURCE_ID}:{provider_id}:{lot_number}",
        provider_id=provider_id,
        procedure_number=procedure,
        lot_number=lot_number,
        title=title[:500],
        description=description[:5000],
        initial_price_rub=price,
        status_text=status[:120],
        deposit_rub=deposit,
        address=_clean(address.group(1))[:500] if address else None,
        cadastral_number=cadastre.group(1) if cadastre else None,
        area_sqm=float(area.group(1).replace(",", ".")) if area else None,
        application_start_at=_datetime_after(
            full_text, r"Начало\s+предоставления\s+заявок\s+на\s+участие",
        ),
        application_deadline=_datetime_after(
            full_text, r"Окончание\s+предоставления\s+заявок\s+на\s+участие",
        ),
        auction_at=_datetime_after(
            full_text, r"Начало\s+подачи\s+предложений\s+о\s+цене\s+имущества",
        ),
    )


async def fetch_vertrades_lot(
    connector: "HtmlEtpConnector", external_id: str,
) -> NormalizedLot:
    from bankrotai.connectors.etp_sources.common import (
        SourceSchemaError, _category, _safe_provider_url,
    )

    match = re.fullmatch(r"vertrades\.ru:([1-9]\d{0,11}):([1-9]\d{0,5})", external_id)
    if not match:
        raise SourceSchemaError("vertrades.ru: invalid per-lot external_id")
    provider_id, lot_text = match.groups()
    detail_url = _safe_provider_url(
        connector.profile, f"https://arest.vertrades.ru/showTrades?id={provider_id}",
    )
    response = await asyncio.to_thread(
        connector._read_request, "get", detail_url,
        timeout=connector.timeout, allow_redirects=False,
    )
    try:
        section = get_vertrades_lot(
            response.content, provider_id=provider_id, lot_number=int(lot_text),
        )
    except ValueError as exc:
        raise SourceSchemaError(f"vertrades.ru: {exc}") from exc
    if section.external_id != external_id:
        raise SourceSchemaError("vertrades.ru: fetched individual lot identity mismatch")
    normalized_status = _vertrades_status(section.status_text)
    if normalized_status == "unknown":
        raise SourceSchemaError("vertrades.ru: unrecognized explicit lot status")
    return NormalizedLot(
        external_id=section.external_id,
        source="vertrades",
        source_system=SOURCE_ID,
        title=section.title,
        description=section.description,
        category=_category(section.title + " " + section.description),
        region_slug=None,
        region_name=None,
        address=section.address,
        cadastral_number=section.cadastral_number,
        vin=None,
        area=section.area_sqm,
        start_price=float(section.initial_price_rub),
        current_price=None,
        auction_status=normalized_status,
        lot_url=detail_url,
        source_url=detail_url,
        detail_level="detail",
        raw_data={
            "provider_procedure_id": section.provider_id,
            "trade_id": section.procedure_number,
            "lot_number": str(section.lot_number),
            "individual_lot_identity_verified": True,
            "detail_start_price_verified": True,
            "detail_status_verified": True,
            "detail_dates_verified": (
                section.application_deadline is not None or section.auction_at is not None
            ),
            "complete_source_inventory": False,
            "safe_for_archival": False,
            "reconcile_missing": False,
        },
        platform_name=connector.profile.platform_name,
        platform_code=connector.profile.platform_code,
        procedure_number=section.procedure_number,
        deposit_amount=float(section.deposit_rub) if section.deposit_rub is not None else None,
        application_start_at=section.application_start_at,
        application_deadline=section.application_deadline,
        auction_at=section.auction_at,
    )


async def enrich_vertrades_lot(
    connector: "HtmlEtpConnector", listing: NormalizedLot,
) -> NormalizedLot:
    from bankrotai.connectors.etp_sources.common import SourceSchemaError
    from bankrotai.connectors.etp_sources.source_specific import apply_detail

    if listing.source_system != SOURCE_ID:
        raise SourceSchemaError("vertrades.ru: cross-source listing cannot be enriched")
    detail = await fetch_vertrades_lot(connector, listing.external_id)
    listed_trade = str((listing.raw_data or {}).get("trade_id") or "")
    if listed_trade and listed_trade != detail.procedure_number:
        raise SourceSchemaError("vertrades.ru: procedure code mismatch")
    listed_lot = str((listing.raw_data or {}).get("lot_number") or "")
    if listed_lot and listed_lot != str((detail.raw_data or {}).get("lot_number") or ""):
        raise SourceSchemaError("vertrades.ru: lot number mismatch")
    if listing.cadastral_number and detail.cadastral_number:
        if listing.cadastral_number != detail.cadastral_number:
            raise SourceSchemaError("vertrades.ru: listing and detail cadastre differ")
    if listing.start_price is not None and detail.start_price is not None:
        a = Decimal(str(listing.start_price))
        b = Decimal(str(detail.start_price))
        if not a.is_finite() or not b.is_finite() or abs(a - b) > Decimal("0.01"):
            raise SourceSchemaError("vertrades.ru: listing and individual detail price differ")

    enriched = apply_detail(listing, detail, version=connector.detail_enrichment_version)
    enriched.deposit_amount = detail.deposit_amount
    enriched.application_start_at = detail.application_start_at
    raw = dict(enriched.raw_data or {})
    raw.update(detail.raw_data)
    raw["detail_enrichment_status"] = "success"
    raw["detail_enrichment_version"] = connector.detail_enrichment_version
    enriched.raw_data = raw
    return enriched
