from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from bankrotai.connectors.etp_sources.pagination import (
    get_form_params, has_next_page as _has_next_page, page_url as _page_url,
)
from bankrotai.connectors.etp_sources.row_isolation import has_multiple_lot_links
from bankrotai.connectors.base import AuctionConnector, ConnectorHealth, ConnectorPage
from bankrotai.connectors.transient_http import request_with_retries
from bankrotai.domain import NormalizedLot
from bankrotai.connectors.etp_sources.source_specific import (
    apply_detail,
    datetime_after,
    profit_initial_money,
    ruson_initial_money,
    sistematorg_lot_count,
)
from bankrotai.connectors.etp_sources.status_normalization import _status


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
    after = re.search(label + r"(?P<between>[^\d]{0,40})" + _MONEY_RE.pattern, value, re.IGNORECASE)
    # Never take another labelled field's value as the initial price.
    if after and re.search(r"текущ\w*\s+цен\w*|задатк\w*", after.group("between"), re.I):
        after = None
    raw: str | None = None
    if after:
        tail = value[after.end():after.end() + 100]
        deposit = re.search(r"размер\s+задатка", tail, re.IGNORECASE)
        if not deposit or _MONEY_RE.search(tail[deposit.end():deposit.end() + 60]):
            raw = after.group(2)
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
        r"текущ\w*\s+цен\w*(?P<between>[^\d]{0,40})" + _MONEY_RE.pattern,
        value, re.IGNORECASE,
    )
    if not match or re.search(r"задатк\w*|начальн\w*\s+цен\w*", match.group("between"), re.I):
        return None
    try:
        amount = float(match.group(2).replace(" ", "").replace("\xa0", "").replace(",", "."))
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


