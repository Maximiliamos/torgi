from __future__ import annotations

import asyncio
import hashlib
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from bankrotai.connectors.base import ConnectorHealth, ConnectorPage
from bankrotai.connectors.etp_sources.common import (
    EtpSearchFilters,
    EtpSourceProfile,
    HtmlEtpConnector,
    SourceAccessError,
    SourceSchemaError,
    parse_detail_html,
    parse_listing_html,
)
from bankrotai.connectors.etp_sources.source_specific import apply_detail
from bankrotai.domain import NormalizedLot


SOURCE_ID = "arbbitlot.ru"
LISTING_URL = "https://torgi.arbbitlot.ru/public/purchases-all/"
DETAIL_URL_TEMPLATE = "https://torgi.arbbitlot.ru/public/{sale_type}/lots/view/{lot_id}/"

PROFILE = EtpSourceProfile(
    SOURCE_ID,
    "АРБбитЛот",
    "arbbitlot",
    LISTING_URL,
    r"torgi\.arbbitlot\.ru/public/(auctions|contests|public-offers)/lots/view/(\d+)/",
    allowed_hosts=("torgi.arbbitlot.ru",),
    detail_url_template=DETAIL_URL_TEMPLATE,
    postback_pagination=True,
)


class ArbbitlotContractError(ValueError):
    """ARBbitLot public evidence does not prove the required per-lot contract."""


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _soup(html: str | bytes) -> BeautifulSoup:
    if isinstance(html, bytes):
        try:
            html = html.decode("utf-8")
        except UnicodeDecodeError:
            html = html.decode("windows-1251", errors="replace")
    return BeautifulSoup(html, "html.parser")


def _safe_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "torgi.arbbitlot.ru"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        raise ArbbitlotContractError("arbbitlot.ru: refused unsafe provider URL")
    return value


def row_identity(row: Tag) -> tuple[str | None, str | None]:
    """Read the displayed procedure number and lot number from one catalogue row."""
    if row.name != "tr":
        return None, None
    table = row.find_parent("table")
    cells = row.find_all("td", recursive=False)
    if table is None or not cells:
        return None, None

    headings: list[str] = []
    for heading_row in table.select("tr"):
        headers = heading_row.find_all("th", recursive=False)
        if headers:
            headings = [_clean(node.get_text(" ", strip=True)).lower() for node in headers]
            break

    procedure_number: str | None = None
    lot_number: str | None = None
    if len(headings) == len(cells):
        for index, heading in enumerate(headings):
            value = _clean(cells[index].get_text(" ", strip=True))
            if re.search(r"№\s*лота", heading):
                if re.fullmatch(r"\d{1,6}", value):
                    lot_number = value
            elif procedure_number is None and (
                heading == "№"
                or re.search(r"№\s*(?:торг|аукцион|конкурс|публичн)", heading)
            ):
                if re.fullmatch(r"\d{4,12}", value):
                    procedure_number = value

    if procedure_number is None:
        value = _clean(cells[0].get_text(" ", strip=True))
        if re.fullmatch(r"\d{4,12}", value):
            procedure_number = value

    # The live ARBbitLot catalogue currently renders the data rows with the
    # stable public-engine layout even when semantic <th> headers are not
    # available to the HTML parser: trade number, trade title, lot number,
    # individual lot link, price, ... .  Accept the positional lot number only
    # when cell 3 itself proves the individual provider lot URL.  This keeps
    # the fallback source-specific and prevents a neighbouring numeric cell
    # from becoming a lot identity.
    if lot_number is None and len(cells) >= 4:
        positional_lot = _clean(cells[2].get_text(" ", strip=True))
        has_individual_link = any(
            PROFILE.lot_url_re.search(
                urljoin(LISTING_URL, str(anchor.get("href") or "").strip())
            )
            for anchor in cells[3].select("a[href]")
        )
        if has_individual_link and re.fullmatch(r"\d{1,6}", positional_lot):
            lot_number = positional_lot

    return procedure_number, lot_number


def _decimal(raw: str) -> float | None:
    cleaned = re.sub(r"[\s\u00a0\u202f]", "", raw).replace(",", ".")
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    return amount if 0 < amount < 1e15 else None


def _labeled_rub_amount(value: str, label: str) -> float | None:
    """Require ARBbitLot's explicit RUB label before accepting a money value."""
    match = re.search(
        label
        + r"\s*,?\s*руб\.?\s*[:\-]?\s*"
        + r"(\d[\d\s\u00a0\u202f]{0,30}(?:[.,]\d{1,2})?)",
        value,
        re.IGNORECASE,
    )
    return _decimal(match.group(1)) if match else None


