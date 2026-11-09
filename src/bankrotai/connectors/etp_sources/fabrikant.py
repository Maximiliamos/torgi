from __future__ import annotations

"""Fabrikant-specific, fail-closed public HTML contract helpers."""

from dataclasses import dataclass
from datetime import datetime
import asyncio
import re
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from bankrotai.connectors.base import ConnectorPage
if TYPE_CHECKING:
    from bankrotai.domain import NormalizedLot

from bankrotai.connectors.etp_sources.common import (
    EtpSearchFilters,
    HtmlEtpConnector,
    SourceSchemaError,
    _safe_provider_url,
)


CATALOGUE_URL = (
    "https://www.fabrikant.ru/procedure/search/sales"
    "?section_ids%5B%5D=6&page_limit=10"
)
PAGE_PARAMETER = "page_number"

PROCEDURE_PATH_RE = re.compile(r"/v2/trades/procedure/view/([^/?#]+)(?:[/?#]|$)", re.I)
LOT_PATH_RE = re.compile(
    r"/v2/trades/procedure/lot/view/([^/?#]+)/([^/?#]+)(?:[/?#]|$)", re.I
)

_SPACE_RE = re.compile(r"\s+")
_CADASTRAL_RE = re.compile(r"(?<!\d)(\d{2}:\d{2}(?::\d{1,7}){2,4})(?!\d)")
_NUMBER_RE = r"(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)"
_RUBLE_RE = r"(?:(?:российск\w*\s+)?рубл\w*\s*\(RUB\)|₽|руб(?:\.|лей)?)"

_STATUS_RE = re.compile(
    r"лот\s*№\s*\d+\s*\.?.{0,1200}?\s+-\s+"
    r"(при[её]м\s+заявок|закончено(?:\s*\([^)]*\))*|"
    r"отказ\s+организатора(?:\s*\([^)]*\))*|"
    r"приостановлено|рассмотрение\s+заявок(?:\s+и\s+подведение\s+итогов)?)",
    re.I,
)


@dataclass(frozen=True, slots=True)
class FabrikantDetailFields:
    title: str | None
    source_status_text: str | None
    auction_status: str
    start_price: float | None
    current_price: float | None
    category_text: str | None
    category: str
    address: str | None
    cadastral_number: str | None
    area: float | None
    application_deadline: datetime | None
    auction_at: datetime | None


def _clean(value: str) -> str:
    return _SPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()


def _soup(html: str | bytes) -> BeautifulSoup:
    if isinstance(html, bytes):
        raw = html
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            decoded = raw.decode("windows-1251", errors="replace")
    else:
        decoded = html
    return BeautifulSoup(decoded, "html.parser")


def _safe_public_url(base_url: str, href: str) -> str | None:
    url = urljoin(base_url, href)
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"fabrikant.ru", "www.fabrikant.ru"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        return None
    return url



@dataclass(frozen=True, slots=True)
class FabrikantCataloguePage:
    procedure_urls: tuple[str, ...]
    visible_page_numbers: tuple[int, ...]
    selected_controls: tuple[tuple[str, str, str], ...]
    available_filter_options: tuple[tuple[str, str, str, str], ...]
    form_contracts: tuple[tuple[str, str], ...]
    has_next_page: bool
    next_url: str | None
    total_hints: tuple[str, ...]


def _catalogue_selected_controls(
    soup: BeautifulSoup,
) -> tuple[tuple[str, str, str], ...]:
    rows: list[tuple[str, str, str]] = []
    interesting = re.compile(
        r"(?:section|status|state|stage|type|kind|sale|bankrupt|page|limit)",
        re.I,
    )
    for node in soup.select("input[name], select[name]"):
        name = str(node.get("name") or "")
        if not interesting.search(name):
            continue
        if node.name == "input":
            kind = str(node.get("type") or "text").lower()
            if kind in {"checkbox", "radio"} and not node.has_attr("checked"):
                continue
            rows.append((name[:80], str(node.get("value") or "")[:120], kind))
            continue
        selected = node.select_one("option[selected]")
        if selected is not None:
            rows.append(
                (name[:80], str(selected.get("value") or "")[:120], "select")
            )
    return tuple(rows[:30])


