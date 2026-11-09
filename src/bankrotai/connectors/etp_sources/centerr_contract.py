"""Strict CenterR catalogue/detail contract helpers.

Only public read-only HTML is parsed. A CenterR provider lot identity is the
canonical /public/<sale-type>/lots/view/<provider-lot-id>/ URL. The visible
trade number and lot number are retained as independent cross-checks.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from bankrotai.connectors.etp_sources.source_specific import datetime_after
from bankrotai.connectors.etp_sources.status_normalization import _status
from bankrotai.domain import NormalizedLot


BASE_URL = "https://bankrupt.centerr.ru/public/purchases-all/"
HOST = "bankrupt.centerr.ru"
LOT_PATH_RE = re.compile(
    r"^/public/(?P<sale_type>auctions|contests|public-offers)/lots/view/"
    r"(?P<provider_lot_id>[1-9]\d{0,11})/?$",
    re.I,
)
CADASTRE_RE = re.compile(r"(?<!\d)(\d{2}:\d{1,3}(?::\d{1,9}){2,4})(?!\d)")
AREA_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*(?:м|метр(?:а|ов)?)|м[²2])",
    re.I,
)
PROPERTY_RE = re.compile(
    r"недвижим|земельн|участок|квартир|комнат|помещен|здани|"
    r"гараж|машино.?мест|жилой\s+дом|склад|сооружен|апартамент",
    re.I,
)
VEHICLE_RE = re.compile(r"автомоб|транспорт|\bVIN\b|прицеп|снегоход", re.I)
RECEIVABLE_RE = re.compile(r"право\s+требован|дебитор", re.I)


class CenterRContractError(ValueError):
    """Public CenterR HTML contradicts the verified lot identity contract."""


@dataclass(frozen=True, slots=True)
class CenterRCatalogueLot:
    external_id: str
    provider_lot_id: str
    sale_type: str
    trade_id: str
    lot_number: str
    detail_url: str
    title: str
    listing_price: float | None
    status_text: str
    cadastral_number: str | None
    area: float | None
    category: str


@dataclass(frozen=True, slots=True)
class CenterRDetailFacts:
    external_id: str
    provider_lot_id: str
    sale_type: str
    trade_id: str
    lot_number: str
    title: str
    status_text: str
    start_price: float | None
    current_price: float | None
    cadastral_number: str | None
    area: float | None
    address: str | None
    category: str
    efrsb_classifier: str | None
    application_start_at: datetime | None
    application_deadline: datetime | None
    auction_at: datetime | None


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _category(value: str) -> str:
    lowered = value.lower()
    if "земельные участки" in lowered or re.search(r"земельн|участок", lowered):
        return "land"
    if PROPERTY_RE.search(value):
        return "real_estate"
    if VEHICLE_RE.search(value):
        return "vehicle"
    if RECEIVABLE_RE.search(value):
        return "receivables"
    if re.search(r"аренд|право пользован", lowered):
        return "lease_right"
    if re.search(r"оборудован|станк|машины и оборудование", lowered):
        return "equipment"
    return "other"


def _efrsb_classifier(value: str) -> str | None:
    match = re.search(
        r"Классификатор\s+ЕФРСБ:\s*(.+?)"
        r"(?=\s+(?:Интервалы\s+снижения\s+цены|Обеспечение\s+заявки|"
        r"Документы\b|№\s+Дата\s+поступления|$))",
        value,
        re.I,
    )
    if not match:
        return None
    classifier = _clean(match.group(1))
    return classifier[:1000] if classifier else None


def _money(value: str) -> float | None:
    cleaned = _clean(value)
    cleaned = re.sub(r"(?:₽|руб(?:\.|лей)?)$", "", cleaned, flags=re.I).strip()
    cleaned = cleaned.replace(" ", "").replace("\u202f", "").replace(",", ".")
    if not re.fullmatch(r"\d{1,14}(?:\.\d{1,2})?", cleaned):
        return None
    amount = float(cleaned)
    return amount if 0 < amount < 1e15 else None


def _money_after(text: str, label: str) -> float | None:
    match = re.search(
        label + r"\s*:?\s*(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)",
        text,
        re.I,
    )
    return _money(match.group(1)) if match else None


def _area(value: str) -> float | None:
    match = AREA_RE.search(value)
    return float(match.group(1).replace(",", ".")) if match else None


def _address(value: str) -> str | None:
    for pattern in (
        r"по адресу:\s*(.+?)(?=\s+(?:с кадастровым номером|кадастровый номер|Дата\b|$))",
        r"Почтовый адрес ориентира:\s*(.+?)(?=\s*,?\s*(?:площадь|Дата\b|$))",
        r"Адрес \(местоположение\):\s*(.+?)(?=\s+(?:Площадь|Кадастровый номер|Дата\b|$))",
    ):
        match = re.search(pattern, value, re.I)
        if match:
            result = _clean(match.group(1)).strip(" .;,")
            if 5 <= len(result) <= 800:
                return result
    return None


def _identity_from_url(detail_url: str) -> tuple[str, str, str]:
    parsed = urlsplit(detail_url)
    if parsed.scheme != "https" or parsed.hostname != HOST or parsed.port is not None:
        raise CenterRContractError("CenterR detail URL must use the exact HTTPS provider host")
    match = LOT_PATH_RE.fullmatch(parsed.path)
    if not match or parsed.query or parsed.fragment:
        raise CenterRContractError("CenterR detail URL does not contain a canonical provider lot ID")
    sale_type = match.group("sale_type").lower()
    provider_lot_id = match.group("provider_lot_id")
    return sale_type, provider_lot_id, f"centerr.ru:{sale_type}-{provider_lot_id}"


def parse_next_page_postback(
    html: str | bytes,
    *,
    next_page: int,
) -> tuple[str, str, dict[str, str]] | None:
    """Return the exact ASP.NET postback needed for the next CenterR page.

    Absence of a matching numeric/next control is the terminal-page signal.
    Hidden form values are returned unchanged so the caller can issue the
    provider-owned POST without inventing pagination state.
    """
    if next_page < 2:
        raise ValueError("CenterR next_page must be >= 2")
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[tuple[str, str, str]] = []
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "")
        if "__doPostBack" not in href:
            continue
        parsed = re.search(r"__doPostBack\('([^']*)','([^']*)'\)", href)
        if parsed:
            candidates.append((
                _clean(anchor.get_text(" ", strip=True)),
                parsed.group(1),
                parsed.group(2),
            ))
    selected = next((item for item in candidates if item[0] == str(next_page)), None)
    if selected is None:
        selected = next((item for item in candidates if item[0] in {">>", "››", "»"}), None)
    if selected is None:
        return None
    hidden = {
        str(node.get("name")): str(node.get("value") or "")
        for node in soup.select("input[type=hidden][name]")
    }
    return selected[1], selected[2], hidden


def summarize_catalogue_identities(rows: list[CenterRCatalogueLot]) -> dict[str, object]:
    """Summarize cross-page lot identity without silently deduplicating conflicts."""
    identities: dict[str, tuple[str, str, str, str]] = {}
    collisions: list[str] = []
    ordered_ids: list[str] = []
    for row in rows:
        current = (row.trade_id, row.lot_number, row.detail_url, row.title)
        previous = identities.get(row.external_id)
        if previous is not None and previous != current:
            collisions.append(row.external_id)
        identities[row.external_id] = current
        ordered_ids.append(row.external_id)
    unique_ids = sorted(set(ordered_ids))
    return {
        "unique_ids": unique_ids,
        "unique_count": len(unique_ids),
        "duplicate_same_identity_count": len(ordered_ids) - len(unique_ids),
        "collisions": sorted(set(collisions)),
        "id_digest": hashlib.sha256("\n".join(unique_ids).encode()).hexdigest(),
    }


def verify_identity(
    catalogue: CenterRCatalogueLot,
    detail: CenterRDetailFacts,
) -> None:
    """Fail closed unless catalogue and detail prove the same individual lot."""
    if (
        detail.external_id != catalogue.external_id
        or detail.trade_id != catalogue.trade_id
        or detail.lot_number != catalogue.lot_number
        or detail.provider_lot_id != catalogue.provider_lot_id
        or detail.sale_type != catalogue.sale_type
    ):
        raise CenterRContractError(
            "CenterR catalogue/detail identity mismatch: "
            f"catalogue={catalogue.trade_id}/{catalogue.lot_number}/"
            f"{catalogue.provider_lot_id}; "
            f"detail={detail.trade_id}/{detail.lot_number}/{detail.provider_lot_id}"
        )


def parse_catalogue_lots(html: str | bytes) -> list[CenterRCatalogueLot]:
    soup = BeautifulSoup(html, "html.parser")
    result: list[CenterRCatalogueLot] = []
    by_external_id: dict[str, CenterRCatalogueLot] = {}

    for row in soup.select("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 9:
            continue
        trade_id = _clean(cells[0].get_text(" ", strip=True))
        lot_number = _clean(cells[2].get_text(" ", strip=True))
        if not re.fullmatch(r"\d{4,12}", trade_id) or not re.fullmatch(r"\d{1,5}", lot_number):
            continue

        links: dict[str, tuple[str, str, str]] = {}
        for anchor in cells[3].select("a[href]"):
            absolute = urljoin(BASE_URL, str(anchor.get("href") or "").strip())
            try:
                sale_type, provider_lot_id, external_id = _identity_from_url(absolute)
            except CenterRContractError:
                continue
            links[absolute] = (sale_type, provider_lot_id, external_id)
        if not links:
            continue
        if len(links) != 1:
            raise CenterRContractError(
                f"CenterR row {trade_id}/{lot_number} contains multiple provider lot links"
            )

        detail_url, (sale_type, provider_lot_id, external_id) = next(iter(links.items()))
        title = _clean(cells[3].get_text(" ", strip=True))
        if not title:
            raise CenterRContractError(f"CenterR lot {external_id} has an empty catalogue title")
        status = _clean(cells[8].get_text(" ", strip=True))
        cadastral = CADASTRE_RE.search(title)
        item = CenterRCatalogueLot(
            external_id=external_id,
            provider_lot_id=provider_lot_id,
            sale_type=sale_type,
            trade_id=trade_id,
            lot_number=lot_number,
            detail_url=detail_url,
            title=title,
            listing_price=_money(cells[4].get_text(" ", strip=True)),
            status_text=status,
            cadastral_number=cadastral.group(1) if cadastral else None,
            area=_area(title),
            category=_category(title),
        )
        previous = by_external_id.get(external_id)
        if previous and (
            previous.trade_id != item.trade_id
            or previous.lot_number != item.lot_number
            or previous.title != item.title
            or previous.detail_url != item.detail_url
        ):
            raise CenterRContractError(
                f"CenterR provider lot collision for {external_id}: "
                f"{previous.trade_id}/{previous.lot_number} vs {item.trade_id}/{item.lot_number}"
            )
        if previous is None:
            by_external_id[external_id] = item
            result.append(item)
    return result


def parse_detail_facts(html: str | bytes, *, detail_url: str) -> CenterRDetailFacts:
    sale_type, provider_lot_id, external_id = _identity_from_url(detail_url)
    soup = BeautifulSoup(html, "html.parser")
    text = _clean(soup.get_text(" ", strip=True))
    if len(text) < 120:
        raise CenterRContractError("CenterR detail page has insufficient content")

    trade = re.search(
        r"Информация\s+(?:об\s+аукционе|о\s+публичном\s+предложении|"
        r"о\s+конкурсе|о\s+торгах)\s*№\s*(\d{4,12})",
        text,
        re.I,
    )
    lot = re.search(r"Информация\s+о\s+лоте\s*№\s*(\d{1,5})", text, re.I)
    if not trade or not lot:
        raise CenterRContractError("CenterR detail is missing trade ID or lot number")

    segment = text[lot.start():]
    title_match = re.search(
        r"Наименование:\s*(.+?)(?=\s+(?:Дата\s+(?:начала|окончания|проведения)|"
        r"Статус:|Текущая\s+цена|Начальная\s+цена|Порядок\s+оформления))",
        segment,
        re.I,
    )
    if not title_match:
        raise CenterRContractError("CenterR detail is missing an isolated lot title")
    title = _clean(title_match.group(1))

    status_match = re.search(
        r"Статус:\s*(.+?)(?=\s+(?:Дата\s+окончания|Текущая\s+цена|"
        r"Начальная\s+цена|Порядок\s+оформления))",
        segment,
        re.I,
    )
    status = _clean(status_match.group(1)) if status_match else ""
    cadastral = CADASTRE_RE.search(title)
    classifier = _efrsb_classifier(segment)
    return CenterRDetailFacts(
        external_id=external_id,
        provider_lot_id=provider_lot_id,
        sale_type=sale_type,
        trade_id=trade.group(1),
        lot_number=lot.group(1),
        title=title,
        status_text=status,
        start_price=_money_after(segment, r"Начальная\s+цена,?\s*руб\.?"),
        current_price=_money_after(segment, r"Текущая\s+цена,?\s*руб\.?"),
        cadastral_number=cadastral.group(1) if cadastral else None,
        area=_area(title),
        address=_address(title),
        category=_category(classifier or title),
        efrsb_classifier=classifier,
        application_start_at=datetime_after(
            segment, r"Дата\s+начала\s+представления\s+заявок(?:\s+на\s+участие)?"
        ),
        application_deadline=datetime_after(
            segment, r"Дата\s+окончания\s+представления\s+заявок(?:\s+на\s+участие)?"
        ),
        auction_at=datetime_after(segment, r"Дата\s+проведения"),
    )


def parse_listing(html: str | bytes, *, page: int) -> list[NormalizedLot]:
    lots: list[NormalizedLot] = []
    for item in parse_catalogue_lots(html):
        lots.append(NormalizedLot(
            external_id=item.external_id,
            source="centerr",
            source_system="centerr.ru",
            title=item.title[:500],
            description=item.title[:5000],
            category=item.category,
            region_slug=None,
            region_name=None,
            address=_address(item.title),
            cadastral_number=item.cadastral_number,
            vin=None,
            area=item.area,
            start_price=item.listing_price,
            current_price=None,
            auction_status=_status(item.status_text),
            lot_url=item.detail_url,
            source_url=item.detail_url,
            detail_level="list",
            raw_data={
                "listing_page": page,
                "trade_id": item.trade_id,
                "lot_number": item.lot_number,
                "provider_lot_id": item.provider_lot_id,
                "sale_type": item.sale_type,
                "individual_lot_identity_verified": True,
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
            },
            platform_name="Центр реализации",
            platform_code="centerr",
            procedure_number=item.trade_id,
        ))
    return lots


def parse_detail(
    html: str | bytes,
    *,
    detail_url: str,
    external_id: str | None = None,
) -> NormalizedLot:
    facts = parse_detail_facts(html, detail_url=detail_url)
    if external_id is not None and external_id != facts.external_id:
        raise CenterRContractError(
            f"CenterR detail substitution: expected {external_id}, got {facts.external_id}"
        )
    return NormalizedLot(
        external_id=facts.external_id,
        source="centerr",
        source_system="centerr.ru",
        title=facts.title[:500],
        description=facts.title[:5000],
        category=facts.category,
        region_slug=None,
        region_name=None,
        address=facts.address,
        cadastral_number=facts.cadastral_number,
        vin=None,
        area=facts.area,
        start_price=facts.start_price,
        current_price=facts.current_price,
        auction_status=_status(facts.status_text),
        lot_url=detail_url,
        source_url=detail_url,
        detail_level="detail",
        raw_data={
            "trade_id": facts.trade_id,
            "lot_number": facts.lot_number,
            "provider_lot_id": facts.provider_lot_id,
            "sale_type": facts.sale_type,
            "efrsb_classifier": facts.efrsb_classifier,
            "detail_identity_verified": True,
            "complete_source_inventory": False,
            "safe_for_archival": False,
            "reconcile_missing": False,
        },
        platform_name="Центр реализации",
        platform_code="centerr",
        procedure_number=facts.trade_id,
        application_start_at=facts.application_start_at,
        application_deadline=facts.application_deadline,
        auction_at=facts.auction_at,
        property_type=facts.efrsb_classifier,
    )