def detail_fields(value: str) -> dict[str, float | str | None]:
    """Parse only explicitly labelled individual-lot ARBbitLot fields."""
    step_percent = re.search(
        r"шаг\s*,?\s*%\s+от\s+начальн\w*\s+цен\w*\s*[:\-]?\s*"
        r"(\d{1,3}(?:[.,]\d{1,4})?)",
        value,
        re.IGNORECASE,
    )
    deposit_percent = re.search(
        r"(?:размер|способ\s+расч[её]та)\s+(?:обеспечен\w*|задатк\w*)"
        r"[^\d%]{0,80}(\d{1,3}(?:[.,]\d{1,4})?)\s*%",
        value,
        re.IGNORECASE,
    )
    trade = re.search(
        r"информация\s+(?:об|о)\s+(?:аукцион\w*|конкурс\w*|публичн\w*\s+предложен\w*)"
        r"\s*№\s*(\d{4,12})",
        value,
        re.IGNORECASE,
    )
    lot_number = re.search(r"информация\s+о\s+лоте\s*№\s*(\d{1,6})", value, re.IGNORECASE)
    return {
        "start_price": _labeled_rub_amount(value, r"начальн\w*\s+цен\w*"),
        "current_price": _labeled_rub_amount(value, r"текущ\w*\s+цен\w*"),
        "deposit_amount": _labeled_rub_amount(
            value, r"размер\s+(?:обеспечен\w*|задатк\w*)"
        ),
        "deposit_percent": (
            float(deposit_percent.group(1).replace(",", ".")) if deposit_percent else None
        ),
        "auction_step_amount": _labeled_rub_amount(value, r"шаг"),
        "auction_step_percent": (
            float(step_percent.group(1).replace(",", ".")) if step_percent else None
        ),
        "trade_id": trade.group(1) if trade else None,
        "lot_number": lot_number.group(1) if lot_number else None,
    }