def _catalogue_filter_options(
    soup: BeautifulSoup,
) -> tuple[tuple[str, str, str, str], ...]:
    rows: list[tuple[str, str, str, str]] = []
    interesting = re.compile(
        r"(?:section|status|state|stage|type|kind|sale|bankrupt|page|limit)",
        re.I,
    )
    for node in soup.select("input[name], select[name]"):
        name = str(node.get("name") or "")
        if not interesting.search(name):
            continue
        if node.name == "input":
            kind = str(node.get("type") or "text").lower()
            if kind not in {"checkbox", "radio"}:
                continue
            rows.append((
                name[:80],
                str(node.get("value") or "")[:120],
                _clean(str(node.get("title") or ""))[:160],
                kind,
            ))
            continue
        for option in node.select("option"):
            rows.append((
                name[:80],
                str(option.get("value") or "")[:120],
                _clean(option.get_text(" ", strip=True))[:160],
                "select_option",
            ))
            if len(rows) >= 120:
                return tuple(rows)
    return tuple(rows[:120])


def _catalogue_form_contracts(
    soup: BeautifulSoup, *, base_url: str
) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for form in soup.select("form"):
        method = str(form.get("method") or "get").strip().lower()
        action = _safe_public_url(base_url, str(form.get("action") or ""))
        if action is None:
            continue
        item = (method[:16], urlparse(action).path[:200])
        if item not in rows:
            rows.append(item)
        if len(rows) >= 20:
            break
    return tuple(rows)


def build_catalogue_url(page: int) -> str:
    """Build the official GET catalogue URL without changing its source filter."""
    if page <= 1:
        return CATALOGUE_URL
    parsed = urlparse(CATALOGUE_URL)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query[PAGE_PARAMETER] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def parse_catalogue_page(
    html: str | bytes, *, base_url: str, page: int
) -> FabrikantCataloguePage:
    """Parse only Fabrikant procedure candidates and its visible GET paginator."""
    soup = _soup(html)
    procedures = tuple(procedure_urls(html, base_url=base_url))
    visible: set[int] = set()
    next_url: str | None = None

    for anchor in soup.select("a[href]"):
        url = _safe_public_url(base_url, str(anchor.get("href") or ""))
        if not url:
            continue
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for raw in query.get(PAGE_PARAMETER, []):
            if raw.isdigit():
                visible.add(int(raw))
        text = _clean(anchor.get_text(" ", strip=True)).lower()
        if (
            str(page + 1) in query.get(PAGE_PARAMETER, [])
            or text in {"следующая", "далее", ">", "›", "»"}
        ):
            next_url = url

    page_text = _clean(soup.get_text(" ", strip=True))
    hints: list[str] = []
    for pattern in (
        r"(?:найдено|всего|результат\w*)[^\d]{0,30}\d{1,7}",
        r"\d{1,7}[^\d]{0,20}(?:процедур\w*|торг\w*|результат\w*)",
        r"страниц\w*[^\d]{0,20}\d{1,6}",
    ):
        for match in re.finditer(pattern, page_text, re.I):
            value = _clean(match.group(0))
            if value and value not in hints:
                hints.append(value[:140])
            if len(hints) >= 10:
                break

    return FabrikantCataloguePage(
        procedure_urls=procedures,
        visible_page_numbers=tuple(sorted(visible)),
        selected_controls=_catalogue_selected_controls(soup),
        available_filter_options=_catalogue_filter_options(soup),
        form_contracts=_catalogue_form_contracts(soup, base_url=base_url),
        has_next_page=(page + 1 in visible) or next_url is not None,
        next_url=next_url,
        total_hints=tuple(hints),
    )


def procedure_urls(html: str | bytes, *, base_url: str) -> list[str]:
    """Return distinct official procedure cards from the search catalogue."""
    soup = _soup(html)
    result: list[str] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        url = _safe_public_url(base_url, str(anchor.get("href") or ""))
        if not url or not PROCEDURE_PATH_RE.search(urlparse(url).path):
            continue
        normalized = url.split("#", 1)[0]
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def lot_urls(html: str | bytes, *, base_url: str) -> list[str]:
    """Return distinct individual-lot routes exposed by a procedure card."""
    soup = _soup(html)
    result: list[str] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        url = _safe_public_url(base_url, str(anchor.get("href") or ""))
        if not url:
            continue
        match = LOT_PATH_RE.search(urlparse(url).path)
        if not match:
            continue
        normalized = (
            f"https://www.fabrikant.ru/v2/trades/procedure/lot/view/"
            f"{match.group(1)}/{match.group(2)}"
        )
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def lot_identity(url: str) -> tuple[str, str] | None:
    match = LOT_PATH_RE.search(urlparse(url).path)
    if not match:
        return None
    return match.group(1), match.group(2)


