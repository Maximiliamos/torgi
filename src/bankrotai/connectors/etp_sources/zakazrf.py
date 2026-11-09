from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from bankrotai.connectors.base import AuctionConnector, ConnectorHealth, ConnectorPage
from bankrotai.connectors.etp_sources.source_specific import apply_detail
from bankrotai.connectors.transient_http import request_with_retries
from bankrotai.domain import NormalizedLot


SOURCE_ID = "zakazrf.ru"
PLATFORM_NAME = "ЗаказРФ"
PLATFORM_CODE = "zakazrf"
CATALOGUE_URL = "https://bankrot.zakazrf.ru/public/purchases-all/"
ALLOWED_HOST = "bankrot.zakazrf.ru"
LOT_URL_RE = re.compile(
    r"bankrot\.zakazrf\.ru/public/(auctions|contests|public-offers)/lots/view/(\d+)/",
    re.IGNORECASE,
)
_EXTERNAL_ID_RE = re.compile(
    r"^zakazrf\.ru:(auctions|contests|public-offers)-(\d+)$",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"\s+")
_CADASTRAL_RE = re.compile(r"(?<!\d)(\d{2}:\d{2}(?::\d{1,7}){2,4})(?!\d)")
_AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", re.IGNORECASE)
_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_POSTBACK_RE = re.compile(r"__doPostBack\('([^']*)','([^']*)'\)")


class ZakazRfAccessError(RuntimeError):
    """ZakazRF refused or redirected a public read-only request."""


class ZakazRfSchemaError(RuntimeError):
    """ZakazRF public markup no longer matches the verified source contract."""


@dataclass(frozen=True, slots=True)
class CataloguePage:
    items: list[NormalizedLot]
    next_page: int | None
    response_url: str


def clean(value: str) -> str:
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
        or parsed.hostname != ALLOWED_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        raise ZakazRfAccessError("zakazrf.ru: refused unsafe provider URL")
    return value


def verify_catalogue_url(value: str) -> None:
    parsed = urlparse(_safe_url(value))
    if parsed.path.rstrip("/") != "/public/purchases-all":
        raise ZakazRfSchemaError(
            f"zakazrf.ru: unexpected catalogue path {parsed.path!r}"
        )


def external_id_from_detail_url(detail_url: str) -> str:
    safe = _safe_url(detail_url)
    match = LOT_URL_RE.search(safe)
    if not match:
        raise ZakazRfSchemaError("zakazrf.ru: detail URL does not match lot schema")
    return f"{SOURCE_ID}:{match.group(1).lower()}-{match.group(2)}"


def detail_url_for_external_id(external_id: str) -> str:
    match = _EXTERNAL_ID_RE.fullmatch(external_id)
    if not match:
        raise ZakazRfSchemaError("zakazrf.ru: invalid external lot identity")
    sale_type, lot_id = match.groups()
    return (
        f"https://{ALLOWED_HOST}/public/{sale_type.lower()}/lots/view/{lot_id}/"
    )


def verify_identity(external_id: str, detail_url: str) -> None:
    observed = external_id_from_detail_url(detail_url)
    if observed != external_id:
        raise ZakazRfSchemaError(
            f"zakazrf.ru: detail identity mismatch: expected {external_id}, got {observed}"
        )


def _lot_section(value: str) -> str:
    markers = list(
        re.finditer(r"информация\s+о\s+лоте\s*№?\s*\d+", value, re.IGNORECASE)
    )
    return value[markers[-1].start():] if markers else value


def parse_lot_number(value: str) -> str | None:
    match = re.search(r"\bномер\s*:\s*(\d{1,8})\b", _lot_section(value), re.IGNORECASE)
    return match.group(1) if match else None


def parse_title(value: str) -> str | None:
    match = re.search(
        r"\bнаименование\s*:\s*(.{4,1200}?)"
        r"(?=\s+(?:(?:дата\s+(?:начала|окончания|проведения|подведения)"
        r"[^:]{0,80})|статус)\s*:)",
        _lot_section(value),
        re.IGNORECASE,
    )
    return clean(match.group(1)) if match else None


