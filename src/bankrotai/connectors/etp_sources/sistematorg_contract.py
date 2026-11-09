"""Strict source contract for public Sistematorg procedure and lot pages.

The catalogue is procedure-level.  A sellable asset exists only after an
explicit numbered lot is observed.  Detail price/status evidence is accepted
only from the matching numbered lot section on the official procedure page.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from bankrotai.connectors.etp_sources.status_normalization import _status
from bankrotai.domain import NormalizedLot


BASE_URL = "https://sistematorg.com/tradelist.php"
DETAIL_PATH = "/trade_view.php"
MONEY_LIMIT = Decimal("1e15")
PROPERTY_CATEGORIES = frozenset({"real_estate", "land"})

_SPACE_RE = re.compile(r"\s+")
_CADASTRAL_RE = re.compile(r"(?<!\d)(\d{2}:\d{2}(?::\d{1,7}){2,4})(?!\d)")
_AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", re.IGNORECASE)
_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_PROCEDURE_RE = re.compile(
    r"\b\d{3,9}-(?:ОАОФ|ОТПП|ЗТПП|ОКОФ|ОКЗФ)\b",
    re.IGNORECASE,
)
_LOT_MARKER_RE = re.compile(r"лот\s*№\s*(\d{1,5})\s*[:.\-]\s*", re.IGNORECASE)
_DECLARED_LOTS_RE = re.compile(r"всего\s+лотов\s*[:：]\s*(\d{1,5})", re.IGNORECASE)
_DATETIME_RE = r"\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}"
_SCHEDULE_ROW_RE = re.compile(
    rf"({_DATETIME_RE})\s+({_DATETIME_RE})\s+"
    r"(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s+"
    r"(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)"
)


@dataclass(frozen=True, slots=True)
class SistematorgCatalogueLot:
    trade_id: str
    lot_id: str
    title: str
    category: str

    @property
    def external_id(self) -> str:
        return f"sistematorg.com:{self.trade_id}:{self.lot_id}"

    @property
    def is_property(self) -> bool:
        return self.category in PROPERTY_CATEGORIES


@dataclass(frozen=True, slots=True)
class SistematorgProcedure:
    trade_id: str
    procedure_number: str | None
    detail_url: str
    declared_lot_count: int
    lots: tuple[SistematorgCatalogueLot, ...]


@dataclass(frozen=True, slots=True)
class SistematorgCataloguePage:
    page: int
    procedures: tuple[SistematorgProcedure, ...]
    rejects: dict[str, int]


def _clean(value: str) -> str:
    return _SPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()


def _decimal_money(value: str) -> Decimal | None:
    raw = re.sub(r"(?:₽|руб(?:\.|лей)?)", "", value, flags=re.IGNORECASE)
    raw = re.sub(r"[\s\u00a0]", "", raw).replace(",", ".")
    if not re.fullmatch(r"\d{1,14}(?:\.\d{1,2})?", raw):
        return None
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return None
    if not amount.is_finite() or not (Decimal(0) < amount < MONEY_LIMIT):
        return None
    return amount.quantize(Decimal("0.01"))


def _float_money(value: str) -> float | None:
    amount = _decimal_money(value)
    return float(amount) if amount is not None else None


def _parse_datetime(value: str) -> datetime | None:
    cleaned = value.strip()
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        pass
    match = re.search(r"\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?", cleaned)
    if match is None:
        return None
    raw = match.group(0)
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def classify_sistematorg_asset(value: str) -> str:
    """Classify one numbered lot without borrowing context from sibling lots."""
    lowered = value.casefold().replace("ё", "е")
    if "право требования" in lowered or "дебитор" in lowered:
        return "receivables"
    if "право аренды" in lowered:
        return "lease_right"
    if any(word in lowered for word in (
        "автомоб", "транспортн", "vin:", "vin ", "прицеп", "снегоход",
    )):
        return "vehicle"
    if any(word in lowered for word in (
        "оборудован", "станок", "инвентар", "оргтехник", "мебель",
    )):
        return "equipment"
    if "земельн" in lowered or "земли " in lowered:
        return "land"
    if any(word in lowered for word in (
        "недвижим", "квартир", "комнат", "жилое помещ", "нежилое помещ",
        "здани", "гараж", "машино-мест", "жилой дом", "сооружен",
        "имущественный комплекс",
    )):
        return "real_estate"
    return "other"


def _trusted_detail_url(raw: str) -> tuple[str, str] | None:
    absolute = urljoin(BASE_URL, raw.strip())
    parsed = urlparse(absolute)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "sistematorg.com"
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != DETAIL_PATH
    ):
        return None
    ids = parse_qs(parsed.query).get("trade_nid", [])
    if len(ids) != 1 or not re.fullmatch(r"[1-9]\d{0,11}", ids[0]):
        return None
    return absolute, ids[0]


def _row_detail_identity(row) -> tuple[str, str] | None:
    candidates: list[str] = []
    for anchor in row.select("a[href]"):
        candidates.append(str(anchor.get("href") or ""))
    nodes = ([row] if row.has_attr("onclick") else []) + row.select("[onclick]")
    for node in nodes:
        for candidate in re.findall(r"['\"]([^'\"]+)['\"]", str(node.get("onclick") or "")):
            if "trade_view.php" in candidate:
                candidates.append(candidate)
    identities = {
        identity
        for candidate in candidates
        if (identity := _trusted_detail_url(candidate)) is not None
    }
    return next(iter(identities)) if len(identities) == 1 else None


def parse_sistematorg_catalogue_page(
    html: str | bytes, *, page: int,
) -> SistematorgCataloguePage:
    """Observe every procedure/numbered lot on one official catalogue page."""
    soup = BeautifulSoup(html, "html.parser")
    procedures: list[SistematorgProcedure] = []
    rejects: dict[str, int] = {}

    def reject(reason: str) -> None:
        rejects[reason] = rejects.get(reason, 0) + 1

    seen_trades: set[str] = set()
    for row in soup.select("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 3:
            continue
        row_text = _clean(row.get_text(" ", strip=True))
        declared_match = _DECLARED_LOTS_RE.search(row_text)
        if declared_match is None:
            continue

        identity = _row_detail_identity(row)
        if identity is None:
            reject("missing_or_ambiguous_detail_url")
            continue
        detail_url, trade_id = identity
        if trade_id in seen_trades:
            reject("duplicate_procedure_on_page")
            continue
        seen_trades.add(trade_id)

        declared_count = int(declared_match.group(1))
        asset_cell = max(
            cells,
            key=lambda cell: len(_LOT_MARKER_RE.findall(cell.get_text(" ", strip=True))),
        )
        asset_text = _clean(asset_cell.get_text(" ", strip=True))
        markers = list(_LOT_MARKER_RE.finditer(asset_text))
        if not markers:
            reject("declared_lots_without_numbered_markers")
            continue
        if len(markers) != declared_count:
            reject("declared_lot_count_mismatch")
            continue

        lots: list[SistematorgCatalogueLot] = []
        seen_lots: set[str] = set()
        invalid = False
        for index, marker in enumerate(markers):
            lot_id = marker.group(1)
            if lot_id in seen_lots:
                reject("duplicate_lot_id_in_procedure")
                invalid = True
                break
            seen_lots.add(lot_id)
            end = markers[index + 1].start() if index + 1 < len(markers) else len(asset_text)
            title = _clean(asset_text[marker.end():end]).strip(" .;")
            if len(title) < 4:
                reject("empty_lot_title")
                invalid = True
                break
            lots.append(SistematorgCatalogueLot(
                trade_id=trade_id,
                lot_id=lot_id,
                title=title[:5000],
                category=classify_sistematorg_asset(title),
            ))
        if invalid:
            continue

        number_match = _PROCEDURE_RE.search(row_text)
        procedures.append(SistematorgProcedure(
            trade_id=trade_id,
            procedure_number=number_match.group(0) if number_match else None,
            detail_url=detail_url,
            declared_lot_count=declared_count,
            lots=tuple(lots),
        ))

    return SistematorgCataloguePage(
        page=page,
        procedures=tuple(procedures),
        rejects=rejects,
    )


def parse_sistematorg_external_id(external_id: str) -> tuple[str, str]:
    match = re.fullmatch(r"sistematorg\.com:([1-9]\d{0,11}):(\d{1,5})", external_id)
    if match is None:
        raise ValueError("sistematorg_invalid_trade_lot_identity")
    return match.group(1), match.group(2)


def _field(segment: str, label: str, next_labels: tuple[str, ...]) -> str | None:
    stop = "|".join(next_labels)
    match = re.search(
        rf"{label}\s*[:|]?\s*(.+?)(?=\s+(?:{stop})\s*[:|]?|$)",
        segment,
        re.IGNORECASE,
    )
    return _clean(match.group(1)) if match else None


def _labelled_money(segment: str, label: str) -> float | None:
    match = re.search(
        rf"{label}\s*[:|]?\s*"
        r"(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:\.|лей)?)",
        segment,
        re.IGNORECASE,
    )
    return _float_money(match.group(1)) if match else None


def _global_field(prefix: str, label: str, next_labels: tuple[str, ...]) -> str | None:
    return _field(prefix, label, next_labels)


def _schedule(segment: str) -> list[dict]:
    marker = re.search(r"периоды\s+проведения\s+торгов", segment, re.IGNORECASE)
    if marker is None:
        return []
    result: list[dict] = []
    for match in _SCHEDULE_ROW_RE.finditer(segment[marker.end():]):
        start_at = _parse_datetime(match.group(1))
        end_at = _parse_datetime(match.group(2))
        price = _decimal_money(match.group(3))
        deposit = _decimal_money(match.group(4))
        if start_at is None or end_at is None or price is None or deposit is None:
            continue
        if end_at < start_at:
            continue
        result.append({
            "application_start_at": start_at.isoformat(),
            "application_deadline": end_at.isoformat(),
            "price": format(price, "f"),
            "deposit": format(deposit, "f"),
        })
    return result


def _address(value: str) -> str | None:
    patterns = (
        r"(?:расположенн\w*\s+по\s+адресу|адрес\s*\(местонахождени[ея]\)|адрес)"
        r"\s*[:\-]?\s*(.+?)(?=;\s*(?:кадастр|площад)|\s+кадастр\w*\s+(?:номер|№)|$)",
        r"место\s+нахождения\s*[:\-]?\s*(.+?)(?=;|\s+кадастр\w*\s+(?:номер|№)|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, value, re.IGNORECASE)
        if match:
            cleaned = _clean(match.group(1)).strip(" ,.;")
            if 6 <= len(cleaned) <= 500:
                return cleaned
    return None


def parse_sistematorg_detail_lots(
    html: str | bytes,
    *,
    detail_url: str,
    platform_name: str = "Систематорг",
    platform_code: str = "sistematorg",
) -> list[NormalizedLot]:
    """Return independently evidenced numbered lots from one procedure detail."""
    identity = _trusted_detail_url(detail_url)
    if identity is None:
        raise ValueError("sistematorg_untrusted_detail_url")
    canonical_url, trade_id = identity

    if isinstance(html, bytes):
        try:
            decoded = html.decode("utf-8")
        except UnicodeDecodeError:
            decoded = html.decode("windows-1251", errors="replace")
    else:
        decoded = html
    soup = BeautifulSoup(decoded, "html.parser")
    text = _clean(soup.get_text(" ", strip=True))
    if len(text) < 80:
        raise ValueError("sistematorg_detail_too_short")

    all_markers = list(_LOT_MARKER_RE.finditer(text))
    declared_match = _DECLARED_LOTS_RE.search(text)
    declared_count = int(declared_match.group(1)) if declared_match else None

    verified_markers: list[re.Match[str]] = []
    for marker in all_markers:
        lot_id = marker.group(1)
        probe = text[marker.end():marker.end() + 1200]
        has_number = re.search(
            rf"номер\s+лота\s*[:|]?\s*{re.escape(lot_id)}\b",
            probe,
            re.IGNORECASE,
        )
        has_name = re.search(r"наименование\s+имущества", probe, re.IGNORECASE)
        if has_number and has_name:
            verified_markers.append(marker)

    # Compatibility with the #1166 single-lot guard: a page that explicitly
    # declares exactly one lot may use a compact legacy layout without the
    # modern label/value table.  Multi-lot pages never use this fallback.
    if not verified_markers and declared_count == 1 and len(all_markers) == 1:
        verified_markers = all_markers

    if not verified_markers:
        raise ValueError("sistematorg_no_individual_lot_sections")
    lot_ids = [marker.group(1) for marker in verified_markers]
    if len(set(lot_ids)) != len(lot_ids):
        raise ValueError("sistematorg_duplicate_detail_lot_id")
    if declared_count is not None and declared_count != len(verified_markers):
        raise ValueError("sistematorg_detail_lot_count_mismatch")

    prefix = text[:verified_markers[0].start()]
    procedure_number = _global_field(
        prefix,
        r"идентификационный\s+номер",
        (
            r"текущий\s+статус", r"(?:вид|тип)\s+торгов", r"форма\s+подачи",
            r"дата\s+начала", r"дата\s+окончания", r"дата\s+проведения",
        ),
    )
    auction_type = _global_field(
        prefix,
        r"(?:вид|тип)\s+торгов",
        (
            r"форма\s+подачи", r"дата\s+начала", r"дата\s+окончания",
            r"дата\s+проведения", r"порядок",
        ),
    )
    global_start = _global_field(
        prefix,
        r"дата\s+начала\s+(?:приема|приёма|представления)\s+заявок(?:\s+на\s+участие)?",
        (r"дата\s+окончания", r"дата\s+проведения", r"порядок"),
    )
    global_end = _global_field(
        prefix,
        r"дата\s+окончания\s+(?:приема|приёма|представления)\s+заявок(?:\s+на\s+участие)?",
        (r"дата\s+проведения", r"порядок", r"место"),
    )
    global_auction = _global_field(
        prefix,
        r"дата\s+проведения",
        (r"порядок", r"место", r"условия"),
    )

    lots: list[NormalizedLot] = []
    for index, marker in enumerate(verified_markers):
        lot_id = marker.group(1)
        end = (
            verified_markers[index + 1].start()
            if index + 1 < len(verified_markers)
            else len(text)
        )
        segment = text[marker.end():end]
        number_value = _field(
            segment,
            r"номер\s+лота",
            (r"наименование\s+имущества", r"сведения\s+об\s+имуществе", r"cведения\s+об\s+имуществе"),
        )
        if number_value is not None:
            number_match = re.match(r"(\d{1,5})\b", number_value)
            if number_match is None or number_match.group(1) != lot_id:
                raise ValueError("sistematorg_lot_number_field_mismatch")

        name = _field(
            segment,
            r"наименование\s+имущества",
            (
                r"сведения\s+об\s+имуществе", r"cведения\s+об\s+имуществе",
                r"начальная\s+цена", r"размер\s+задатка", r"статус\s+торгов",
            ),
        )
        description = _field(
            segment,
            r"(?:сведения|cведения)\s+об\s+имуществе[^|:]{0,180}",
            (
                r"начальная\s+цена", r"шаг\s+аукциона", r"размер\s+задатка",
                r"порядок\s+ознакомления", r"подано\s+заявок", r"статус\s+торгов",
            ),
        )
        marker_title = _clean(
            re.split(
                r"\s+(?:номер\s+лота|наименование\s+имущества|начальная\s+цена)\b",
                segment,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
        ).strip(" .;")
        title = name or marker_title
        if len(title) < 3:
            raise ValueError("sistematorg_missing_lot_title")
        description = description or title

        start_price = _labelled_money(segment, r"начальная\s+цена")
        current_price = _labelled_money(segment, r"текущая\s+цена")
        deposit = _labelled_money(segment, r"размер\s+задатка")
        auction_step = _labelled_money(segment, r"шаг\s+аукциона")
        status_text = _field(
            segment,
            r"статус\s+торгов",
            (
                r"периоды\s+проведения\s+торгов", r"информация\s+об\s+эп",
                r"извещения\s+о\s+торгах", r"лот\s*№",
            ),
        )
        normalized_status = _status(status_text or "")
        combined = _clean(f"{title} {description}")
        category = classify_sistematorg_asset(combined)
        schedule = _schedule(segment)

        cadastral = _CADASTRAL_RE.search(combined)
        area_match = _AREA_RE.search(combined)
        vin = _VIN_RE.search(combined)
        area = float(area_match.group(1).replace(",", ".")) if area_match else None

        application_start = _parse_datetime(global_start or "")
        application_deadline = _parse_datetime(global_end or "")
        if schedule:
            application_start = _parse_datetime(schedule[0]["application_start_at"])
            application_deadline = _parse_datetime(schedule[-1]["application_deadline"])

        lots.append(NormalizedLot(
            external_id=f"sistematorg.com:{trade_id}:{lot_id}",
            source="sistematorg",
            source_system="sistematorg.com",
            title=title[:500],
            description=description[:5000],
            category=category,
            region_slug=None,
            region_name=None,
            address=_address(combined),
            cadastral_number=cadastral.group(1) if cadastral else None,
            vin=vin.group(0).upper() if vin else None,
            area=area,
            start_price=start_price,
            current_price=current_price,
            auction_status=normalized_status,
            lot_url=canonical_url,
            source_url=canonical_url,
            detail_level="detail",
            raw_data={
                "trade_id": trade_id,
                "lot_id": lot_id,
                "individual_lot_identity_verified": True,
                "per_lot_detail_verified": True,
                "price_trade_id": trade_id if start_price is not None else None,
                "price_lot_id": lot_id if start_price is not None else None,
                "detail_start_price_verified": start_price is not None,
                "detail_current_price_verified": current_price is not None,
                "detail_status_verified": normalized_status != "unknown",
                "property_classification": category,
                "is_property_lot": category in PROPERTY_CATEGORIES,
                "public_offer_schedule_verified": bool(schedule),
                "declared_detail_lot_count": declared_count,
                "observed_detail_lot_count": len(verified_markers),
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
            },
            platform_name=platform_name,
            platform_code=platform_code,
            procedure_number=procedure_number,
            deposit_amount=deposit,
            application_start_at=application_start,
            application_deadline=application_deadline,
            auction_at=_parse_datetime(global_auction or ""),
            auction_step_amount=auction_step,
            auction_type=auction_type,
            public_offer_schedule=schedule or None,
            property_type=category,
        ))
    return lots