def external_id_from_lot_url(url: str) -> str | None:
    identity = lot_identity(url)
    if identity is None:
        return None
    return f"fabrikant.ru:{identity[0]}:{identity[1]}"


def _money_number(raw: str) -> float | None:
    cleaned = raw.replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    return amount if 0 < amount < 1e15 else None


def _label_segment(text: str, label: str, stops: tuple[str, ...], limit: int = 800) -> str | None:
    match = re.search(label, text, re.I)
    if not match:
        return None
    tail = text[match.end():match.end() + limit]
    stop_positions = []
    for stop in stops:
        stop_match = re.search(stop, tail, re.I)
        if stop_match:
            stop_positions.append(stop_match.start())
    if stop_positions:
        tail = tail[:min(stop_positions)]
    value = _clean(tail).strip(" :-")
    return value or None


def fabrikant_initial_money(text: str) -> float | None:
    """Read only an explicitly labelled lot initial price.

    Deposits, auction steps and public-offer stage values are never used as
    fallback initial prices.
    """
    preferred = re.search(
        r"(?:начальная\s+цена\s+(?:предмета\s+договора|договора)|"
        r"начальная\s+цена\s+лота)"
        r"(?:(?!размер\s+задатка|шаг\s+аукциона|этап\s+понижения).){0,180}?"
        + _NUMBER_RE + r"\s*" + _RUBLE_RE,
        text,
        re.I,
    )
    if preferred:
        return _money_number(preferred.group(1))

    # Some bankruptcy templates label the top-level field simply
    # "Начальная цена". Use it only when it occurs before an explicit public-
    # offer schedule and can be tied to a RUB currency marker.
    prefix = text.split("Этап понижения", 1)[0]
    matches = list(re.finditer(
        r"начальная\s+цена\b"
        r"(?:(?!размер\s+задатка|шаг\s+аукциона).){0,160}?"
        + _NUMBER_RE + r"\s*" + _RUBLE_RE,
        prefix,
        re.I,
    ))
    if len(matches) == 1:
        return _money_number(matches[0].group(1))
    return None


def fabrikant_current_money(text: str) -> float | None:
    match = re.search(
        r"текущая\s+цена\b"
        r"(?:(?!размер\s+задатка|шаг\s+аукциона).){0,120}?"
        + _NUMBER_RE + r"\s*" + _RUBLE_RE,
        text,
        re.I,
    )
    return _money_number(match.group(1)) if match else None


def fabrikant_status(text: str) -> tuple[str, str | None]:
    match = _STATUS_RE.search(text)
    if not match:
        return "unknown", None
    raw = _clean(match.group(1))
    lowered = raw.lower()
    if re.search(r"при[её]м\s+заявок", lowered):
        return "active", raw
    if "приостанов" in lowered:
        return "paused", raw
    if "закончено" in lowered or "отказ организатора" in lowered:
        return "closed", raw
    # "Рассмотрение заявок" means application intake ended, but does not by
    # itself prove that the auction/procedure is terminal.
    return "unknown", raw


def _parse_date_after(text: str, label: str) -> datetime | None:
    match = re.search(
        label + r"[^\d]{0,80}(\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?)",
        text,
        re.I,
    )
    if not match:
        return None
    raw = match.group(1)
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def _structured_category(text: str) -> str | None:
    value = _label_segment(
        text,
        r"категория\s+объекта",
        (
            r"коды?\s+окпд",
            r"классификатор\s+имущества",
            r"количество\s+по\s+океи",
            r"местонахождение\s+объекта",
            r"начальная\s+цена",
        ),
        limit=300,
    )
    return value[:200] if value else None


def normalize_category(category_text: str | None, text: str) -> str:
    value = (category_text or "") + " " + text[:1200]
    lowered = value.lower()
    if any(token in lowered for token in (
        "земельн", "участок", "земля",
    )):
        return "land"
    if any(token in lowered for token in (
        "квартир", "комнат", "жилое помещ", "нежилое помещ", "здани",
        "сооружен", "машино-мест", "гараж", "недвижим",
    )):
        return "real_estate"
    if any(token in lowered for token in (
        "легковые автомобили", "грузовые автомобили", "автомоб", "транспорт",
    )):
        return "vehicle"
    if any(token in lowered for token in ("право требования", "дебитор")):
        return "receivables"
    return "other"


