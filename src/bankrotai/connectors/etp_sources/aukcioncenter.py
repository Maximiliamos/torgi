"""AukcionCenter source-specific lot isolation and detail parsing.

The public register is procedure-oriented: one row can contain several lots from
unrelated asset classes.  This module therefore never treats purchase/general
id as a lot id and never accepts procedure-level money as a per-lot price.
"""
from __future__ import annotations

from datetime import datetime
import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from bankrotai.domain import NormalizedLot
from bankrotai.connectors.etp_sources.status_normalization import _status

if TYPE_CHECKING:
    from bankrotai.connectors.etp_sources.common import EtpSourceProfile


_TRADE_RE = re.compile(r"\b(?P<trade>\d{3,9}-(?:ОАОФ|ОТПП|ЗТПП|ОКОФ|ОКЗФ|ОАЗФ))\b", re.I)
_LOT_RE = re.compile(r"Лот\s*№\s*(?P<lot>\d{1,5})\s*[:.\-]?\s*", re.I)
_GENERAL_PATH_RE = re.compile(r"/(?:etp/)?trade/view/purchase/general\.html$", re.I)
_INNER_LOTS_PATH = "/etp/trade/inner-view-lots.html"
_INNER_LOTS_PATH_RE = re.compile(r"/etp/trade/inner-view-lots\.html$", re.I)
_CADASTRAL_RE = re.compile(r"(?<!\d)(\d{2}:\d{1,3}(?::\d{1,9}){2,4})(?!\d)")
_AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", re.I)
_AREA_EXPLICIT_RE = re.compile(
    r"(?:площад(?:ь|ью|и)?|S)\s*[:=]?\s*"
    r"(\d[\d\s\u00a0\u202f]*(?:[.,]\d+)?)"
    r"(?:\s*\+/-\s*\d[\d\s\u00a0\u202f]*(?:[.,]\d+)?)?\s*"
    r"(?:кв\.?\s*м|м[²2])",
    re.I,
)
_DATETIME_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})\b")
_PROPERTY_RE = re.compile(
    r"недвижим|земельн|участок|квартир|комнат|помещен|здани|сооружен|"
    r"гараж|машино.?мест|жилой\s+дом|склад|кадастров",
    re.I,
)
_NON_PROPERTY_RE = re.compile(
    r"автомоб|транспорт|оборудован|право\s+требован|дебитор|"
    r"дол[яи]\s+в\s+уставн|акци[яи]|ценн\w*\s+бумаг|товарн\w*\s+знак",
    re.I,
)
_PURE_LEASE_RE = re.compile(r"\bправо\s+аренды\b", re.I)
_OWNED_REALTY_RE = re.compile(
    r"\b(?:здани|помещен|квартир|жилой\s+дом|гараж|сооружен|"
    r"земельн\w*\s+участок)\b",
    re.I,
)
_STATUS_MARKERS = (
    "идёт приём заявок",
    "идет прием заявок",
    "объявлены",
    "торги завершены",
    "торги отменены",
)
_INITIAL_PRICE_RE = re.compile(
    r"(?:начальн\w*\s+(?:продажн\w*\s+)?(?:цен\w*|стоимост\w*)|"
    r"начальной\s+стоимостью)\s*[:\-–—]?\s*"
    r"(\d[\d\s\u00a0\u202f]{0,24}(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:\.|лей)?)?",
    re.I,
)
_CURRENT_PRICE_RE = re.compile(
    r"текущ\w*\s+цен\w*\s*[:\-–—]?\s*"
    r"(\d[\d\s\u00a0\u202f]{0,24}(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:\.|лей)?)?",
    re.I,
)
_ADDRESS_RE = re.compile(
    r"(?:адрес(?:\s*\(местонахождени[ея]\))?|местоположение)\s*[:;]?\s*"
    r"(.{8,350}?)(?=(?:\s+(?:кадастров|площад|право|начальн|текущ)\w*\b)|$)",
    re.I,
)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _area_values(value: str) -> list[float]:
    matches = _AREA_EXPLICIT_RE.findall(value)
    if not matches:
        matches = _AREA_RE.findall(value)
    output: list[float] = []
    for raw in matches:
        normalized = re.sub(r"[\s\u00a0\u202f]", "", raw).replace(",", ".")
        try:
            area = float(normalized)
        except ValueError:
            continue
        if area > 0:
            output.append(area)
    return output


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%d.%m.%Y %H:%M")
    except ValueError:
        return None


