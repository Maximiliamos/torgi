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



def _zakazrf_lot_section(value: str) -> str:
    """Return the isolated public lot section, excluding procedure boilerplate."""
    markers = list(re.finditer(r"информация\s+о\s+лоте\s*№?\s*\d+", value, re.IGNORECASE))
    if not markers:
        return value
    return value[markers[-1].start():]


def zakazrf_lot_number(value: str) -> str | None:
    section = _zakazrf_lot_section(value)
    match = re.search(r"\bномер\s*:\s*(\d{1,8})\b", section, re.IGNORECASE)
    return match.group(1) if match else None


def zakazrf_title(value: str) -> str | None:
    """Extract the lot's own labelled name, not the procedure-level name."""
    section = _zakazrf_lot_section(value)
    match = re.search(
        r"\bнаименование\s*:\s*(.{4,1200}?)(?=\s+(?:дата\s+(?:начала|окончания|проведения|подведения)|статус)\s*:)",
        section,
        re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else None


def _zakazrf_labelled_money(value: str, label: str) -> float | None:
    section = _zakazrf_lot_section(value)
    match = re.search(
        label
        + r"\s*,?\s*(?:руб(?:\.|лей)?)?\s*:\s*"
        + r"(\d[\d\s\u00a0]{0,30}(?:[.,]\d{1,2})?)",
        section,
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


def zakazrf_initial_money(value: str) -> float | None:
    """ZakazRF prints the RUB marker in the label, before the number."""
    return _zakazrf_labelled_money(value, r"начальн\w*\s+цен\w*")


def zakazrf_current_money(value: str) -> float | None:
    """Public offers expose a separate explicitly-labelled current price."""
    return _zakazrf_labelled_money(value, r"текущ\w*\s+цен\w*")


def zakazrf_status_text(value: str) -> str | None:
    """Read only the explicit lot Status field; ignore conditional boilerplate."""
    section = _zakazrf_lot_section(value)
    match = re.search(
        r"\bстатус\s*:\s*(.{2,100}?)(?=\s+(?:дата|начальн\w*\s+цен|текущ\w*\s+цен|шаг|порядок|сведения|обеспечение)\b)",
        section,
        re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else None



def zakazrf_normalized_status(value: str | None) -> str:
    if not value:
        return "unknown"
    lowered = re.sub(r"\s+", " ", value).strip().lower()
    if lowered in {"прием заявок", "приём заявок", "идут торги"}:
        return "active"
    if any(token in lowered for token in (
        "окончен", "завершен", "завершён", "не состоялся", "состоялся", "отменен", "отменён",
    )):
        return "closed"
    if "приостанов" in lowered:
        return "paused"
    if any(token in lowered for token in ("объявлен", "ожидается")):
        return "scheduled"
    return "unknown"

def zakazrf_classifier(value: str) -> str | None:
    """Extract the provider's structured EFRSB classifier for the individual lot."""
    section = _zakazrf_lot_section(value)
    match = re.search(
        r"классификатор\s+ефрсб\s*:\s*(.{2,300}?)"
        r"(?=\s+(?:интервалы\s+снижения\s+цены|обеспечение\s+заявки|"
        r"порядок\s+внесения|документы|всего\s+подано|$))",
        section,
        re.IGNORECASE,
    )
    if not match:
        return None
    classifier = re.sub(r"\s+", " ", match.group(1)).strip(" :-")
    return classifier or None


def zakazrf_structured_category(classifier: str | None) -> str | None:
    """Map only the structured EFRSB classifier; never infer from free prose."""
    if not classifier:
        return None
    lowered = classifier.lower()
    if "земельн" in lowered or "земли " in lowered:
        return "land"
    if any(token in lowered for token in (
        "жилые здания", "жилые помещения", "здания (кроме жилых)",
        "нежил", "сооружен", "гараж", "машино-мест", "недвижим",
    )):
        return "real_estate"
    if any(token in lowered for token in ("автомоб", "транспорт", "прицеп", "снегоход")):
        return "vehicle"
    if any(token in lowered for token in ("право требования", "права требования", "уступка требован", "дебитор")):
        return "receivables"
    if any(token in lowered for token in ("оборудован", "машины и оборудование", "станк")):
        return "equipment"
    if "аренд" in lowered or "право пользования" in lowered:
        return "lease"
    return None


def zakazrf_address(title: str | None) -> str | None:
    if not title:
        return None
    match = re.search(
        r"\b(?:адрес|по\s+адресу)\s*:\s*(.+?)"
        r"(?=,\s*(?:кадастров(?:ый)?\s+номер|кад\.?\s*№)|$)",
        title,
        re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", match.group(1)).strip(" ,") if match else None


def build_zakazrf_detail_lot(
    value: str,
    *,
    external_id: str,
    detail_url: str,
    platform_name: str,
    platform_code: str,
    complete_source_inventory: bool,
    safe_for_archival: bool,
    procedure_number: str | None,
    bankruptcy_case_number: str | None,
) -> NormalizedLot:
    """Build a ZakazRF lot only from the isolated individual-lot contract."""
    title = zakazrf_title(value) or "Лот"
    classifier = zakazrf_classifier(value)
    cadastral = re.search(r"(?<!\d)(\d{2}:\d{2}(?::\d{1,7}){2,4})(?!\d)", title)
    area_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", title, re.IGNORECASE)
    vin = re.search(r"\b[A-HJ-NPR-Z0-9]{17}\b", title, re.IGNORECASE)
    start_price = zakazrf_initial_money(value)
    current_price = zakazrf_current_money(value)
    status_text = zakazrf_status_text(value)
    category = zakazrf_structured_category(classifier) or "other"
    return NormalizedLot(
        external_id=external_id,
        source="zakazrf",
        source_system="zakazrf.ru",
        title=title[:500],
        description=value[:5000],
        category=category,
        region_slug=None,
        region_name=None,
        address=zakazrf_address(title),
        cadastral_number=cadastral.group(1) if cadastral else None,
        vin=vin.group(0).upper() if vin else None,
        area=float(area_match.group(1).replace(",", ".")) if area_match else None,
        start_price=start_price,
        current_price=current_price,
        auction_status=zakazrf_normalized_status(status_text),
        lot_url=detail_url,
        source_url=detail_url,
        detail_level="detail",
        raw_data={
            "complete_source_inventory": complete_source_inventory,
            "safe_for_archival": safe_for_archival,
            "reconcile_missing": False,
            "structured_property_classifier": classifier,
            "structured_property_classifier_verified": bool(classifier),
            "lot_number": zakazrf_lot_number(value),
            "detail_start_price_verified": start_price is not None,
            "detail_current_price_verified": current_price is not None,
            "detail_status_verified": status_text is not None,
        },
        platform_name=platform_name,
        platform_code=platform_code,
        procedure_number=procedure_number,
        bankruptcy_case_number=bankruptcy_case_number,
        application_deadline=datetime_after(
            value,
            r"(?:окончание|дата окончания)\s+(?:приема|приёма|представления)\s+заявок",
        ),
        auction_at=datetime_after(value, r"дата проведения"),
    )

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
