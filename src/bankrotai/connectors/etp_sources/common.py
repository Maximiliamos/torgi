from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag

from bankrotai.connectors.base import AuctionConnector, ConnectorHealth, ConnectorPage
from bankrotai.domain import NormalizedLot


class SourceAccessError(RuntimeError):
    """The provider explicitly refused public read-only access."""


class SourceSchemaError(RuntimeError):
    """The listing no longer matches the evidence-backed source profile."""


@dataclass(frozen=True, slots=True)
class EtpSearchFilters:
    page: int = 1
    page_size: int | None = None
    query: str | None = None


@dataclass(frozen=True, slots=True)
class EtpSourceProfile:
    source_id: str
    platform_name: str
    platform_code: str
    listing_url: str
    lot_url_pattern: str
    status: str = "pilot"
    page_parameter: str | None = None
    first_page_value: int = 1
    supports_get_pagination: bool = False
    complete_source_inventory: bool = False
    safe_for_archival: bool = False
    blocked_reason: str | None = None
    allowed_hosts: tuple[str, ...] = ()
    detail_url_template: str | None = None
    detail_id_separator: str | None = None
    form_page_field: str | None = None
    form_id: str | None = None
    postback_pagination: bool = False
    api_kind: str | None = None

    @property
    def lot_url_re(self) -> re.Pattern[str]:
        return re.compile(self.lot_url_pattern, re.IGNORECASE)


_SPACE_RE = re.compile(r"\s+")
_MONEY_RE = re.compile(r"(?<!\d)(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:\.|лей)?)", re.IGNORECASE)
_CADASTRAL_RE = re.compile(r"(?<!\d)(\d{2}:\d{2}(?::\d{1,7}){2,4})(?!\d)")
_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", re.IGNORECASE)


def _clean(value: str) -> str:
    return _SPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()


def _money(value: str) -> float | None:
    label = r"(?:начальн\w*\s+(?:\([^)]*\)\s*)?цен\w*|цена\s+лот\w*)"
    after = re.search(label + r"[^\d]{0,40}" + _MONEY_RE.pattern, value, re.IGNORECASE)
    raw: str | None = None
    if after:
        tail = value[after.end():after.end() + 100]
        deposit = re.search(r"размер\s+задатка", tail, re.IGNORECASE)
        if not deposit or _MONEY_RE.search(tail[deposit.end():deposit.end() + 60]):
            raw = after.group(1)
    if raw is None:
        before = re.search(_MONEY_RE.pattern + r"[^\d]{0,40}" + label, value, re.IGNORECASE)
        if not before or re.search(
            r"размер\s+задатка", value[max(0, before.start() - 60):before.start()], re.IGNORECASE
        ):
            return None
        raw = before.group(1)
    if not raw:
        return None
    raw = raw.replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None



def _current_money(value: str) -> float | None:
    """A current price requires its own explicit label."""
    match = re.search(
        r"текущ\w*\s+цен\w*[^\d]{0,40}" + _MONEY_RE.pattern,
        value, re.IGNORECASE,
    )
    if not match:
        return None
    try:
        amount = float(match.group(1).replace(" ", "").replace("\xa0", "").replace(",", "."))
        return amount if 0 < amount < 1e15 else None
    except ValueError:
        return None


def _table_prices(row: Tag) -> tuple[float | None, float | None]:
    """Table amounts are reliable only when tied to a named price column."""
    if row.name != "tr":
        return None, None
    table = row.find_parent("table")
    if table is None:
        return None, None
    cells = row.find_all("td", recursive=False)
    if not cells:
        return None, None
    headings: list[str] = []
    for heading_row in table.select("tr"):
        headers = heading_row.find_all("th", recursive=False)
        if headers:
            headings = [_clean(h.get_text(" ", strip=True)).lower() for h in headers]
            break
    if len(headings) != len(cells):
        return None, None

    def parse_number(raw: str) -> float | None:
        cleaned = _clean(raw).replace(" ", "").replace("\xa0", "")
        cleaned = re.sub(r"(?:₽|руб(?:\.|лей)?)$", "", cleaned, flags=re.I)
        if not re.fullmatch(r"\d{1,14}(?:[.,]\d{1,2})?", cleaned):
            return None
        amount = float(cleaned.replace(",", "."))
        return amount if 0 < amount < 1e15 else None

    initial: float | None = None
    current: float | None = None
    for title, cell in zip(headings, cells):
        if re.search(r"начальн\w*\s+цен\w*|цена\s+лот\w*", title):
            initial = parse_number(cell.get_text(" ", strip=True))
        elif re.search(r"текущ\w*\s+цен\w*", title):
            current = parse_number(cell.get_text(" ", strip=True))
    return initial, current