def efrsb_classifier(value: str) -> str | None:
    """Return ARBbitLot's structured EFRSB classifier block, if present."""
    match = re.search(
        r"классификатор\s+ефрсб\s*:\s*(.{2,500}?)"
        r"(?=\s+(?:обеспечение\s+заявки|интервалы\s+снижения\s+цены|документы\b|"
        r"всего\s+подано\s+заявок|порядок\s+внесения|$))",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None
    classifier = _clean(match.group(1)).strip(" ;")
    return classifier or None


def classify_property(classifier: str | None, fallback: str) -> str:
    """Prefer the provider's structured classifier over free-text heuristics."""
    lowered = _clean(classifier or "").lower()
    if "земель" in lowered:
        return "land"
    if any(word in lowered for word in (
        "квартир", "комнат", "жил", "недвиж", "помещен", "здани", "гараж", "машино",
    )):
        return "real_estate"
    if any(word in lowered for word in ("автомоб", "транспорт", "прицеп")):
        return "vehicle"
    if any(word in lowered for word in ("право требован", "дебитор")):
        return "receivables"
    return fallback


def _address(value: str) -> str | None:
    match = re.search(
        r"(?:адрес(?:\s*\(местонахождение\))?|местоположение)\s*[:\-]?\s*"
        r"(.{5,300}?)(?=\s+(?:кадастров|площад|начальн|текущ|статус|шаг|"
        r"размер\s+обеспеч)\b)",
        value,
        re.IGNORECASE,
    )
    return _clean(match.group(1)) if match else None


def apply_listing_contract(lot: NormalizedLot, row: Tag, context: str) -> None:
    procedure_number, lot_number = row_identity(row)
    if procedure_number is None:
        raise ArbbitlotContractError("arbbitlot.ru: catalogue procedure_id is unverified")
    if lot_number is None:
        raise ArbbitlotContractError("arbbitlot.ru: catalogue lot_number is unverified")
    raw = dict(lot.raw_data or {})
    raw.update({
        "procedure_id": procedure_number,
        "lot_number": lot_number,
        "individual_lot_identity_verified": True,
    })
    lot.raw_data = raw
    lot.procedure_number = procedure_number
    lowered = _clean(context).lower()
    if "отменён организатором" in lowered or "отменен организатором" in lowered:
        lot.auction_status = "closed"


def _find_catalogue_row(soup: BeautifulSoup, lot_url: str | None) -> Tag | None:
    if not lot_url:
        return None
    for anchor in soup.select("a[href]"):
        absolute = urljoin(LISTING_URL, str(anchor.get("href") or ""))
        if absolute != lot_url:
            continue
        row = anchor.find_parent("tr")
        if isinstance(row, Tag):
            return row
    return None


def parse_catalogue_page(html: str | bytes, *, page: int = 1) -> list[NormalizedLot]:
    """Parse one official ARBbitLot catalogue page and prove row-level identity."""
    lots = parse_listing_html(PROFILE, html, page=page)
    soup = _soup(html)
    for lot in lots:
        row = _find_catalogue_row(soup, lot.lot_url)
        if row is None:
            raise ArbbitlotContractError(
                f"arbbitlot.ru: catalogue row missing for {lot.external_id}"
            )
        apply_listing_contract(lot, row, _clean(row.get_text(" ", strip=True)))
    return lots


def _external_id_from_detail_url(detail_url: str) -> str:
    _safe_url(detail_url)
    match = PROFILE.lot_url_re.search(detail_url)
    if not match:
        raise ArbbitlotContractError("arbbitlot.ru: detail URL does not match lot schema")
    return f"{SOURCE_ID}:{match.group(1)}-{match.group(2)}"


def apply_detail_contract(lot: NormalizedLot, value: str) -> None:
    fields = detail_fields(value)
    start_price = fields["start_price"]
    if start_price is None:
        raise ArbbitlotContractError(
            "arbbitlot.ru: individual detail does not prove an explicit start price"
        )
    if fields["trade_id"] is None:
        raise ArbbitlotContractError("arbbitlot.ru: detail procedure_id is unverified")
    if fields["lot_number"] is None:
        raise ArbbitlotContractError("arbbitlot.ru: detail lot_number is unverified")

    classifier = efrsb_classifier(value)
    lot.start_price = float(start_price)
    current_price = fields["current_price"]
    lot.current_price = float(current_price) if current_price is not None else None
    lot.address = _address(value) or lot.address
    lot.procedure_number = str(fields["trade_id"])
    lot.deposit_amount = (
        float(fields["deposit_amount"]) if fields["deposit_amount"] is not None else None
    )
    lot.deposit_percent = (
        float(fields["deposit_percent"]) if fields["deposit_percent"] is not None else None
    )
    lot.auction_step_amount = (
        float(fields["auction_step_amount"])
        if fields["auction_step_amount"] is not None
        else None
    )
    lot.auction_step_percent = (
        float(fields["auction_step_percent"])
        if fields["auction_step_percent"] is not None
        else None
    )
    lot.property_type = classifier
    lot.category = classify_property(classifier, lot.category)

    lowered = _clean(value).lower()
    if "отменён организатором" in lowered or "отменен организатором" in lowered:
        lot.auction_status = "closed"
    raw = dict(lot.raw_data or {})
    raw.update({
        "procedure_id": fields["trade_id"],
        "lot_number": fields["lot_number"],
        "per_lot_detail_verified": True,
        "price_unverified": False,
        "current_price_unverified": current_price is None,
        "efrsb_classifier": classifier,
    })
    lot.raw_data = raw


def parse_detail(
    html: str | bytes,
    *,
    detail_url: str,
    external_id: str | None = None,
) -> NormalizedLot:
    """Parse an official individual ARBbitLot lot card and fail closed on price/identity."""
    expected_external_id = _external_id_from_detail_url(detail_url)
    if external_id is not None and external_id != expected_external_id:
        raise ArbbitlotContractError("arbbitlot.ru: external_id does not match detail URL")
    lot = parse_detail_html(
        PROFILE,
        html,
        detail_url=detail_url,
        external_id=external_id or expected_external_id,
    )
    text_value = _clean(_soup(html).get_text(" ", strip=True))
    apply_detail_contract(lot, text_value)
    return lot


def verify_catalogue_detail_identity(listing: NormalizedLot, detail: NormalizedLot) -> None:
    if listing.source_system != SOURCE_ID or detail.source_system != SOURCE_ID:
        raise ArbbitlotContractError("arbbitlot.ru: source mismatch")
    if listing.external_id != detail.external_id:
        raise ArbbitlotContractError("arbbitlot.ru: catalogue/detail external_id mismatch")
    listing_raw = listing.raw_data or {}
    detail_raw = detail.raw_data or {}
    for key in ("procedure_id", "lot_number"):
        left = listing_raw.get(key)
        right = detail_raw.get(key)
        if left is None:
            raise ArbbitlotContractError(f"arbbitlot_catalogue_{key}_unverified")
        if right is None:
            raise ArbbitlotContractError(f"arbbitlot_detail_{key}_unverified")
        if str(left) != str(right):
            raise ArbbitlotContractError(f"arbbitlot_catalogue_detail_{key}_mismatch")

    if detail.start_price is None:
        raise ArbbitlotContractError("arbbitlot_detail_start_price_unverified")
    if (
        listing.start_price is not None
        and abs(float(listing.start_price) - float(detail.start_price)) > 0.01
    ):
        raise ArbbitlotContractError("arbbitlot_catalogue_detail_start_price_mismatch")
    if (
        listing.current_price is not None
        and detail.current_price is not None
        and abs(float(listing.current_price) - float(detail.current_price)) > 0.01
    ):
        raise ArbbitlotContractError("arbbitlot_catalogue_detail_current_price_mismatch")


def merge_verified_detail(
    listing: NormalizedLot,
    detail: NormalizedLot,
    *,
    version: int = 1,
) -> NormalizedLot:
    """Apply a verified ARBbitLot detail without source-specific shared-parser branches."""
    verify_catalogue_detail_identity(listing, detail)
    listing_current = listing.current_price
    result = apply_detail(listing, detail, version=version)
    result.deposit_amount = detail.deposit_amount
    result.deposit_percent = detail.deposit_percent
    result.auction_step_amount = detail.auction_step_amount
    result.auction_step_percent = detail.auction_step_percent
    result.property_type = detail.property_type or result.property_type
    result.category = detail.category or result.category

    raw = dict(result.raw_data or {})
    detail_raw = detail.raw_data or {}
    raw.update({
        "procedure_id": detail_raw.get("procedure_id"),
        "lot_number": detail_raw.get("lot_number"),
        "efrsb_classifier": detail_raw.get("efrsb_classifier"),
        "listing_current_price_used_as_fallback": (
            detail.current_price is None and listing_current is not None
        ),
    })
    result.raw_data = raw
    return result


def postback_link_for_page(soup: BeautifulSoup, page: int) -> Tag | None:
    """Return the provider control for the requested 1-based page."""
    direct = next((
        node for node in soup.select("a[href]")
        if _clean(node.get_text(" ", strip=True)) == str(page)
        and "__doPostBack" in str(node.get("href") or "")
    ), None)
    if isinstance(direct, Tag):
        return direct
    if page > 1 and (page - 1) % 10 == 0:
        grouped = next((
            node for node in soup.select("a[href]")
            if _clean(node.get_text(" ", strip=True)) == ">>"
            and "__doPostBack" in str(node.get("href") or "")
        ), None)
        return grouped if isinstance(grouped, Tag) else None
    return None


def has_next_page(html: str | bytes, page: int) -> bool:
    soup = _soup(html)
    if postback_link_for_page(soup, page + 1) is not None:
        return True
    return False


def is_terminal_page(html: str | bytes, page: int) -> bool:
    return not has_next_page(html, page)


def build_next_request(
    html: str | bytes,
    *,
    current_url: str,
    requested_page: int,
) -> tuple[str, dict[str, str]]:
    """Build the exact ASP.NET postback for the next requested page."""
    soup = _soup(html)
    link = postback_link_for_page(soup, requested_page)
    if link is None:
        raise ArbbitlotContractError(
            f"arbbitlot.ru: page {requested_page} postback target disappeared"
        )
    postback = re.search(
        r"__doPostBack\('([^']*)','([^']*)'\)",
        str(link.get("href") or ""),
    )
    if not postback:
        raise ArbbitlotContractError(
            f"arbbitlot.ru: invalid page {requested_page} postback"
        )
    form = link.find_parent("form")
    if not isinstance(form, Tag):
        form = soup.find("form")
    if not isinstance(form, Tag):
        raise ArbbitlotContractError("arbbitlot.ru: pagination form disappeared")

    data = {
        str(node.get("name")): str(node.get("value") or "")
        for node in form.select("input[type=hidden][name]")
    }
    data["__EVENTTARGET"], data["__EVENTARGUMENT"] = postback.groups()
    action = _safe_url(
        urljoin(current_url or LISTING_URL, str(form.get("action") or ""))
    )
    return action, data


class ArbbitlotConnector(HtmlEtpConnector):
    """Standalone ARBbitLot connector for source tests and read-only evidence."""

    profile = PROFILE
    capabilities = frozenset({"search", "fetch_lot", "healthcheck", "detail_enrichment"})
    detail_enrichment_version = 1

    def _request(self, page: int) -> requests.Response:
        if page <= 1:
            response = self._read_request(
                "get",
                LISTING_URL,
                timeout=self.timeout,
                allow_redirects=False,
            )
            self._last_response, self._last_page = response, 1
            return response

        previous = self._last_response if self._last_page == page - 1 else None
        if previous is None:
            if page != 2:
                raise SourceAccessError(
                    f"{SOURCE_ID}: sequential ASP.NET state required before page {page}"
                )
            previous = self._read_request(
                "get",
                LISTING_URL,
                timeout=self.timeout,
                allow_redirects=False,
            )
            self._last_response, self._last_page = previous, 1

        try:
            action, data = build_next_request(
                previous.content,
                current_url=previous.url or LISTING_URL,
                requested_page=page,
            )
        except ArbbitlotContractError as exc:
            raise SourceSchemaError(str(exc)) from exc
        response = self._read_request(
            "post",
            action,
            data=data,
            timeout=self.timeout,
            allow_redirects=False,
        )
        self._last_response, self._last_page = response, page
        return response

    async def search(self, filters, cursor: str | None = None) -> ConnectorPage:
        normalized = (
            filters if isinstance(filters, EtpSearchFilters)
            else EtpSearchFilters(**(filters or {}))
        )
        page = max(1, int(cursor or normalized.page))
        response = await asyncio.to_thread(self._request, page)
        fingerprint = hashlib.sha256(response.content).hexdigest()
        if page > 1 and fingerprint in {
            value for number, value in self._page_fingerprints.items() if number < page
        }:
            raise SourceSchemaError(f"{SOURCE_ID}: repeated catalogue page content at page {page}")
        self._page_fingerprints[page] = fingerprint
        try:
            lots = parse_catalogue_page(response.content, page=page)
        except ArbbitlotContractError as exc:
            raise SourceSchemaError(str(exc)) from exc
        if not lots:
            raise SourceSchemaError(f"{SOURCE_ID}: no lot links matched catalogue schema")
        next_cursor = str(page + 1) if has_next_page(response.content, page) else None
        return ConnectorPage(
            items=lots,
            next_cursor=next_cursor,
            metadata={
                "source": SOURCE_ID,
                "page": page,
                "items_on_page": len(lots),
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
                "response_url": response.url,
            },
        )

    async def fetch_lot(self, external_id: str) -> NormalizedLot:
        prefix = SOURCE_ID + ":"
        if not external_id.startswith(prefix):
            raise SourceSchemaError(f"{SOURCE_ID}: invalid external_id")
        identity = external_id[len(prefix):]
        match = re.fullmatch(r"(auctions|contests|public-offers)-(\d+)", identity)
        if not match:
            raise SourceSchemaError(f"{SOURCE_ID}: invalid external_id")
        detail_url = _safe_url(
            DETAIL_URL_TEMPLATE.format(sale_type=match.group(1), lot_id=match.group(2))
        )
        response = await asyncio.to_thread(
            self._read_request,
            "get",
            detail_url,
            timeout=self.timeout,
            allow_redirects=False,
        )
        try:
            return parse_detail(
                response.content,
                detail_url=detail_url,
                external_id=external_id,
            )
        except ArbbitlotContractError as exc:
            raise SourceSchemaError(str(exc)) from exc

    async def enrich_lot(self, lot: NormalizedLot) -> NormalizedLot:
        detail = await self.fetch_lot(lot.external_id)
        try:
            return merge_verified_detail(
                lot,
                detail,
                version=self.detail_enrichment_version,
            )
        except ArbbitlotContractError as exc:
            raise SourceSchemaError(str(exc)) from exc

    async def healthcheck(self) -> ConnectorHealth:
        try:
            page = await self.search({})
        except Exception as exc:
            return ConnectorHealth("degraded", str(exc), {"source": SOURCE_ID})
        return ConnectorHealth(
            "ok",
            f"matched {len(page.items)} verified catalogue rows",
            {"source": SOURCE_ID},
        )


__all__ = [
    "ArbbitlotConnector",
    "ArbbitlotContractError",
    "PROFILE",
    "SOURCE_ID",
    "apply_detail_contract",
    "apply_listing_contract",
    "build_next_request",
    "classify_property",
    "detail_fields",
    "efrsb_classifier",
    "has_next_page",
    "is_terminal_page",
    "merge_verified_detail",
    "parse_catalogue_page",
    "parse_detail",
    "postback_link_for_page",
    "row_identity",
    "verify_catalogue_detail_identity",
]
