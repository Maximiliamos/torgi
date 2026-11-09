from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime
import re

from bankrotai.domain import NormalizedLot


def sistematorg_lot_count(value: str) -> int | None:
    """A Sistematorg listing row represents a trade, potentially containing many lots."""
    match = re.search(r"всего\s+лотов\s*[:：]\s*(\d+)", value, re.IGNORECASE)
    if match is None:
        return None
    count = int(match.group(1))
    return count if 1 <= count <= 100_000 else None


def datetime_after(value: str, label: str) -> datetime | None:
    match = re.search(
        label + r"[^\d]{0,40}(\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)",
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


def ruson_initial_money(value: str) -> float | None:
    """RUSSIA OnLine renders a labelled initial price that may omit a RUB suffix."""
    match = re.search(
        r"начальн\w*\s+цен\w*\s*[:\-]?\s*(\d[\d\s\u00a0]{0,20}(?:[.,]\d{1,2})?)",
        value,
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


def profit_initial_money(value: str) -> float | None:
    """Profit renders a labelled decimal without a RUB suffix."""
    match = re.search(
        r"начальная\s+цена\s+продажи\s+(\d[\d\s\u00a0]{0,20})[.,]\s*(\d{2})(?!\d)",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None
    amount = float(re.sub(r"[\s\u00a0]", "", match.group(1)) + "." + match.group(2))
    return amount if 0 < amount < 1e15 else None



_ETPU_CLASSIFIER_END_RE = re.compile(
    r"\s+(?:Интервалы\s+снижения\s+цены|Обеспечение\s+заявки|Документы|Изображения)\b",
    re.IGNORECASE,
)


def etpu_lot_section(value: str) -> str:
    """Return only the ETPU per-lot section, excluding procedure-level prose."""
    match = re.search(r"Информация\s+о\s+лоте\s+№\s*\d+", value, re.IGNORECASE)
    return value[match.start():] if match else value


def etpu_labelled_money(value: str, label: str) -> float | None:
    """Read ETPU labelled ruble amounts without adjacent-field guesses."""
    match = re.search(
        label + r"\s*,?\s*руб\.?\s*:\s*(\d[\d\s\u00a0]{0,30}(?:[.,]\d{1,2})?)",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None
    raw = re.sub(r"[\s\u00a0]", "", match.group(1)).replace(",", ".")
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return None
    if not amount.is_finite() or not (Decimal(0) < amount < Decimal("1e15")):
        return None
    return float(amount)


def etpu_prices(value: str) -> tuple[float | None, float | None]:
    """Return only explicitly labelled individual ETPU start/current prices."""
    section = etpu_lot_section(value)
    return (
        etpu_labelled_money(section, r"Начальная\s+цена"),
        etpu_labelled_money(section, r"Текущая\s+цена"),
    )


def etpu_lot_title(value: str) -> str | None:
    section = etpu_lot_section(value)
    match = re.search(
        r"Наименование\s*:\s*(.{4,1500}?)"
        r"(?=\s+Дата\s+(?:проведения|начала\s+первого\s+интервала)"
        r"(?:\s*\(\s*Московское\s+время\s*\))?\s*:|\s+Статус\s*:)",
        section,
        re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else None


def etpu_explicit_status(value: str) -> str | None:
    """Read only the provider's labelled status, never status-like prose."""
    section = etpu_lot_section(value)
    match = re.search(
        r"Статус\s*:\s*("
        r"Извещение\s+опубликовано|"
        r"При[её]м\s+заявок\s+на\s+интервале\s+не\s+активен|"
        r"При[её]м\s+заявок|"
        r"Определение\s+участников\s+торгов|"
        r"Идут\s+торги|"
        r"Подведение\s+результатов|"
        r"Окончен|"
        r"Не\s+состоялся|"
        r"Отмен[её]н\s+организатором|"
        r"Приостановлен"
        r")\b",
        section,
        re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else None


def etpu_procedure_number(value: str) -> str | None:
    match = re.search(
        r"Информация\s+(?:об\s+аукционе|о\s+конкурсе|о\s+публичном\s+предложении)"
        r"\s*№\s*([0-9]{3,20})",
        value,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def etpu_moscow_datetime(value: str, *labels: str) -> datetime | None:
    """Parse only the platform's explicitly labelled Moscow-time timestamps."""
    section = etpu_lot_section(value)
    for label in labels:
        match = re.search(
            label
            + r"\s*\(\s*Московское\s+время\s*\)\s*:\s*"
            + r"(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}(?::\d{2})?)",
            section,
            re.IGNORECASE,
        )
        if not match:
            continue
        raw = match.group(1)
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                pass
    return None


def etpu_classifier_section(value: str) -> str | None:
    """Extract text belonging to the structured EFRSB classifier block."""
    section = etpu_lot_section(value)
    marker = re.search(r"Классификатор\s+ЕФРСБ\s*:\s*", section, re.IGNORECASE)
    if not marker:
        return None
    tail = section[marker.end():]
    end = _ETPU_CLASSIFIER_END_RE.search(tail)
    raw = tail[:end.start()] if end else tail[:2000]
    cleaned = re.sub(r"\s+", " ", raw).strip()
    return cleaned[:2000] or None


def etpu_classifier_flags(value: str) -> tuple[str, ...]:
    """Map the official EFRSB classifier block to conservative source tags."""
    classifier = etpu_classifier_section(value)
    if not classifier:
        return ()
    lowered = classifier.lower()
    flags: list[str] = []

    def add(flag: str, *patterns: str) -> None:
        if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in patterns):
            flags.append(flag)

    add("lease", r"\bправ(?:о|а)\s+аренды\b")
    add("shares", r"\bправа\s+долевой\s+собственности\b")
    add("receivables", r"\bправа?\s+требован", r"\bдебитор")
    add(
        "vehicle",
        r"\bавтомобил",
        r"\bприцеп",
        r"\bполуприцеп",
        r"\bмотоцикл",
        r"\bавтобус",
    )
    add(
        "equipment",
        r"\bоборудован",
        r"\bмашины\b",
        r"\bспециализированная\s+техника\b",
        r"\bэлектродвигател",
        r"\bгенератор",
        r"\bтрансформатор",
        r"\bинструмент\b",
    )
    add("land", r"\bземельные\s+участки\b")
    add(
        "real_estate",
        r"\bжилые\s+здания",
        r"\bздания\s*\(помещения\)",
        r"\bздания\s*\(кроме\s+жилых\)",
        r"\bздания\s+и\s+сооружения",
        r"\bздания\s+для\b",
        r"\bсооружен",
        r"\bпомещен",
        r"\bнезавершенн\w*\s+строительств",
        r"\bнедвижим",
    )
    if not flags and re.search(r"\bпрочее\b", lowered, re.IGNORECASE):
        flags.append("other")
    return tuple(flags)


def etpu_category(value: str) -> str | None:
    """Choose one normalized category while retaining every source flag in raw_data."""
    flags = etpu_classifier_flags(value)
    if not flags:
        return None
    for preferred in (
        "lease", "shares", "receivables", "real_estate", "land",
        "vehicle", "equipment", "other",
    ):
        if preferred in flags:
            return preferred
    return None


def _checked_amount(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("etp_invalid_boolean_money")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("etp_invalid_money") from exc
    if not amount.is_finite() or not (Decimal(0) < amount < Decimal("1e15")):
        raise ValueError("etp_invalid_money")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("etp_invalid_money_precision")
    return amount


def apply_detail(lot: NormalizedLot, detail: NormalizedLot, *, version: int) -> NormalizedLot:
    """Upgrade a shallow provider listing only with an isolated verified detail."""
    if detail.external_id != lot.external_id or detail.source_system != lot.source_system:
        raise ValueError("etp_detail_identity_or_source_mismatch")
    if getattr(detail, "detail_level", None) != "detail":
        raise ValueError("etp_detail_not_verified")

    listing_start = _checked_amount(lot.start_price)
    detail_start = _checked_amount(detail.start_price)
    _checked_amount(lot.current_price)
    _checked_amount(detail.current_price)
    if (
        listing_start is not None
        and detail_start is not None
        and abs(listing_start - detail_start) > Decimal("0.01")
    ):
        raise ValueError("etp_detail_start_price_conflicts_with_listing")

    lot.title = detail.title or lot.title
    lot.description = detail.description or lot.description
    lot.category = detail.category or lot.category
    lot.address = detail.address or lot.address
    lot.cadastral_number = detail.cadastral_number or lot.cadastral_number
    lot.vin = detail.vin or lot.vin
    lot.area = detail.area or lot.area
    lot.start_price = detail.start_price if detail.start_price is not None else lot.start_price
    lot.current_price = (
        detail.current_price if detail.current_price is not None else lot.current_price
    )
    lot.auction_status = (
        detail.auction_status if detail.auction_status != "unknown" else lot.auction_status
    )
    lot.application_deadline = detail.application_deadline or lot.application_deadline
    lot.auction_at = detail.auction_at or lot.auction_at
    lot.procedure_number = detail.procedure_number or lot.procedure_number
    lot.detail_level = "detail"
    raw = dict(lot.raw_data or {})
    raw.update({
        "detail_enrichment_status": "success",
        "detail_enrichment_version": version,
        "detail_identity_verified": True,
        "detail_start_price_verified": detail.start_price is not None,
        "detail_current_price_verified": detail.current_price is not None,
        "detail_status_verified": detail.auction_status != "unknown",
        "listing_price_used_as_fallback": (
            detail.start_price is None and lot.start_price is not None
        ),
    })
    lot.raw_data = raw
    return lot