def _category(value: str) -> str:
    lowered = value.lower()
    if any(word in lowered for word in ("квартир", "комнат", "жилой дом", "недвижим", "помещен")):
        return "real_estate"
    if any(word in lowered for word in ("земельн", "участок", "кадастров")):
        return "land"
    if any(word in lowered for word in ("автомоб", "транспорт", "vin", "прицеп", "снегоход")):
        return "vehicle"
    if any(word in lowered for word in ("право требован", "дебитор")):
        return "receivables"
    return "other"


def _status(value: str) -> str:
    lowered = value.lower()
    if any(word in lowered for word in ("заверш", "окончен", "состоял", "отменен", "отменён")):
        return "closed"
    if any(word in lowered for word in ("приостанов", "аннулирован")):
        return "paused"
    if any(word in lowered for word in ("прием заяв", "приём заяв", "объявлен", "текущ", "идут торги")):
        return "active"
    return "unknown"


def _datetime_after(value: str, label: str) -> datetime | None:
    match = re.search(label + r"[^\d]{0,40}(\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)", value, re.I)
    if not match:
        return None
    raw = match.group(1)
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def _label_value(value: str, label: str, pattern: str) -> str | None:
    match = re.search(label + r"\s*[:№]?\s*(" + pattern + r")", value, re.I)
    return _clean(match.group(1)) if match else None


def _external_id(profile: EtpSourceProfile, href: str, match: re.Match[str]) -> str:
    groups = [part for part in match.groups() if part]
    if groups:
        identity = "-".join(groups)
    else:
        parsed = urlparse(href)
        identity = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        if parsed.query:
            identity = f"{identity}-{parsed.query}"
    return f"{profile.source_id}:{identity}"


def _container(anchor: Tag) -> Tag:
    for name in ("tr", "article", "li"):
        parent = anchor.find_parent(name)
        if parent is not None:
            return parent
    for class_pattern in (re.compile(r"(?:lot|trade|item|card|result)", re.I),):
        parent = anchor.find_parent(class_=class_pattern)
        if parent is not None:
            return parent
    return anchor.parent if isinstance(anchor.parent, Tag) else anchor


def _parse_soup(html: str | bytes) -> BeautifulSoup:
    if isinstance(html, bytes):
        # html.parser preserves onclick rows in malformed legacy XHTML better
        # than libxml. Playwright serializes legacy pages as UTF-8 while often
        # retaining their old cp1251 meta tag, so valid UTF-8 takes priority.
        try:
            decoded = html.decode("utf-8")
        except UnicodeDecodeError:
            probe = BeautifulSoup(html, "lxml")
            decoded = html.decode(probe.original_encoding or "windows-1251", errors="replace")
        return BeautifulSoup(decoded, "html.parser")
    return BeautifulSoup(html, "html.parser")