def _money(match: re.Match[str] | None) -> float | None:
    if match is None:
        return None
    raw = re.sub(r"[\s\u00a0\u202f]", "", match.group(1)).replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        return None
    return amount if 0 < amount < 1e15 else None


def _row_urls(row: Tag, listing_url: str) -> list[str]:
    urls = [urljoin(listing_url, str(node.get("href") or "")) for node in row.select("a[href]")]
    for node in [row, *row.select("[onclick]")]:
        for candidate in re.findall(r"['\"]([^'\"]+)['\"]", str(node.get("onclick") or "")):
            if candidate.startswith(("/", "https://")):
                urls.append(urljoin(listing_url, candidate))
    return urls


def _provider_identity(url: str) -> tuple[str, int | None] | None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "aukcioncenter.ru" or not _GENERAL_PATH_RE.fullmatch(parsed.path):
        return None
    query = parse_qs(parsed.query)
    provider_ids = query.get("id", [])
    if len(provider_ids) != 1 or not re.fullmatch(r"[1-9]\d{5,12}", provider_ids[0]):
        return None
    lot_values = query.get("lotNumber", [])
    lot_number: int | None = None
    if len(lot_values) == 1 and re.fullmatch(r"[1-9]\d{0,4}", lot_values[0]):
        lot_number = int(lot_values[0])
    return provider_ids[0], lot_number


def _asset_class(value: str) -> str:
    property_match = bool(_PROPERTY_RE.search(value))
    non_property_match = bool(_NON_PROPERTY_RE.search(value))
    # A property object can legitimately carry an ancillary land-lease right
    # (e.g. building + lease of the underlying parcel). Pure lease rights are
    # not ownership real estate and remain a separate class.
    pure_lease = bool(_PURE_LEASE_RE.search(value)) and not bool(
        re.search(r"здани|помещен|квартир|жилой\\s+дом|гараж|сооружен", value, re.I)
    )
    if pure_lease:
        return "lease_right"
    if property_match and non_property_match:
        return "mixed"
    if property_match:
        if re.search(r"земельн|участок|кадастров", value, re.I) and not re.search(
            r"здани|помещен|квартир|жилой\\s+дом|гараж|сооружен", value, re.I
        ):
            return "land"
        return "real_estate"
    if re.search(r"автомоб|транспорт|vin\\b|прицеп|тепловоз", value, re.I):
        return "vehicle"
    if re.search(r"право\\s+требован|дебитор", value, re.I):
        return "receivables"
    if re.search(r"дол[яи]\\s+в\\s+уставн|акци[яи]|ценн\\w*\\s+бумаг", value, re.I):
        return "equity"
    if re.search(r"оборудован|станок|линия\\s+для|мебел|техник", value, re.I):
        return "equipment"
    if re.search(r"товарн\\w*\\s+знак|интеллектуальн", value, re.I):
        return "intangible"
    return "other"


def _source_status(text: str, *, active_filter: bool) -> tuple[str, str | None]:
    lowered = text.lower()
    explicit = next((marker for marker in _STATUS_MARKERS if marker in lowered), None)
    if explicit:
        if explicit == "объявлены":
            return "scheduled", explicit
        return _status(explicit), explicit
    if active_filter:
        # The official processStatus=BID_SUBMISSION filter itself is source
        # evidence that the procedure is accepting applications. A deadline
        # alone never produces this state.
        return "active", "processStatus=BID_SUBMISSION"
    return "unknown", None


