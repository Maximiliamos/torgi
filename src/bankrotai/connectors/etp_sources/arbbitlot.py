from __future__ import annotations

import re

from bs4 import Tag

from bankrotai.domain import NormalizedLot


class ArbbitlotContractError(ValueError):
    """ARBbitLot detail does not prove the required per-lot contract."""


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def row_identity(row: Tag) -> tuple[str | None, str | None]:
    """Read displayed public trade number and lot number from one catalogue row."""
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

    trade_number: str | None = None
    lot_number: str | None = None
    if len(headings) == len(cells):
        for index, heading in enumerate(headings):
            value = _clean(cells[index].get_text(" ", strip=True))
            if re.search(r"№\s*лота", heading):
                if re.fullmatch(r"\d{1,6}", value):
                    lot_number = value
            elif trade_number is None and (
                heading == "№"
                or re.search(r"№\s*(?:торг|аукцион|конкурс|публичн)", heading)
            ):
                if re.fullmatch(r"\d{4,12}", value):
                    trade_number = value

    # Current FogSoft tables use a bare "№" first column. This fallback stays
    # bounded to that cell; it never scans unrelated numbers in the row.
    if trade_number is None:
        value = _clean(cells[0].get_text(" ", strip=True))
        if re.fullmatch(r"\d{4,12}", value):
            trade_number = value
    return trade_number, lot_number


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
    """Parse only explicitly labelled per-lot ARBbitLot fields."""
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
    trade_number, lot_number = row_identity(row)
    raw = dict(lot.raw_data or {})
    raw.update({"procedure_id": trade_number, "lot_number": lot_number})
    lot.raw_data = raw
    lot.procedure_number = trade_number
    lowered = _clean(context).lower()
    if "отменён организатором" in lowered or "отменен организатором" in lowered:
        lot.auction_status = "closed"


def apply_detail_contract(lot: NormalizedLot, value: str) -> None:
    fields = detail_fields(value)
    start_price = fields["start_price"]
    if start_price is None:
        raise ArbbitlotContractError(
            "arbbitlot.ru: individual detail does not prove an explicit start price"
        )
    classifier = efrsb_classifier(value)
    lot.start_price = float(start_price)
    current_price = fields["current_price"]
    lot.current_price = float(current_price) if current_price is not None else None
    lot.address = _address(value) or lot.address
    if fields["trade_id"]:
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