def _safe_provider_url(profile: EtpSourceProfile, value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in profile.allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        raise SourceAccessError(f"{profile.source_id}: refused unsafe provider URL")
    return value


def _parse_multi_lot_catalogue(
    profile: EtpSourceProfile, html: str | bytes, *, page: int
) -> list[NormalizedLot] | None:
    if profile.source_id not in {"nistp.ru", "vertrades.ru"}:
        return None
    from bankrotai.connectors.registry.direct_etp_catalogue import PROFILE_BY_ID, parse_catalogue

    decoded = _parse_soup(html).decode()
    observations = parse_catalogue(PROFILE_BY_ID[profile.source_id], decoded, limit=100)
    if len(observations) == 100:
        raise SourceSchemaError(f"{profile.source_id}: page exceeds the verified per-page lot limit")
    identified = [item for item in observations if item.get("external_id")]
    if not identified:
        return None
    lots: list[NormalizedLot] = []
    for item in identified:
        price_value = item.get("listing_price_rub")
        try:
            price = float(price_value) if isinstance(price_value, (str, int, float)) and price_value != "" else None
        except (TypeError, ValueError):
            price = None
        title = _clean(str(item.get("title") or ""))
        detail_url = _safe_provider_url(profile, str(item.get("catalogue_detail_link") or ""))
        query = parse_qs(urlparse(detail_url).query)
        provider_id = next(iter(query.get("trade_nid", []) or query.get("id", [])), None)
        if not provider_id or not item.get("lot"):
            raise SourceSchemaError(f"{profile.source_id}: per-lot provider identity is missing")
        lots.append(NormalizedLot(
            external_id=f"{profile.source_id}:{provider_id}:{item['lot']}",
            source=profile.source_id.removesuffix(".ru"),
            source_system=profile.source_id,
            title=title[:500],
            description=title[:5000],
            category=_category(title),
            region_slug=None,
            region_name=None,
            address=None,
            cadastral_number=item.get("cadastral_number"),
            vin=None,
            area=None,
            start_price=price,
            current_price=None,
            auction_status=_status(str(item.get("source_status_text") or "")),
            lot_url=detail_url,
            source_url=detail_url,
            detail_level="search",
            raw_data={
                "listing_page": page,
                "trade_id": item.get("trade"),
                "lot_number": item.get("lot"),
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
            },
            platform_name=profile.platform_name,
            platform_code=profile.platform_code,
        ))
    return lots


def parse_listing_html(profile: EtpSourceProfile, html: str | bytes, *, page: int = 1) -> list[NormalizedLot]:
    multi_lot = _parse_multi_lot_catalogue(profile, html, page=page)
    if multi_lot is not None:
        return multi_lot
    soup = _parse_soup(html)
    candidates: dict[str, tuple[str, str, Tag, re.Match[str]]] = {}
    for anchor in soup.select("a[href]"):
        href = urljoin(profile.listing_url, str(anchor.get("href") or "").strip())
        parsed = urlparse(href)
        if profile.allowed_hosts and parsed.hostname not in profile.allowed_hosts:
            continue
        match = profile.lot_url_re.search(href)
        if not match:
            continue
        title = _clean(anchor.get_text(" ", strip=True))
        if not title:
            title = _clean(str(anchor.get("title") or ""))
        container = _container(anchor)
        existing = candidates.get(href)
        if existing is None or len(title) > len(existing[0]):
            candidates[href] = (title, href, container, match)

    # Several older Russian ETP engines make an entire table row clickable and
    # keep the canonical detail URL only in inline onclick JavaScript.  We do
    # not execute the script; the public relative URL is extracted as data.
    for node in soup.select("[onclick]"):
        onclick = str(node.get("onclick") or "")
        urls = re.findall(r"['\"]([^'\"]+)['\"]", onclick)
        for raw_href in urls:
            if not raw_href.startswith(("/", "http://", "https://")):
                continue
            href = urljoin(profile.listing_url, raw_href)
            parsed = urlparse(href)
            if profile.allowed_hosts and parsed.hostname not in profile.allowed_hosts:
                continue
            match = profile.lot_url_re.search(href)
            if not match:
                continue
            cells = [_clean(cell.get_text(" ", strip=True)) for cell in node.select("td")]
            title = max((cell for cell in cells if not cell.isdigit()), key=len, default="")
            candidates[href] = (title, href, node, match)

    lots: list[NormalizedLot] = []
    seen: set[str] = set()
    for title, href, container, match in candidates.values():
        context = _clean(container.get_text(" ", strip=True))
        if container.name == "tr":
            cell_titles = [_clean(cell.get_text(" ", strip=True)) for cell in container.select("td")]
            title = max((cell for cell in cell_titles if 4 <= len(cell) <= 1000), key=len, default=title)
        if len(title) < 4 or title.isdigit():
            alternatives = [
                _clean(node.get_text(" ", strip=True))
                for node in container.select("a[href]")
                if profile.lot_url_re.search(urljoin(profile.listing_url, str(node.get("href") or "")))
            ]
            title = max(alternatives, key=len, default=title)
        if len(title) < 4 or title.isdigit():
            continue
        external_id = _external_id(profile, href, match)
        if external_id in seen:
            continue
        seen.add(external_id)
        cadastral = _CADASTRAL_RE.search(context)
        vin = _VIN_RE.search(context)
        area_match = _AREA_RE.search(context)
        area = float(area_match.group(1).replace(",", ".")) if area_match else None
        table_initial, table_current = _table_prices(container)
        price = _money(context)
        if price is None:
            price = table_initial
        current_price = _current_money(context)
        if current_price is None:
            current_price = table_current
        lots.append(NormalizedLot(
            external_id=external_id,
            source=profile.source_id.removesuffix(".ru").removesuffix(".com"),
            source_system=profile.source_id,
            title=title[:500],
            description=context[:5000],
            category=_category(context),
            region_slug=None,
            region_name=None,
            address=None,
            cadastral_number=cadastral.group(1) if cadastral else None,
            vin=vin.group(0).upper() if vin else None,
            area=area,
            start_price=price,
            current_price=current_price,
            auction_status=_status(context),
            lot_url=href,
            source_url=href,
            detail_level="list",
            raw_data={
                "listing_url": profile.listing_url,
                "listing_page": page,
                "complete_source_inventory": profile.complete_source_inventory,
                "safe_for_archival": profile.safe_for_archival,
                "reconcile_missing": False,
            },
            platform_name=profile.platform_name,
            platform_code=profile.platform_code,
        ))
    return lots


def parse_detail_html(
    profile: EtpSourceProfile,
    html: str | bytes,
    *,
    detail_url: str,
    external_id: str | None = None,
) -> NormalizedLot:
    soup = _parse_soup(html)
    text_value = _clean(soup.get_text(" ", strip=True))
    if len(text_value) < 80:
        raise SourceSchemaError(f"{profile.source_id}: detail page has insufficient content")
    match = profile.lot_url_re.search(detail_url)
    if external_id is None:
        if not match:
            raise SourceSchemaError(f"{profile.source_id}: detail URL does not match its verified schema")
        external_id = _external_id(profile, detail_url, match)

    headings = [_clean(node.get_text(" ", strip=True)) for node in soup.select("h1, h2, h3")]
    title_candidates = [item for item in headings if len(item) >= 8 and "информац" not in item.lower()]
    title = max(title_candidates, key=len, default="")
    if not title:
        title = _label_value(
            text_value,
            r"(?:наименование имущества|предмет договора \(лота\)|наименование)",
            r".{8,500}?(?=\s+(?:начальн|статус|организатор|дата|сведения)\b)",
        ) or "Лот"

    cadastral = _CADASTRAL_RE.search(text_value)
    vin = _VIN_RE.search(text_value)
    area_match = _AREA_RE.search(text_value)
    procedure_number = _label_value(
        text_value,
        r"(?:идентификационный номер|номер процедуры|информация о (?:публичном предложении|торгах))",
        r"[A-ZА-Я0-9._-]{3,40}",
    )
    case_number = _label_value(text_value, r"номер дела о банкротстве", r"[A-ZА-Я0-9/.-]{4,50}")
    area = float(area_match.group(1).replace(",", ".")) if area_match else None
    price = _money(text_value)
    current_price = _current_money(text_value)
    return NormalizedLot(
        external_id=external_id,
        source=profile.source_id.removesuffix(".ru").removesuffix(".com"),
        source_system=profile.source_id,
        title=title[:500],
        description=text_value[:5000],
        category=_category(text_value),
        region_slug=None,
        region_name=None,
        address=None,
        cadastral_number=cadastral.group(1) if cadastral else None,
        vin=vin.group(0).upper() if vin else None,
        area=area,
        start_price=price,
        current_price=current_price,
        auction_status=_status(text_value),
        lot_url=detail_url,
        source_url=detail_url,
        detail_level="detail",
        raw_data={
            "complete_source_inventory": profile.complete_source_inventory,
            "safe_for_archival": profile.safe_for_archival,
            "reconcile_missing": False,
        },
        platform_name=profile.platform_name,
        platform_code=profile.platform_code,
        procedure_number=procedure_number,
        bankruptcy_case_number=case_number,
        application_deadline=_datetime_after(
            text_value,
            r"(?:окончание|дата окончания)\s+(?:приема|приёма|представления)\s+заявок",
        ),
        auction_at=_datetime_after(text_value, r"дата проведения"),
    )


def parse_sberbank_json(profile: EtpSourceProfile, payload: dict[str, Any], *, page: int) -> list[NormalizedLot]:
    items = payload.get("PurchaseList")
    if not isinstance(items, list):
        raise SourceSchemaError(f"{profile.source_id}: PurchaseList is missing from API response")
    lots: list[NormalizedLot] = []
    seen: set[str] = set()
    for item in items:
        raw = item.get("_source") if isinstance(item, dict) else None
        if not isinstance(raw, dict):
            continue
        href = str(raw.get("objectHref") or "")
        match = profile.lot_url_re.search(href)
        if not match:
            continue
        external_id = _external_id(profile, href, match)
        if external_id in seen:
            continue
        seen.add(external_id)
        title = _clean(str(raw.get("title") or raw.get("BidName") or raw.get("purchName") or ""))
        if not title:
            continue
        price: float | None = None
        # The API provides an explicitly RUB-denominated price. A generic
        # amount is accepted only with an explicit ruble currency marker.
        amount = raw.get("purchAmountRUB")
        currency = str(raw.get("purchCurrency") or raw.get("CurrencyCode") or "").strip().upper()
        if amount is None and currency in {"RUB", "643"}:
            amount = raw.get("purchAmount")
        try:
            if isinstance(amount, (str, int, float)) and not isinstance(amount, bool) and amount != "":
                candidate = float(str(amount).replace(" ", "").replace(",", "."))
                if 0 < candidate < 1e15:
                    price = candidate
        except (TypeError, ValueError, OverflowError):
            pass
        text_value = _clean(" ".join(str(raw.get(key) or "") for key in ("title", "purchName", "Address")))
        cadastral = _CADASTRAL_RE.search(str(raw.get("cadastralNumber") or text_value))
        vin = _VIN_RE.search(text_value)
        lots.append(NormalizedLot(
            external_id=external_id,
            source="sberbank-ast",
            source_system=profile.source_id,
            title=title[:500],
            description=text_value[:5000],
            category=_category(text_value),
            region_slug=None,
            region_name=_clean(str(raw.get("RegionNameTerm") or "")) or None,
            address=_clean(str(raw.get("Address") or "")) or None,
            cadastral_number=cadastral.group(1) if cadastral else None,
            vin=vin.group(0).upper() if vin else None,
            area=None,
            start_price=price,
            current_price=None,
            auction_status=_status(str(raw.get("PurchaseStageTerm") or raw.get("purchStateNameTerm") or "")),
            lot_url=href,
            source_url=href,
            detail_level="search",
            raw_data={
                "listing_page": page,
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
            },
            platform_name=profile.platform_name,
            platform_code=profile.platform_code,
            procedure_number=_clean(str(raw.get("purchCode") or "")) or None,
            application_deadline=_datetime_after(
                f"Дата окончания представления заявок {raw.get('RequestStopDate') or ''}",
                r"дата окончания представления заявок",
            ),
            # AuctionResultDate describes the result, not the start of bidding.
            # Never publish it as auction_at without source-specific evidence.
            auction_at=None,
        ))
    return lots


def _has_next_page(profile: EtpSourceProfile, html: str | bytes, page: int) -> bool:
    soup = _parse_soup(html)
    if profile.form_page_field:
        return any(
            _clean(node.get_text(" ", strip=True)) == str(page + 1)
            and re.search(rf"pagenum_send\(\s*{page + 1}\s*\)", str(node.get("onclick") or ""))
            for node in soup.select("[onclick]")
        )
    if profile.postback_pagination:
        return any(
            _clean(node.get_text(" ", strip=True)) == str(page + 1)
            and "__doPostBack" in str(node.get("href") or "")
            for node in soup.select("a[href]")
        )
    if not profile.supports_get_pagination or not profile.page_parameter:
        return False
    expected = str(profile.first_page_value + page)
    for anchor in soup.select("a[href]"):
        query = parse_qs(urlparse(urljoin(profile.listing_url, str(anchor.get("href") or ""))).query)
        if expected in query.get(profile.page_parameter, []):
            return True
    return False


def _page_url(profile: EtpSourceProfile, page: int) -> str:
    if page <= 1 or not profile.page_parameter:
        return profile.listing_url
    parsed = urlparse(profile.listing_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query[profile.page_parameter] = [str(profile.first_page_value + page - 1)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


class HtmlEtpConnector(AuctionConnector):
    profile: EtpSourceProfile
    capabilities = frozenset({"search", "fetch_lot", "healthcheck"})
    safe_for_reconciliation = False

    def __init__(self, *, session: requests.Session | None = None, timeout: float = 25.0) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self._last_response: requests.Response | None = None
        self._last_page = 0
        self.session.headers.setdefault(
            "User-Agent",
            "TORGI/1.0 (+https://github.com/Maximiliamos/torgi; public read-only connector)",
        )

    @property
    def source_id(self) -> str:  # type: ignore[override]
        return self.profile.source_id

    def _check_response(self, response: requests.Response) -> requests.Response:
        if 300 <= response.status_code < 400:
            raise SourceAccessError(f"{self.source_id}: provider redirect was refused")
        if response.status_code in {401, 403, 429}:
            raise SourceAccessError(f"{self.source_id}: provider returned HTTP {response.status_code}")
        response.raise_for_status()
        return response

    def _request(self, page: int) -> requests.Response:
        if self.profile.blocked_reason:
            raise SourceAccessError(self.profile.blocked_reason)
        if self.profile.api_kind == "sberbank_ast":
            filter_body = (
                "<query><targetPageCode>UtpESGoodsList</targetPageCode><searchbyorg></searchbyorg>"
                f"<pagesize>21</pagesize><pagenum>{page - 1}</pagenum><sortType></sortType>"
                "<sortOrder>desc</sortOrder><sourceTerm>Продажа имущества (предприятия) банкротов</sourceTerm>"
                "<searchBarType>anyWord</searchBarType><saleType></saleType><org></org>"
                "<cadastralNumber></cadastralNumber><isOnMapTerm></isOnMapTerm><hasRequest></hasRequest>"
                "<hasDeposit></hasDeposit><orgInnKpp></orgInnKpp></query>"
            )
            api_url = _safe_provider_url(
                self.profile, "https://www.sberbank-ast.ru/api/Processing/main"
            )
            response = self._check_response(self.session.post(
                api_url,
                json={
                    "windowCode": "/ESGoodsList",
                    "actionType": "MONITOR",
                    "actionCode": "default",
                    "documentBody": "",
                    "parm": "es",
                    "filterBody": filter_body,
                    "options": {},
                },
                timeout=self.timeout,
                allow_redirects=False,
            ))
            self._last_response, self._last_page = response, page
            return response
        supports_form = bool(self.profile.form_page_field or self.profile.postback_pagination)
        if page > 1 and not self.profile.supports_get_pagination and not supports_form:
            raise SourceAccessError(f"{self.source_id}: pagination requires a provider-specific browser/form contract")
        if page <= 1 or self.profile.supports_get_pagination:
            page_url = _safe_provider_url(self.profile, _page_url(self.profile, page))
            response = self._check_response(self.session.get(
                page_url, timeout=self.timeout, allow_redirects=False
            ))
            self._last_response, self._last_page = response, page
            return response

        previous = self._last_response if self._last_page == page - 1 else None
        previous = previous or self._check_response(self.session.get(
            _safe_provider_url(self.profile, self.profile.listing_url),
            timeout=self.timeout,
            allow_redirects=False,
        ))
        soup = _parse_soup(previous.content)
        if self.profile.form_page_field:
            form = soup.find("form", id=self.profile.form_id) if self.profile.form_id else soup.find("form")
            if not isinstance(form, Tag):
                raise SourceSchemaError(f"{self.source_id}: pagination form disappeared")
            data: dict[str, str] = {}
            for node in form.select("input[name]"):
                if str(node.get("type") or "").lower() not in {"submit", "button", "image", "file"}:
                    data[str(node.get("name"))] = str(node.get("value") or "")
            data[self.profile.form_page_field] = str(page)
            action = _safe_provider_url(
                self.profile,
                urljoin(previous.url or self.profile.listing_url, str(form.get("action") or "")),
            )
        else:
            link = next((
                node for node in soup.select("a[href]")
                if _clean(node.get_text(" ", strip=True)) == str(page) and "__doPostBack" in str(node.get("href") or "")
            ), None)
            if not isinstance(link, Tag):
                raise SourceSchemaError(f"{self.source_id}: page {page} postback target disappeared")
            postback = re.search(r"__doPostBack\('([^']*)','([^']*)'\)", str(link.get("href") or ""))
            if not postback:
                raise SourceSchemaError(f"{self.source_id}: invalid page {page} postback")
            data = {
                str(node.get("name")): str(node.get("value") or "")
                for node in soup.select("input[type=hidden][name]")
            }
            data["__EVENTTARGET"], data["__EVENTARGUMENT"] = postback.groups()
            action = _safe_provider_url(
                self.profile, previous.url or self.profile.listing_url
            )
        response = self._check_response(self.session.post(
            action, data=data, timeout=self.timeout, allow_redirects=False
        ))
        self._last_response, self._last_page = response, page
        return response

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        normalized = filters if isinstance(filters, EtpSearchFilters) else EtpSearchFilters(**(filters or {}))
        page = max(1, int(cursor or normalized.page))
        response = await asyncio.to_thread(self._request, page)
        if self.profile.api_kind == "sberbank_ast":
            payload = response.json()
            lots = parse_sberbank_json(self.profile, payload, page=page)
            total = int(payload.get("Total") or 0)
            next_cursor = str(page + 1) if page * 21 < total else None
        else:
            lots = parse_listing_html(self.profile, response.content, page=page)
            total = None
            next_cursor = str(page + 1) if lots and _has_next_page(self.profile, response.content, page) else None
        if not lots and page == 1:
            raise SourceSchemaError(f"{self.source_id}: no lot links matched the verified listing schema")
        return ConnectorPage(items=lots, next_cursor=next_cursor, metadata={
            "source": self.source_id,
            "page": page,
            "items_on_page": len(lots),
            "total_items": total,
            "complete_source_inventory": self.profile.complete_source_inventory,
            "safe_for_archival": self.profile.safe_for_archival,
            "reconcile_missing": False,
            "maturity": self.profile.status,
            "response_url": response.url,
        })

    async def fetch_lot(self, external_id: str) -> NormalizedLot:
        if self.profile.blocked_reason:
            raise SourceAccessError(self.profile.blocked_reason)
        if self.source_id in {"nistp.ru", "vertrades.ru"}:
            raise SourceAccessError(
                f"{self.source_id}: per-lot detail isolation inside a shared trade card is not proven"
            )
        template = self.profile.detail_url_template
        if not template:
            raise SourceAccessError(f"{self.source_id}: detail URL template is not proven")
        identity = external_id.removeprefix(self.source_id + ":")
        identity_path = identity
        if self.profile.detail_id_separator:
            identity_path = identity.replace("-", self.profile.detail_id_separator)
        sale_type, separator, lot_id = identity.rpartition("-")
        detail_url = template.format(
            identity=identity,
            identity_path=identity_path,
            sale_type=sale_type if separator else "",
            lot_id=lot_id if separator else identity,
        )
        detail_url = _safe_provider_url(self.profile, detail_url)
        response = await asyncio.to_thread(
            self.session.get, detail_url, timeout=self.timeout, allow_redirects=False
        )
        if response.status_code in {401, 403, 429}:
            raise SourceAccessError(f"{self.source_id}: provider returned HTTP {response.status_code}")
        response.raise_for_status()
        return parse_detail_html(
            self.profile,
            response.content,
            detail_url=detail_url,
            external_id=external_id,
        )

    async def healthcheck(self) -> ConnectorHealth:
        if self.profile.blocked_reason:
            return ConnectorHealth("blocked", self.profile.blocked_reason, {"source": self.source_id})
        try:
            response = await asyncio.to_thread(self._request, 1)
            if self.profile.api_kind == "sberbank_ast":
                lots = parse_sberbank_json(self.profile, response.json(), page=1)
            else:
                lots = parse_listing_html(self.profile, response.content)
        except Exception as exc:
            return ConnectorHealth("degraded", str(exc), {"source": self.source_id})
        status = "ok" if lots else "degraded"
        return ConnectorHealth(status, f"matched {len(lots)} public lot links", {"source": self.source_id})


def connector_type(profile: EtpSourceProfile) -> type[HtmlEtpConnector]:
    class ProfiledConnector(HtmlEtpConnector):
        pass

    ProfiledConnector.profile = profile
    if profile.source_id in {"nistp.ru", "vertrades.ru"}:
        ProfiledConnector.capabilities = frozenset({"search", "healthcheck"})
    ProfiledConnector.__name__ = "".join(part.title() for part in re.split(r"[^a-z0-9]+", profile.source_id)) + "Connector"
    ProfiledConnector.__qualname__ = ProfiledConnector.__name__
    return ProfiledConnector