def _lifecycle_kind(raw_status: str | None) -> str:
    value = (raw_status or "").lower()
    if "отмен" in value:
        return "cancelled"
    if "заверш" in value or "окончен" in value:
        return "finished"
    if "приём заявок" in value or "прием заявок" in value or "bid_submission" in value.lower():
        return "active"
    if "объяв" in value:
        return "scheduled"
    return "unknown"


def _dates_from_row(text: str) -> tuple[datetime | None, datetime | None]:
    observed = [_parse_datetime(value) for value in _DATETIME_RE.findall(text)]
    dates = [value for value in observed if value is not None]
    if len(dates) < 2:
        return None, None
    # The verified active register columns are "Начало приема заявок" then
    # "Завершение приема заявок".
    return dates[-2], dates[-1]


def parse_listing(
    profile: "EtpSourceProfile", html: str | bytes, *, page: int,
) -> list[NormalizedLot]:
    if isinstance(html, bytes):
        try:
            html = html.decode("utf-8")
        except UnicodeDecodeError:
            html = html.decode("windows-1251", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    active_filter = "processStatus=BID_SUBMISSION" in profile.listing_url
    output: list[NormalizedLot] = []
    seen: set[str] = set()

    for row in soup.select("tr"):
        row_text = _clean(row.get_text(" ", strip=True))
        trade_match = _TRADE_RE.search(row_text)
        if not trade_match:
            continue
        identities = [
            identity
            for url in _row_urls(row, profile.listing_url)
            if (identity := _provider_identity(url)) is not None
        ]
        provider_ids = {provider_id for provider_id, _ in identities}
        if len(provider_ids) != 1:
            # No stable provider procedure id (or conflicting ids): fail closed.
            continue
        provider_id = next(iter(provider_ids))
        url_lot_numbers = {lot for _, lot in identities if lot is not None}

        parts = list(_LOT_RE.finditer(row_text))
        segments: list[tuple[int, str]] = []
        if parts:
            for index, marker in enumerate(parts):
                end = parts[index + 1].start() if index + 1 < len(parts) else len(row_text)
                segments.append((int(marker.group("lot")), _clean(row_text[marker.end():end])))
        elif len(url_lot_numbers) == 1:
            segments.append((next(iter(url_lot_numbers)), row_text))
        else:
            # A single-looking procedure is still not proof that its lot number
            # is 1. Do not invent individual identity.
            continue

        auction_status, raw_status = _source_status(row_text, active_filter=active_filter)
        application_start, application_deadline = _dates_from_row(row_text)
        for lot_number, segment in segments:
            asset_class = _asset_class(segment)
            external_id = f"{profile.source_id}:{provider_id}:{lot_number}"
            if external_id in seen:
                continue
            seen.add(external_id)
            cadastral_numbers = list(dict.fromkeys(_CADASTRAL_RE.findall(segment)))
            cadastral = _CADASTRAL_RE.search(segment)
            area_values = _area_values(segment)
            area = area_values[0] if area_values else None
            detail_url = browser_url(
                f"{profile.source_id}:{provider_id}:{lot_number}"
            )
            output.append(NormalizedLot(
                external_id=external_id,
                source="aukcioncenter",
                source_system=profile.source_id,
                title=segment[:500],
                description=segment[:5000],
                category=asset_class,
                region_slug=None,
                region_name=None,
                address=None,
                cadastral_number=cadastral.group(1) if cadastral else None,
                vin=None,
                area=area,
                # Listing prose can contain deposits, nominal values and even
                # historical prices. Price is accepted only from isolated detail.
                start_price=None,
                current_price=None,
                auction_status=auction_status,
                lot_url=detail_url,
                source_url=detail_url,
                detail_level="search",
                raw_data={
                    "listing_page": page,
                    "procedure_provider_id": provider_id,
                    "trade_number": trade_match.group("trade").upper(),
                    "lot_number": lot_number,
                    "source_status_text": raw_status,
                    "source_lifecycle": _lifecycle_kind(raw_status),
                    "source_asset_class": asset_class,
                    "cadastral_numbers": cadastral_numbers,
                    "area_values": area_values,
                    "eligible_property": asset_class in {"land", "real_estate"},
                    "active_filter_verified": active_filter,
                    "individual_lot_identity_verified": True,
                    "listing_price_unverified": True,
                    "complete_source_inventory": False,
                    "safe_for_archival": False,
                    "reconcile_missing": False,
                },
                platform_name=profile.platform_name,
                platform_code=profile.platform_code,
                procedure_number=trade_match.group("trade").upper(),
                application_start_at=application_start,
                application_deadline=application_deadline,
            ))
    return output


def _external_parts(external_id: str) -> tuple[str, int]:
    match = re.fullmatch(r"aukcioncenter\.ru:([1-9]\d{5,12}):([1-9]\d{0,4})", external_id)
    if not match:
        raise ValueError("aukcioncenter_invalid_lot_identity")
    return match.group(1), int(match.group(2))


def browser_url(external_id: str) -> str:
    """Human-readable procedure page; never a source of per-lot price."""
    provider_id, lot_number = _external_parts(external_id)
    return (
        "https://aukcioncenter.ru/trade/view/purchase/general.html?"
        + urlencode({"id": provider_id, "lotNumber": lot_number})
    )


def detail_request_url(external_id: str, *, page: int = 1) -> str:
    """First-party AJAX lot-loader contract from /etp/scripts/viewLots.js."""
    provider_id, lot_number = _external_parts(external_id)
    return (
        "https://aukcioncenter.ru" + _INNER_LOTS_PATH + "?"
        + urlencode({
            "perspective": "inline",
            "id": provider_id,
            "page": str(page),
            "lotNumber": str(lot_number),
        })
    )


def _lot_segment(text: str, lot_number: int) -> str | None:
    matches = list(_LOT_RE.finditer(text))
    for index, marker in enumerate(matches):
        if int(marker.group("lot")) != lot_number:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return _clean(text[marker.end():end])
    return None


def parse_detail(
    profile: "EtpSourceProfile",
    html: str | bytes,
    *,
    detail_url_value: str,
    external_id: str,
) -> NormalizedLot:
    provider_id, lot_number = _external_parts(external_id)
    parsed = urlparse(detail_url_value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "aukcioncenter.ru"
        or not _INNER_LOTS_PATH_RE.fullmatch(parsed.path)
        or query.get("perspective") != ["inline"]
        or query.get("id") != [provider_id]
        or query.get("page") != ["1"]
        or query.get("lotNumber") != [str(lot_number)]
    ):
        raise ValueError("aukcioncenter_detail_identity_mismatch")

    if isinstance(html, bytes):
        try:
            html = html.decode("utf-8")
        except UnicodeDecodeError:
            html = html.decode("windows-1251", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    whole = _clean(soup.get_text(" ", strip=True))
    if len(whole) < 80:
        raise ValueError("aukcioncenter_detail_too_short")

    explicit_lot_numbers = [
        int(match.group("lot")) for match in _LOT_RE.finditer(whole)
    ]
    if explicit_lot_numbers and lot_number not in explicit_lot_numbers:
        raise ValueError("aukcioncenter_inner_lot_identity_mismatch")
    if len(set(explicit_lot_numbers)) > 1:
        raise ValueError("aukcioncenter_inner_endpoint_returned_multiple_lots")

    # The first-party AJAX request itself carries the exact provider id and
    # lotNumber and renders the lot container. If the fragment labels the lot,
    # isolate that section; otherwise the whole fragment is the isolated payload.
    selected = _lot_segment(whole, lot_number) if explicit_lot_numbers else whole
    if not selected or len(selected) < 40:
        raise ValueError("aukcioncenter_inner_lot_payload_too_short")

    asset_class = _asset_class(selected)
    if asset_class == "mixed":
        raise ValueError("aukcioncenter_detail_mixed_asset_class")

    start_price = _money(_INITIAL_PRICE_RE.search(selected))
    current_price = _money(_CURRENT_PRICE_RE.search(selected))
    cadastral_numbers = list(dict.fromkeys(_CADASTRAL_RE.findall(selected)))
    cadastral = _CADASTRAL_RE.search(selected)
    area_values = _area_values(selected)
    address_match = _ADDRESS_RE.search(selected)

    # Status is only accepted from an explicit lifecycle marker. Application
    # end timestamps and "прием заявок завершен" do not mean the auction ended.
    normalized_status, raw_status = _source_status(whole, active_filter=False)

    start_at = None
    deadline = None
    for label, target in (
        (r"(?:начало|дата начала)\s+(?:приема|приёма)\s+заявок", "start"),
        (r"(?:окончание|завершение|дата окончания)\s+(?:приема|приёма)\s+заявок", "end"),
    ):
        match = re.search(label + r"[^\d]{0,60}(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})", whole, re.I)
        if match:
            parsed_date = _parse_datetime(match.group(1))
            if target == "start":
                start_at = parsed_date
            else:
                deadline = parsed_date

    auction_at = None
    auction_match = re.search(
        r"(?:аукцион\s+состоится|дата\s+проведения(?:\s+торгов)?)"
        r"[^\d]{0,80}(\d{2}\.\d{2}\.\d{4})(?:[^\d]{0,40}(\d{2}:\d{2}))?",
        whole,
        re.I,
    )
    if auction_match:
        raw = auction_match.group(1) + (" " + auction_match.group(2) if auction_match.group(2) else " 00:00")
        auction_at = _parse_datetime(raw)

    title = re.split(
        r"\s+(?:начальн\w*\s+(?:продажн\w*\s+)?(?:цен\w*|стоимост\w*)|"
        r"текущ\w*\s+цен\w*|размер\s+задатка)\b",
        selected,
        maxsplit=1,
        flags=re.I,
    )[0]
    title = _clean(title) or f"Лот №{lot_number}"

    return NormalizedLot(
        external_id=external_id,
        source="aukcioncenter",
        source_system=profile.source_id,
        title=title[:500],
        description=selected[:5000],
        category=asset_class,
        region_slug=None,
        region_name=None,
        address=_clean(address_match.group(1)) if address_match else None,
        cadastral_number=cadastral.group(1) if cadastral else None,
        vin=None,
        area=area_values[0] if area_values else None,
        start_price=start_price,
        current_price=current_price,
        auction_status=normalized_status,
        lot_url=detail_url_value,
        source_url=detail_url_value,
        detail_level="detail",
        raw_data={
            "procedure_provider_id": provider_id,
            "lot_number": lot_number,
            "source_status_text": raw_status,
            "source_lifecycle": _lifecycle_kind(raw_status),
            "source_asset_class": asset_class,
            "cadastral_numbers": cadastral_numbers,
            "area_values": area_values,
            "eligible_property": asset_class in {"land", "real_estate"},
            "individual_lot_identity_verified": True,
            "individual_lot_section_verified": True,
            "detail_endpoint": _INNER_LOTS_PATH,
            "detail_start_price_verified": start_price is not None,
            "detail_current_price_verified": current_price is not None,
            "complete_source_inventory": False,
            "safe_for_archival": False,
            "reconcile_missing": False,
        },
        platform_name=profile.platform_name,
        platform_code=profile.platform_code,
        application_start_at=start_at,
        application_deadline=deadline,
        auction_at=auction_at,
    )