def parse_detail_fields(html: str | bytes) -> FabrikantDetailFields:
    soup = _soup(html)
    text = _clean(soup.get_text(" ", strip=True))

    title = _label_segment(
        text,
        r"предмет\s+договора",
        (
            r"категория\s+объекта",
            r"характеристики\s+объекта",
            r"коды?\s+окпд",
            r"классификатор\s+имущества",
            r"местонахождение\s+объекта",
            r"начальная\s+цена",
        ),
        limit=1500,
    )
    if not title:
        heading = re.search(
            r"лот\s*№\s*\d+\s*\.\s*(.{8,800}?)\s+-\s+"
            r"(?:при[её]м\s+заявок|закончено|отказ\s+организатора|"
            r"приостановлено|рассмотрение\s+заявок)",
            text,
            re.I,
        )
        title = _clean(heading.group(1)) if heading else None

    status, status_text = fabrikant_status(text)
    category_text = _structured_category(text)
    cadastral = _CADASTRAL_RE.search(text)

    area_match = re.search(
        r"(?:общая\s+площадь(?:,?\s*м[²2])?|площадь(?:,?\s*м[²2])?)"
        r"\s*[:=-]?\s*(\d+(?:[.,]\d+)?)",
        text,
        re.I,
    )
    if not area_match:
        area_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", text, re.I)
    area = float(area_match.group(1).replace(",", ".")) if area_match else None

    address = None
    address_match = re.search(
        r"\bадрес\s*:\s*(.{5,500}?)(?=\s+(?:начальная\s+цена|"
        r"категория\s+объекта|коды?\s+окпд|характеристики\s+объекта|"
        r"задаток|дата\s+и\s+время|$))",
        text,
        re.I,
    )
    if address_match:
        address = _clean(address_match.group(1))

    return FabrikantDetailFields(
        title=title[:500] if title else None,
        source_status_text=status_text,
        auction_status=status,
        start_price=fabrikant_initial_money(text),
        current_price=fabrikant_current_money(text),
        category_text=category_text,
        category=normalize_category(category_text, text),
        address=address,
        cadastral_number=cadastral.group(1) if cadastral else None,
        area=area,
        application_deadline=_parse_date_after(
            text, r"дата\s+и\s+время\s+окончания\s+при[её]ма\s+заявок"
        ),
        auction_at=_parse_date_after(
            text, r"дата\s+и\s+время\s+начала\s+аукциона"
        ),
    )


class FabrikantContractError(ValueError):
    """Public Fabrikant HTML no longer proves the expected lot contract."""


def parse_detail_lot(
    profile: Any,
    html: str | bytes,
    *,
    detail_url: str,
    external_id: str | None = None,
) -> NormalizedLot:
    from bankrotai.domain import NormalizedLot

    identity = lot_identity(detail_url)
    if identity is None:
        raise FabrikantContractError("detail URL is not an individual lot route")
    expected_id = f"fabrikant.ru:{identity[0]}:{identity[1]}"
    if external_id is not None and external_id != expected_id:
        raise FabrikantContractError("detail lot identity mismatch")
    fields = parse_detail_fields(html)
    if not fields.title:
        raise FabrikantContractError("individual detail title is not proven")
    text = _clean(_soup(html).get_text(" ", strip=True))
    return NormalizedLot(
        external_id=external_id or expected_id,
        source="fabrikant",
        source_system=profile.source_id,
        title=fields.title,
        description=text[:5000],
        category=fields.category,
        region_slug=None,
        region_name=None,
        address=fields.address,
        cadastral_number=fields.cadastral_number,
        vin=None,
        area=fields.area,
        start_price=fields.start_price,
        current_price=fields.current_price,
        auction_status=fields.auction_status,
        lot_url=detail_url,
        source_url=detail_url,
        detail_level="detail",
        raw_data={
            "complete_source_inventory": False,
            "safe_for_archival": False,
            "reconcile_missing": False,
            "individual_lot_identity_verified": True,
            "fabrikant_category_text": fields.category_text,
            "fabrikant_source_status_text": fields.source_status_text,
            "detail_start_price_verified": fields.start_price is not None,
            "detail_current_price_verified": fields.current_price is not None,
            "detail_status_verified": fields.auction_status != "unknown",
        },
        platform_name=profile.platform_name,
        platform_code=profile.platform_code,
        application_deadline=fields.application_deadline,
        auction_at=fields.auction_at,
    )