def _labelled_money(value: str, label: str) -> float | None:
    match = re.search(
        label
        + r"\s*,?\s*(?:руб(?:\.|лей)?)?\s*:\s*"
        + r"(\d[\d\s\u00a0]{0,30}(?:[.,]\d{1,2})?)",
        _lot_section(value),
        re.IGNORECASE,
    )
    if not match:
        return None
    raw = re.sub(r"[\s\u00a0]", "", match.group(1)).replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        return None
    return amount if 0 < amount < 1e15 else None


def parse_initial_price(value: str) -> float | None:
    """Only the explicit individual-lot initial-price label is accepted."""
    return _labelled_money(value, r"начальн\w*\s+цен\w*")


def parse_current_price(value: str) -> float | None:
    """Only the explicit current-price label is accepted for public offers."""
    return _labelled_money(value, r"текущ\w*\s+цен\w*")


def parse_status_text(value: str) -> str | None:
    match = re.search(
        r"\bстатус\s*:\s*(.{2,100}?)"
        r"(?=\s+(?:дата|начальн\w*\s+цен\w*|текущ\w*\s+цен\w*|"
        r"шаг|порядок|сведения|обеспечение)\b)",
        _lot_section(value),
        re.IGNORECASE,
    )
    return clean(match.group(1)) if match else None


def parse_status(value: str | None) -> str:
    if not value:
        return "unknown"
    lowered = clean(value).lower()
    if re.search(
        r"(?:при[её]м\s+заявок\s+заверш|заверш[её]н\s+при[её]м\s+заявок)",
        lowered,
    ):
        return "unknown"
    if lowered in {"прием заявок", "приём заявок", "идут торги"}:
        return "active"
    if any(
        token in lowered
        for token in (
            "окончен",
            "завершен",
            "завершён",
            "не состоялся",
            "состоялся",
            "отменен",
            "отменён",
        )
    ):
        return "closed"
    if "приостанов" in lowered:
        return "paused"
    if any(token in lowered for token in ("объявлен", "ожидается")):
        return "scheduled"
    return "unknown"


def parse_classifier(value: str) -> str | None:
    match = re.search(
        r"классификатор\s+ефрсб\s*:\s*(.{2,300}?)"
        r"(?=(?:\s+(?:интервалы\s+снижения\s+цены|обеспечение\s+заявки|"
        r"порядок\s+внесения|документы|всего\s+подано))|\s*$)",
        _lot_section(value),
        re.IGNORECASE,
    )
    if not match:
        return None
    classifier = clean(match.group(1)).strip(" :-")
    return classifier or None


def classify_property(classifier: str | None) -> str | None:
    """Classify only structured EFRSB evidence; free prose never promotes property."""
    if not classifier:
        return None
    lowered = classifier.lower()
    if "земельн" in lowered or "земли " in lowered:
        return "land"
    if any(
        token in lowered
        for token in (
            "жилые здания",
            "жилые помещения",
            "здания (кроме жилых)",
            "нежил",
            "сооружен",
            "гараж",
            "машино-мест",
            "недвижим",
        )
    ):
        return "real_estate"
    if any(
        token in lowered
        for token in ("автомоб", "транспорт", "прицеп", "снегоход")
    ):
        return "vehicle"
    if any(
        token in lowered
        for token in (
            "право требования",
            "права требования",
            "уступка требован",
            "дебитор",
        )
    ):
        return "receivables"
    if any(
        token in lowered
        for token in ("оборудован", "машины и оборудование", "станк")
    ):
        return "equipment"
    if "аренд" in lowered or "право пользования" in lowered:
        return "lease"
    return None


def parse_address(title: str | None) -> str | None:
    if not title:
        return None
    match = re.search(
        r"\b(?:адрес|по\s+адресу)\s*:\s*(.+?)"
        r"(?=,\s*(?:кадастров(?:ый)?\s+номер|кад\.?\s*№)|$)",
        title,
        re.IGNORECASE,
    )
    return clean(match.group(1)).strip(" ,") if match else None