def _parse_sistematorg_procedure_lots(
    profile: EtpSourceProfile, html: str | bytes, *, page: int
) -> list[NormalizedLot]:
    """Represent source trade rows as individual *candidate* property lots.

    A trade_nid names the whole procedure, not a sellable asset. Neither
    a procedure-level price nor an unrelated lot's status can be attached
    to a candidate. Detail-level truth is still mandatory before promotion.
    """
    soup = _parse_soup(html)
    result: list[NormalizedLot] = []
    seen: set[str] = set()
    lot_marker = re.compile(r"лот\s*№\s*(\d{1,5})\s*[:.\-]?\s*", re.I)
    property_words = re.compile(
        r"недвижим|земельн|участок|квартир|комнат|помещен|здани|"
        r"гараж|машино.?мест|жилой\s+дом|склад|сооружен", re.I
    )
    for row in soup.select("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 3:
            continue
        procedure_urls: list[str] = []
        for anchor in row.select("a[href]"):
            procedure_urls.append(urljoin(profile.listing_url, str(anchor.get("href") or "")))
        clickable_nodes = ([row] if row.has_attr("onclick") else []) + row.select("[onclick]")
        for node in clickable_nodes:
            for candidate in re.findall(r"['\"]([^'\"]+)['\"]", str(node.get("onclick") or "")):
                if candidate.startswith(("/", "https://")):
                    procedure_urls.append(urljoin(profile.listing_url, candidate))
        trusted: list[tuple[str, re.Match[str]]] = []
        for url in procedure_urls:
            try:
                _safe_provider_url(profile, url)
            except SourceAccessError:
                continue
            matched = profile.lot_url_re.search(url)
            if matched:
                trusted.append((url, matched))
        if not trusted:
            continue
        detail_url, match = trusted[0]
        trade_id = match.group(1)
        # Layouts observed in saved pages use both three and four columns.
        # Scan the row's data cells rather than assigning a semantic meaning
        # to one fixed column index.
        cell_text = _clean(" ".join(cell.get_text(" ", strip=True) for cell in cells[1:]))
        reported_match = re.search(r"всего\s+лотов\s*:\s*(\d{1,5})", cell_text, re.I)
        reported_trade_lots = int(reported_match.group(1)) if reported_match else None
        markers = list(lot_marker.finditer(cell_text))
        if not markers:
            # There is no safe way to infer the lot number from a trade row.
            continue
        for index, marker in enumerate(markers):
            end = markers[index + 1].start() if index + 1 < len(markers) else len(cell_text)
            title = _clean(cell_text[marker.end():end])
            if len(title) < 8 or not property_words.search(title):
                continue
            external_id = f"{profile.source_id}:{trade_id}:{marker.group(1)}"
            if external_id in seen:
                continue
            seen.add(external_id)
            cadastre = _CADASTRAL_RE.search(title)
            result.append(NormalizedLot(
                external_id=external_id,
                source="sistematorg",
                source_system=profile.source_id,
                title=title[:500],
                description=title[:5000],
                category=_category(title),
                region_slug=None,
                region_name=None,
                address=None,
                cadastral_number=cadastre.group(1) if cadastre else None,
                vin=None,
                area=None,
                start_price=None,
                current_price=None,
                auction_status="unknown",
                lot_url=detail_url,
                source_url=detail_url,
                detail_level="search",
                raw_data={
                    "listing_page": page,
                    "procedure_id": trade_id,
                    "lot_number": marker.group(1),
                    "trade_level_listing": True,
                    "reported_trade_lots": reported_trade_lots,
                    "individual_lot_identity_verified": False,
                    "per_lot_detail_verified": False,
                    "price_unverified": True,
                    "status_unverified": True,
                    "complete_source_inventory": False,
                    "safe_for_archival": False,
                    "reconcile_missing": False,
                },
                platform_name=profile.platform_name,
                platform_code=profile.platform_code,
            ))
    return result


def _parse_multi_lot_catalogue(
    profile: EtpSourceProfile, html: str | bytes, *, page: int
) -> list[NormalizedLot] | None:
    if profile.source_id == "sistematorg.com":
        return _parse_sistematorg_procedure_lots(profile, html, page=page)
    if profile.source_id not in {"nistp.ru", "vertrades.ru"}:
        return None
    from bankrotai.connectors.registry.direct_etp_catalogue import PROFILE_BY_ID, parse_catalogue

    decoded = _parse_soup(html).decode()
    observations = parse_catalogue(PROFILE_BY_ID[profile.source_id], decoded, limit=100)
    if len(observations) == 100:
        raise SourceSchemaError(f"{profile.source_id}: page exceeds the verified per-page lot limit")
    identified = [item for item in observations if item.get("external_id")]
    if not identified:
        # NISTP is explicitly configured to return individual lot rows.
        # Never fall through to the generic single-link parser: its trade_nid
        # would otherwise become a phantom single asset.
        if profile.source_id == "nistp.ru":
            return []
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
                "catalogue_source_status_text": str(item.get("source_status_text") or ""),
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
        # CenterR publishes promotional lot links in its news sidebar on
        # every page. They are not entries of the paginated lot catalogue.
        if profile.source_id == "centerr.ru" and anchor.find_parent(
            class_=re.compile(r"\bnews-(?:short|body)\b", re.I)
        ) is not None:
            continue
        href = urljoin(profile.listing_url, str(anchor.get("href") or "").strip())
        try:
            _safe_provider_url(profile, href)
        except SourceAccessError:
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
            try:
                _safe_provider_url(profile, href)
            except SourceAccessError:
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
        ambiguous_row = has_multiple_lot_links(profile, container)
        context = _clean(container.get_text(" ", strip=True))
        if container.name == "tr" and not ambiguous_row:
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
        if ambiguous_row:
            context = title
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
        # Sistematorg's table lists *trades*, not individual property lots.
        # A row with several distinct lot links is equally unsafe: row-level
        # price/status/cadastre may belong to another lot in the procedure.
        trade_lots = sistematorg_lot_count(context) if profile.source_id == "sistematorg.com" else None
        if ambiguous_row or (profile.source_id == "sistematorg.com" and trade_lots != 1):
            price = None
            current_price = None
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
                "trade_level_listing": profile.source_id == "sistematorg.com",
                "reported_trade_lots": trade_lots,
                "ambiguous_multi_lot_row": ambiguous_row,
                "individual_lot_identity_verified": (
                    profile.source_id != "sistematorg.com" and not ambiguous_row
                ),
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
    if profile.source_id == "sistematorg.com":
        trade_lots = sistematorg_lot_count(text_value)
        if trade_lots != 1:
            raise SourceSchemaError(
                "sistematorg.com: cannot attribute a trade-level price to an individual lot"
            )
    if profile.source_id == "ausib.ru":
        lot_tables = soup.select("table[id^=lotNumber]")
        if len(lot_tables) != 1:
            raise SourceSchemaError(
                "ausib.ru: procedure detail does not prove exactly one independently identified lot"
            )
    match = profile.lot_url_re.search(detail_url)
    if external_id is None:
        if not match:
            raise SourceSchemaError(f"{profile.source_id}: detail URL does not match its verified schema")
        external_id = _external_id(profile, detail_url, match)
    if profile.source_id == "utpl.ru" and (
        match is None or external_id != _external_id(profile, detail_url, match)
    ):
        raise SourceSchemaError("utpl.ru: requested lot ID does not match individual detail URL")

    headings = [_clean(node.get_text(" ", strip=True)) for node in soup.select("h1, h2, h3")]
    title_candidates = [item for item in headings if len(item) >= 8 and "информац" not in item.lower()]
    title = max(title_candidates, key=len, default="")
    if not title:
        title = _label_value(
            text_value,
            r"(?:наименование имущества|предмет договора \(лота\)|предмет торгов|наименование)",
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
    if price is None and profile.source_id == "etp-profit.ru":
        price = profit_initial_money(text_value)
    if price is None and profile.source_id == "rus-on.ru":
        price = ruson_initial_money(text_value)
    current_price = _current_money(text_value)
    utpl_classifier: str | None = None
    utpl_status: str | None = None
    utpl_property_kinds: tuple[str, ...] = ()
    utpl_cadastral: str | None = None
    utpl_vin: str | None = None
    utpl_address: str | None = None
    utpl_application_deadline = None
    utpl_auction_at = None
    if profile.source_id == "utpl.ru":
        from bankrotai.connectors.etp_sources.utpl_contract import (
            UtplLotSchemaError, parse_utpl_detail_fields, normalize_utpl_status,
        )
        try:
            fields = parse_utpl_detail_fields(html)
        except UtplLotSchemaError as exc:
            raise SourceSchemaError(str(exc)) from exc
        title = str(fields["title"])
        price = fields["initial_price"]
        current_price = fields["current_price"]
        utpl_status = fields["source_status_text"]
        utpl_classifier = fields["classifier"]
        utpl_property_kinds = tuple(str(value) for value in fields["property_kinds"])
        # The procedure header may describe unrelated assets and rights.
        category = str(fields["category"])
        utpl_cadastral = fields["cadastral_number"]
        utpl_vin = fields["vin"]
        utpl_address = fields["address"]
        area = fields["area"]
        utpl_application_deadline = fields["application_deadline"]
        utpl_auction_at = fields["auction_at"]
    else:
        category = _category(text_value)
    return NormalizedLot(
        external_id=external_id,
        source=profile.source_id.removesuffix(".ru").removesuffix(".com"),
        source_system=profile.source_id,
        title=title[:500],
        description=text_value[:5000],
        category=category,
        region_slug=None,
        region_name=None,
        address=(utpl_address if profile.source_id == "utpl.ru" else None),
        cadastral_number=(
            utpl_cadastral if profile.source_id == "utpl.ru"
            else (cadastral.group(1) if cadastral else None)
        ),
        vin=(
            utpl_vin if profile.source_id == "utpl.ru"
            else (vin.group(0).upper() if vin else None)
        ),
        area=area,
        start_price=price,
        current_price=current_price,
        auction_status=(normalize_utpl_status(utpl_status) if profile.source_id == "utpl.ru" else _status(text_value)),
        lot_url=detail_url,
        source_url=detail_url,
        detail_level="detail",
        raw_data={
            "complete_source_inventory": profile.complete_source_inventory,
            "safe_for_archival": profile.safe_for_archival,
            "reconcile_missing": False,
            **({
                "utpl_classifier": utpl_classifier,
                "utpl_property_kinds": list(utpl_property_kinds),
                "utpl_lot_section_verified": True,
            } if profile.source_id == "utpl.ru" else {}),
        },
        platform_name=profile.platform_name,
        platform_code=profile.platform_code,
        procedure_number=procedure_number,
        bankruptcy_case_number=case_number,
        application_deadline=(
            utpl_application_deadline if profile.source_id == "utpl.ru"
            else datetime_after(
                text_value,
                r"(?:окончание|дата окончания)\s+(?:приема|приёма|представления)\s+заявок",
            )
        ),
        auction_at=(
            utpl_auction_at if profile.source_id == "utpl.ru"
            else datetime_after(text_value, r"дата проведения")
        ),
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
        _safe_provider_url(profile, href)
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
            application_deadline=datetime_after(
                f"Дата окончания представления заявок {raw.get('RequestStopDate') or ''}",
                r"дата окончания представления заявок",
            ),
            # AuctionResultDate describes the result, not the start of bidding.
            # Never publish it as auction_at without source-specific evidence.
            auction_at=None,
        ))
    return lots


class HtmlEtpConnector(AuctionConnector):
    profile: EtpSourceProfile
    capabilities = frozenset({"search", "fetch_lot", "healthcheck"})
    safe_for_reconciliation = False

    def __init__(self, *, session: requests.Session | None = None, timeout: float = 25.0) -> None:
        self.source_id = self.profile.source_id
        self.session = session or requests.Session()
        self.timeout = timeout
        self._last_response: requests.Response | None = None
        self._last_page = 0
        self._page_fingerprints: dict[int, str] = {}
        self.session.headers.setdefault(
            "User-Agent",
            "TORGI/1.0 (+https://github.com/Maximiliamos/torgi; public read-only connector)",
        )

    RETRY_DELAYS_SECONDS = (1.0, 2.0, 4.0)

    def _check_response(self, response: requests.Response) -> requests.Response:
        if 300 <= response.status_code < 400:
            raise SourceAccessError(f"{self.source_id}: provider redirect was refused")
        if response.status_code in {401, 403, 429}:
            raise SourceAccessError(f"{self.source_id}: provider returned HTTP {response.status_code}")
        response.raise_for_status()
        return response

    def _read_request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        response = request_with_retries(
            self.session, method, url, delays=self.RETRY_DELAYS_SECONDS, **kwargs
        )
        return self._check_response(response)

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
            response = self._read_request("post",
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
            )
            self._last_response, self._last_page = response, page
            return response
        supports_form = bool(self.profile.form_page_field or self.profile.postback_pagination)
        if page > 1 and not self.profile.supports_get_pagination and not supports_form:
            raise SourceAccessError(f"{self.source_id}: pagination requires a provider-specific browser/form contract")
        if page <= 1 or self.profile.supports_get_pagination:
            page_url = _safe_provider_url(self.profile, _page_url(self.profile, page))
            response = self._read_request(
                "get", page_url, timeout=self.timeout, allow_redirects=False
            )
            self._last_response, self._last_page = response, page
            return response

        previous = self._last_response if self._last_page == page - 1 else None
        previous = previous or self._read_request(
            "get",
            _safe_provider_url(self.profile, self.profile.listing_url),
            timeout=self.timeout,
            allow_redirects=False,
        )
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
            # NISTP's public nodesearch form uses GET for pagination.
            # Honour the actual HTML form method, retaining every checked
            # filter and repeated input name. POST can return a plausible
            # page-1 document even when the user requested page 2.
            method = str(form.get("method") or "get").strip().lower()
            if method == "get":
                params = get_form_params(form, self.profile.form_page_field, page)
                # Native browser GET submissions replace the action's query
                # with form fields. Appending params to previous.url would
                # duplicate pagenum/view_type and silently reuse wrong filters.
                parsed_action = urlparse(action)
                get_action = parsed_action._replace(query="", fragment="").geturl()
                response = self._read_request(
                    "get", get_action, params=params, timeout=self.timeout, allow_redirects=False,
                )
                self._last_response, self._last_page = response, page
                return response
            if method != "post":
                raise SourceSchemaError(
                    f"{self.source_id}: unsupported pagination form method"
                )
        else:
            if self.profile.source_id == "utpl.ru":
                from bankrotai.connectors.etp_sources.utpl_contract import utpl_next_postback
                postback_values = utpl_next_postback(previous.content, page - 1)
                if postback_values is None:
                    raise SourceSchemaError(
                        f"{self.source_id}: page {page} provider postback target disappeared"
                    )
            else:
                link = next((
                    node for node in soup.select("a[href]")
                    if _clean(node.get_text(" ", strip=True)) == str(page)
                    and "__doPostBack" in str(node.get("href") or "")
                ), None)
                if not isinstance(link, Tag):
                    raise SourceSchemaError(f"{self.source_id}: page {page} postback target disappeared")
                postback = re.search(
                    r"__doPostBack\('([^']*)','([^']*)'\)",
                    str(link.get("href") or ""),
                )
                if not postback:
                    raise SourceSchemaError(f"{self.source_id}: invalid page {page} postback")
                postback_values = postback.groups()
            data = {
                str(node.get("name")): str(node.get("value") or "")
                for node in soup.select("input[type=hidden][name]")
            }
            data["__EVENTTARGET"], data["__EVENTARGUMENT"] = postback_values
            action = _safe_provider_url(
                self.profile, previous.url or self.profile.listing_url
            )
        response = self._read_request(
            "post", action, data=data, timeout=self.timeout, allow_redirects=False
        )
        self._last_response, self._last_page = response, page
        return response

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        normalized = filters if isinstance(filters, EtpSearchFilters) else EtpSearchFilters(**(filters or {}))
        page = max(1, int(cursor or normalized.page))
        response = await asyncio.to_thread(self._request, page)
        fingerprint = hashlib.sha256(response.content).hexdigest()
        if page > 1 and fingerprint in {
            value for number, value in self._page_fingerprints.items() if number < page
        }:
            raise SourceSchemaError(
                f"{self.source_id}: repeated catalogue page content at page {page}"
            )
        self._page_fingerprints[page] = fingerprint
        if self.profile.api_kind == "sberbank_ast":
            payload = response.json()
            lots = parse_sberbank_json(self.profile, payload, page=page)
            total = int(payload.get("Total") or 0)
            next_cursor = str(page + 1) if page * 21 < total else None
        else:
            lots = parse_listing_html(self.profile, response.content, page=page)
            total = None
            next_cursor = str(page + 1) if lots and _has_next_page(self.profile, response.content, page) else None
        structurally_valid_empty_nistp_page = False
        if not lots and self.source_id == "nistp.ru":
            from bankrotai.connectors.registry.direct_etp_catalogue import (
                PROFILE_BY_ID, parse_nistp_lot_observations,
            )
            observations = parse_nistp_lot_observations(
                PROFILE_BY_ID["nistp.ru"], _parse_soup(response.content).decode(), limit=100,
            )
            structurally_valid_empty_nistp_page = bool(observations)
            if structurally_valid_empty_nistp_page:
                next_cursor = (
                    str(page + 1)
                    if _has_next_page(self.profile, response.content, page) else None
                )
        if not lots and not structurally_valid_empty_nistp_page:
            raise SourceSchemaError(
                f"{self.source_id}: no lot links matched the verified listing schema on page {page}"
            )
        if not (
            self.profile.supports_get_pagination or self.profile.form_page_field
            or self.profile.postback_pagination or self.profile.api_kind
        ):
            raise SourceSchemaError(
                f"{self.source_id}: public listing exists but the pagination/last-page contract is not verified"
            )
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
        if self.source_id == "nistp.ru":
            from bankrotai.connectors.etp_sources.nistp_detail_adapter import fetch_nistp_lot
            return await fetch_nistp_lot(self, external_id)
        if self.source_id == "vertrades.ru":
            raise SourceAccessError(
                f"{self.source_id}: per-lot detail isolation inside a shared trade card is not proven"
            )
        template = self.profile.detail_url_template
        if not template:
            raise SourceAccessError(f"{self.source_id}: detail URL template is not proven")
        identity = external_id.removeprefix(self.source_id + ":")
        if self.source_id == "sistematorg.com":
            identity = identity.split(":", 1)[0]
            if not identity.isdigit():
                raise SourceSchemaError("sistematorg.com: invalid procedure identity")
        identity_path = identity
        if self.profile.detail_id_separator:
            identity_path = identity.replace("-", self.profile.detail_id_separator)
        sale_type, separator, lot_id = identity.rpartition("-")
        profit_parts = identity.split("-") if self.source_id == "etp-profit.ru" else []
        if self.source_id == "etp-profit.ru" and (
            len(profit_parts) != 3
            or profit_parts[0] not in {"oaof", "otpp"}
            or not all(part.isdigit() for part in profit_parts[1:])
        ):
            raise SourceSchemaError("etp-profit.ru: invalid kind/trade/lot identity")
        detail_url = template.format(
            identity=identity,
            identity_path=identity_path,
            sale_type=sale_type if separator else "",
            lot_id=lot_id if separator else identity,
            profit_kind=profit_parts[0] if profit_parts else "",
            profit_trade_id=profit_parts[1] if profit_parts else "",
            profit_lot_id=profit_parts[2] if profit_parts else "",
        )
        detail_url = _safe_provider_url(self.profile, detail_url)
        response = await asyncio.to_thread(
            self._read_request, "get", detail_url,
            timeout=self.timeout, allow_redirects=False,
        )
        return parse_detail_html(
            self.profile,
            response.content,
            detail_url=detail_url,
            external_id=external_id,
        )

    async def enrich_lot(self, lot: NormalizedLot) -> NormalizedLot:
        if self.source_id == "nistp.ru":
            from bankrotai.connectors.etp_sources.nistp_detail_adapter import enrich_nistp_lot
            return await enrich_nistp_lot(self, lot)
        detail = await self.fetch_lot(lot.external_id)
        return apply_detail(lot, detail, version=self.detail_enrichment_version)

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
        pagination_verified = bool(
            self.profile.supports_get_pagination or self.profile.form_page_field
            or self.profile.postback_pagination or self.profile.api_kind
        )
        status = "ok" if lots and pagination_verified else "degraded"
        message = (f"matched {len(lots)} public lot links" if pagination_verified
                   else "catalogue observed; pagination and last page not verified")
        return ConnectorHealth(status, message, {"source": self.source_id})


def connector_type(profile: EtpSourceProfile) -> type[HtmlEtpConnector]:
    class ProfiledConnector(HtmlEtpConnector):
        pass

    ProfiledConnector.profile = profile
    if profile.source_id in {
        "etp-profit.ru", "ausib.ru", "nistp.ru", "sistematorg.com", "rus-on.ru",
        "utpl.ru",
    }:
        ProfiledConnector.capabilities = frozenset({
            "search", "fetch_lot", "healthcheck", "detail_enrichment",
        })
    if profile.source_id == "vertrades.ru":
        ProfiledConnector.capabilities = frozenset({"search", "healthcheck"})
    ProfiledConnector.__name__ = "".join(part.title() for part in re.split(r"[^a-z0-9]+", profile.source_id)) + "Connector"
    ProfiledConnector.__qualname__ = ProfiledConnector.__name__
    return ProfiledConnector