def resolve_catalogue_page_lots(
    profile: Any,
    response: requests.Response,
    *,
    page: int,
    read_get: Callable[[str], requests.Response],
) -> list[NormalizedLot]:
    """Resolve every procedure card to canonical individual lot details."""
    procedures = procedure_urls(
        response.content, base_url=response.url or profile.listing_url
    )
    if not procedures:
        raise FabrikantContractError(
            "no procedure cards matched the verified catalogue schema"
        )

    lots: list[NormalizedLot] = []
    seen: set[str] = set()
    unresolved: list[str] = []
    for procedure_url in procedures:
        procedure_response = read_get(procedure_url)
        urls = lot_urls(
            procedure_response.content,
            base_url=procedure_response.url or procedure_url,
        )
        if not urls:
            unresolved.append(procedure_url)
            continue
        for detail_url in urls:
            identity = lot_identity(detail_url)
            if identity is None:
                continue
            external_id = f"fabrikant.ru:{identity[0]}:{identity[1]}"
            if external_id in seen:
                continue
            seen.add(external_id)
            detail_response = read_get(detail_url)
            detail = parse_detail_lot(
                profile,
                detail_response.content,
                detail_url=detail_url,
                external_id=external_id,
            )
            detail.raw_data.update({
                "listing_page": page,
                "catalogue_procedure_url": procedure_url,
                "trade_id": identity[0],
                "lot_id": identity[1],
                "trade_level_listing": False,
            })
            lots.append(detail)

    if unresolved:
        raise FabrikantContractError(
            "catalogue procedure did not expose an individual lot route"
        )
    if not lots:
        raise FabrikantContractError(
            "no individually identified lots resolved from catalogue page"
        )
    return lots


class FabrikantConnector(HtmlEtpConnector):
    """Fabrikant procedure catalogue -> canonical individual lot connector."""

    def _read_get(self, url: str) -> requests.Response:
        return self._read_request(
            "get",
            _safe_provider_url(self.profile, url),
            timeout=self.timeout,
            allow_redirects=False,
        )

    def _resolve_page(
        self, response: requests.Response, *, page: int
    ) -> list:
        try:
            return resolve_catalogue_page_lots(
                self.profile,
                response,
                page=page,
                read_get=self._read_get,
            )
        except FabrikantContractError as exc:
            raise SourceSchemaError(f"fabrikant.ru: {exc}") from exc

    async def search(
        self, filters: Any, cursor: str | None = None
    ) -> ConnectorPage:
        normalized = (
            filters
            if isinstance(filters, EtpSearchFilters)
            else EtpSearchFilters(**(filters or {}))
        )
        page = max(1, int(cursor or normalized.page))
        response = await asyncio.to_thread(self._request, page)
        lots = await asyncio.to_thread(self._resolve_page, response, page=page)
        page_contract = parse_catalogue_page(
            response.content,
            base_url=response.url or self.profile.listing_url,
            page=page,
        )
        next_cursor = (
            str(page + 1)
            if lots and page_contract.has_next_page
            else None
        )
        return ConnectorPage(
            items=lots,
            next_cursor=next_cursor,
            metadata={
                "source": self.source_id,
                "page": page,
                "items_on_page": len(lots),
                "total_items": None,
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
                "maturity": self.profile.status,
                "response_url": response.url,
            },
        )

    async def fetch_lot(self, external_id: str):
        prefix = "fabrikant.ru:"
        if not external_id.startswith(prefix):
            raise SourceSchemaError("fabrikant.ru: external_id has wrong source")
        identity = external_id[len(prefix):]
        parts = identity.split(":", 1)
        if len(parts) != 2 or not all(parts):
            raise SourceSchemaError(
                "fabrikant.ru: invalid procedure/lot identity"
            )
        detail_url = (
            "https://www.fabrikant.ru/v2/trades/procedure/lot/view/"
            f"{parts[0]}/{parts[1]}"
        )
        response = await asyncio.to_thread(self._read_get, detail_url)
        try:
            return parse_detail_lot(
                self.profile,
                response.content,
                detail_url=detail_url,
                external_id=external_id,
            )
        except FabrikantContractError as exc:
            raise SourceSchemaError(f"fabrikant.ru: {exc}") from exc