def _datetime_after(value: str, label: str) -> datetime | None:
    match = re.search(
        label
        + r"[^\d]{0,60}"
        + r"(\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None
    raw = match.group(1)
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def parse_dates(value: str) -> tuple[datetime | None, datetime | None]:
    section = _lot_section(value)
    deadline = _datetime_after(
        section,
        r"(?:окончание|дата окончания)\s+(?:приема|приёма|представления)\s+заявок",
    )
    auction_at = _datetime_after(section, r"дата проведения")
    return deadline, auction_at


def parse_property_fields(title: str | None) -> dict[str, Any]:
    title = title or ""
    cadastral = _CADASTRAL_RE.search(title)
    area_match = _AREA_RE.search(title)
    vin = _VIN_RE.search(title)
    return {
        "address": parse_address(title),
        "cadastral_number": cadastral.group(1) if cadastral else None,
        "area": float(area_match.group(1).replace(",", ".")) if area_match else None,
        "vin": vin.group(0).upper() if vin else None,
    }


def parse_public_offer_schedule(html: str | bytes) -> list[dict[str, Any]] | None:
    """Parse interval rows only when a table explicitly names date and price columns."""
    soup = parse_soup(html)
    schedule: list[dict[str, Any]] = []
    for table in soup.select("table"):
        rows = table.select("tr")
        headers: list[str] = []
        for row in rows:
            cells = row.find_all(["th", "td"], recursive=False)
            texts = [clean(cell.get_text(" ", strip=True)).lower() for cell in cells]
            if any("дата начала интервала" in item for item in texts) and any(
                "цена на интервале" in item for item in texts
            ):
                headers = texts
                continue
            if not headers or len(cells) != len(headers):
                continue
            date_index = next(
                (i for i, item in enumerate(headers) if "дата начала интервала" in item),
                None,
            )
            price_index = next(
                (i for i, item in enumerate(headers) if "цена на интервале" in item),
                None,
            )
            if date_index is None or price_index is None:
                continue
            date_text = clean(cells[date_index].get_text(" ", strip=True))
            price_text = clean(cells[price_index].get_text(" ", strip=True))
            start_at = _datetime_after(
                f"Дата начала интервала {date_text}", r"дата начала интервала"
            )
            money_match = re.search(
                r"(?<!\d)(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)(?!\d)", price_text
            )
            if not start_at or not money_match:
                continue
            amount = float(
                re.sub(r"[\s\u00a0]", "", money_match.group(1)).replace(",", ".")
            )
            if 0 < amount < 1e15:
                schedule.append(
                    {"starts_at": start_at.isoformat(), "price": amount}
                )
    return schedule or None


def parse_detail(
    html: str | bytes,
    *,
    detail_url: str,
    external_id: str | None = None,
    complete_source_inventory: bool = False,
    safe_for_archival: bool = False,
) -> NormalizedLot:
    text_value = clean(parse_soup(html).get_text(" ", strip=True))
    if len(text_value) < 80:
        raise ZakazRfSchemaError("zakazrf.ru: detail page has insufficient content")
    observed_id = external_id_from_detail_url(detail_url)
    if external_id is None:
        external_id = observed_id
    elif observed_id != external_id:
        raise ZakazRfSchemaError(
            f"zakazrf.ru: detail identity mismatch: expected {external_id}, got {observed_id}"
        )

    title = parse_title(text_value) or "Лот"
    classifier = parse_classifier(text_value)
    fields = parse_property_fields(title)
    start_price = parse_initial_price(text_value)
    current_price = parse_current_price(text_value)
    status_text = parse_status_text(text_value)
    deadline, auction_at = parse_dates(text_value)

    return NormalizedLot(
        external_id=external_id,
        source="zakazrf",
        source_system=SOURCE_ID,
        title=title[:500],
        description=text_value[:5000],
        category=classify_property(classifier) or "other",
        region_slug=None,
        region_name=None,
        address=fields["address"],
        cadastral_number=fields["cadastral_number"],
        vin=fields["vin"],
        area=fields["area"],
        start_price=start_price,
        current_price=current_price,
        auction_status=parse_status(status_text),
        lot_url=detail_url,
        source_url=detail_url,
        detail_level="detail",
        raw_data={
            "complete_source_inventory": complete_source_inventory,
            "safe_for_archival": safe_for_archival,
            "reconcile_missing": False,
            "structured_property_classifier": classifier,
            "structured_property_classifier_verified": bool(classifier),
            "lot_number": parse_lot_number(text_value),
            "detail_identity_verified": True,
            "detail_start_price_verified": start_price is not None,
            "detail_current_price_verified": current_price is not None,
            "detail_status_verified": status_text is not None,
        },
        platform_name=PLATFORM_NAME,
        platform_code=PLATFORM_CODE,
        application_deadline=deadline,
        auction_at=auction_at,
        public_offer_schedule=parse_public_offer_schedule(html),
    )


def parse_catalogue_page(html: str | bytes, *, page: int = 1) -> list[NormalizedLot]:
    """Parse lot-level links but defer property classification to individual detail."""
    soup = parse_soup(html)
    lots: list[NormalizedLot] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = urljoin(CATALOGUE_URL, str(anchor.get("href") or "").strip())
        try:
            external_id = external_id_from_detail_url(href)
        except (ZakazRfAccessError, ZakazRfSchemaError):
            continue
        if external_id in seen:
            continue
        title = clean(anchor.get_text(" ", strip=True)) or clean(
            str(anchor.get("title") or "")
        )
        if len(title) < 4:
            continue
        seen.add(external_id)
        lots.append(
            NormalizedLot(
                external_id=external_id,
                source="zakazrf",
                source_system=SOURCE_ID,
                title=title[:500],
                description=title[:5000],
                category="other",
                region_slug=None,
                region_name=None,
                address=None,
                cadastral_number=None,
                vin=None,
                area=None,
                start_price=None,
                current_price=None,
                auction_status="unknown",
                lot_url=href,
                source_url=href,
                detail_level="list",
                raw_data={
                    "listing_page": page,
                    "individual_lot_identity_verified": True,
                    "classification_requires_detail": True,
                    "complete_source_inventory": False,
                    "safe_for_archival": False,
                    "reconcile_missing": False,
                },
                platform_name=PLATFORM_NAME,
                platform_code=PLATFORM_CODE,
            )
        )
    return lots


def _is_postback_link(node: Tag) -> bool:
    return "__doPostBack" in str(node.get("href") or "")


def find_postback_page_link(
    soup: BeautifulSoup | Tag, target_page: int
) -> Tag | None:
    exact = next(
        (
            node
            for node in soup.select("a[href]")
            if clean(node.get_text(" ", strip=True)) == str(target_page)
            and _is_postback_link(node)
        ),
        None,
    )
    if isinstance(exact, Tag):
        return exact
    if target_page > 1 and (target_page - 1) % 10 == 0:
        grouped = next(
            (
                node
                for node in soup.select("a[href]")
                if clean(node.get_text(" ", strip=True)) in {">>", "»"}
                and _is_postback_link(node)
            ),
            None,
        )
        return grouped if isinstance(grouped, Tag) else None
    return None


def has_next_page(html: str | bytes, page: int) -> bool:
    return find_postback_page_link(parse_soup(html), page + 1) is not None


def build_next_request(
    html: str | bytes, *, current_url: str, target_page: int
) -> tuple[str, dict[str, str]]:
    soup = parse_soup(html)
    link = find_postback_page_link(soup, target_page)
    if not isinstance(link, Tag):
        raise ZakazRfSchemaError(
            f"zakazrf.ru: page {target_page} postback target disappeared"
        )
    postback = _POSTBACK_RE.search(str(link.get("href") or ""))
    if not postback:
        raise ZakazRfSchemaError(
            f"zakazrf.ru: invalid page {target_page} postback"
        )
    data = {
        str(node.get("name")): str(node.get("value") or "")
        for node in soup.select("input[type=hidden][name]")
    }
    data["__EVENTTARGET"], data["__EVENTARGUMENT"] = postback.groups()
    form = link.find_parent("form") or soup.find("form")
    action = str(form.get("action") or "") if isinstance(form, Tag) else ""
    request_url = _safe_url(urljoin(current_url or CATALOGUE_URL, action))
    verify_catalogue_url(request_url)
    return request_url, data


class ZakazRfReadonlyClient:
    """Read-only ZakazRF client used by source-specific audits and future wiring."""

    RETRY_DELAYS_SECONDS = (1.0, 2.0, 4.0)

    def __init__(
        self, *, session: requests.Session | None = None, timeout: float = 25.0
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self._last_response: requests.Response | None = None
        self._last_page = 0
        self.session.headers.setdefault(
            "User-Agent",
            "TORGI/1.0 (+https://github.com/Maximiliamos/torgi; public read-only connector)",
        )

    def _check_response(self, response: requests.Response) -> requests.Response:
        if 300 <= response.status_code < 400:
            raise ZakazRfAccessError("zakazrf.ru: provider redirect was refused")
        if response.status_code in {401, 403, 429}:
            raise ZakazRfAccessError(
                f"zakazrf.ru: provider returned HTTP {response.status_code}"
            )
        response.raise_for_status()
        return response

    def _read(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        response = request_with_retries(
            self.session,
            method,
            _safe_url(url),
            delays=self.RETRY_DELAYS_SECONDS,
            **kwargs,
        )
        return self._check_response(response)

    def request_page(self, page: int) -> requests.Response:
        if page < 1:
            raise ValueError("page must be >= 1")
        if page == 1:
            response = self._read(
                "get",
                CATALOGUE_URL,
                timeout=self.timeout,
                allow_redirects=False,
            )
            verify_catalogue_url(response.url or CATALOGUE_URL)
            self._last_response, self._last_page = response, 1
            return response

        previous = self._last_response if self._last_page == page - 1 else None
        previous = previous or self._read(
            "get",
            CATALOGUE_URL,
            timeout=self.timeout,
            allow_redirects=False,
        )
        verify_catalogue_url(previous.url or CATALOGUE_URL)
        action, data = build_next_request(
            previous.content,
            current_url=previous.url or CATALOGUE_URL,
            target_page=page,
        )
        response = self._read(
            "post",
            action,
            data=data,
            timeout=self.timeout,
            allow_redirects=False,
        )
        verify_catalogue_url(response.url or action)
        self._last_response, self._last_page = response, page
        return response

    def search_page(self, page: int) -> CataloguePage:
        response = self.request_page(page)
        items = parse_catalogue_page(response.content, page=page)
        next_page = page + 1 if has_next_page(response.content, page) else None
        return CataloguePage(
            items=items,
            next_page=next_page,
            response_url=response.url or CATALOGUE_URL,
        )

    def fetch_lot(self, external_id: str) -> NormalizedLot:
        detail_url = detail_url_for_external_id(external_id)
        response = self._read(
            "get",
            detail_url,
            timeout=self.timeout,
            allow_redirects=False,
        )
        return parse_detail(
            response.content,
            detail_url=detail_url,
            external_id=external_id,
        )



class ZakazRfConnector(AuctionConnector):
    """Drop-in connector whose source behaviour is fully owned by this module."""

    source_id = SOURCE_ID
    capabilities = frozenset(
        {"search", "fetch_lot", "healthcheck", "detail_enrichment"}
    )
    safe_for_reconciliation = False

    def __init__(
        self, *, session: requests.Session | None = None, timeout: float = 25.0
    ) -> None:
        self.client = ZakazRfReadonlyClient(session=session, timeout=timeout)

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        page = max(1, int(cursor or 1))
        result = await asyncio.to_thread(self.client.search_page, page)
        return ConnectorPage(
            items=result.items,
            next_cursor=str(result.next_page) if result.next_page is not None else None,
            metadata={
                "source": SOURCE_ID,
                "page": page,
                "items_on_page": len(result.items),
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
                "maturity": "pilot",
                "response_url": result.response_url,
            },
        )

    async def fetch_lot(self, external_id: str) -> NormalizedLot:
        return await asyncio.to_thread(self.client.fetch_lot, external_id)

    async def enrich_lot(self, lot: NormalizedLot) -> NormalizedLot:
        detail = await self.fetch_lot(lot.external_id)
        return apply_detail(lot, detail, version=self.detail_enrichment_version)

    async def healthcheck(self) -> ConnectorHealth:
        try:
            result = await asyncio.to_thread(self.client.search_page, 1)
        except Exception as exc:
            return ConnectorHealth(
                "degraded", str(exc), {"source": SOURCE_ID}
            )
        status = "ok" if result.items else "degraded"
        return ConnectorHealth(
            status,
            f"matched {len(result.items)} public lot links",
            {
                "source": SOURCE_ID,
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
            },
        )
