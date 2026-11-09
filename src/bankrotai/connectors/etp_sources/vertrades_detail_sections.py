"""Fail-closed parsing of individual lots on a shared Vertrades trade card.

The official `arest.vertrades.ru/showTrades?id=<procedure>` page contains
multiple independently priced lots.  This module never treats a procedure-wide
text blob as one lot: the requested lot number must have one explicit section
and every verified per-lot field must occur inside that section.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from bs4 import BeautifulSoup

_MAX_HTML = 2_000_000
_HEADER = re.compile(r"^Сведения\s+по\s+лоту\s*№\s*(\d{1,6})$", re.I)
_PROCEDURE = re.compile(
    r"идентификационный\s+номер\s*:?\s*([0-9]{1,12}\s*[-–—]\s*[А-ЯA-Z]{1,12})",
    re.I,
)
_CADASTRE = re.compile(r"(?<!\d)(\d{2}:\d{1,3}:\d{5,9}:\d{1,9})(?!\d)")
_AREA = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", re.I)
_ADDRESS = re.compile(
    r"Адрес\s*:\s*(.+?)(?=\s+(?:Начальная\s+цена|Задаток|Ограничения|"
    r"Обременения|Собственник|Продается|Продаётся)\s*:|$)",
    re.I,
)
_STATUS_LABEL = re.compile(r"^Статус\s+торгов\s+по\s+лоту\b", re.I)
_TITLE_LABEL = re.compile(
    r"^Краткие\s+сведения\s+об\s+имуществе\s*\(наименование\s+лота\)\b",
    re.I,
)
_DESCRIPTION_LABEL = re.compile(
    r"^[CС]ведения\s+об\s+арестованном\s+имуществе,\s*реализуемом\s+на\s+торгах",
    re.I,
)
_PRICE_LABEL = re.compile(r"^Начальная\s+цена\s+продажи\s+имущества\b", re.I)
_DEPOSIT_LABEL = re.compile(r"^Размер\s+задатка\b", re.I)
_DATE = r"(\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)"


class VertradesCardError(ValueError):
    """The public page does not prove a unique requested lot contract."""


@dataclass(frozen=True, slots=True)
class VertradesLotSection:
    external_id: str
    provider_id: str
    procedure_number: str
    lot_number: int
    title: str
    description: str
    initial_price_rub: Decimal
    status_text: str
    deposit_rub: Decimal | None
    address: str | None
    cadastral_number: str | None
    area_sqm: float | None
    application_start_at: datetime | None
    application_deadline: datetime | None
    auction_at: datetime | None


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _lines(html: str | bytes) -> list[str]:
    if len(html) > _MAX_HTML:
        raise VertradesCardError("vertrades_detail_page_too_large")
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style"]):
        node.decompose()
    return [_clean(x) for x in soup.get_text("\n").splitlines() if _clean(x)]


def _one_labeled(lines: list[str], label: re.Pattern[str], *, required: bool) -> str | None:
    values: list[str] = []
    for index, line in enumerate(lines):
        match = label.match(line)
        if not match:
            continue
        inline = _clean(line[match.end():].lstrip(" :-–—"))
        value = inline
        if not value and index + 1 < len(lines):
            value = lines[index + 1]
        if value:
            values.append(_clean(value))
    unique = list(dict.fromkeys(values))
    if len(unique) > 1:
        raise VertradesCardError("ambiguous_individual_lot_field")
    if not unique:
        if required:
            raise VertradesCardError("missing_individual_lot_field")
        return None
    return unique[0]


def _money(value: str | None, *, required: bool) -> Decimal | None:
    if not value:
        if required:
            raise VertradesCardError("missing_individual_lot_money")
        return None
    match = re.search(r"(?<!\d)(\d[\d\s\u00a0\u202f]*)(?:[.,](\d{1,2}))?(?!\d)", value)
    if not match:
        if required:
            raise VertradesCardError("invalid_individual_lot_money")
        return None
    whole = re.sub(r"[\s\u00a0\u202f]", "", match.group(1))
    cents = (match.group(2) or "0").ljust(2, "0")
    try:
        amount = Decimal(f"{whole}.{cents}")
    except InvalidOperation as exc:
        raise VertradesCardError("invalid_individual_lot_money") from exc
    if not amount.is_finite() or not (Decimal("0") < amount < Decimal("1e15")):
        raise VertradesCardError("invalid_individual_lot_money")
    return amount


def _datetime_after(text: str, label: str) -> datetime | None:
    match = re.search(label + r"\s*:?\s*" + _DATE, text, re.I)
    if not match:
        return None
    raw = match.group(1)
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _procedure_number(text: str) -> str:
    found = [
        re.sub(r"[-–—]", "-", _clean(m.group(1)).replace(" ", "")).upper()
        for m in _PROCEDURE.finditer(text)
    ]
    found = list(dict.fromkeys(found))
    if len(found) != 1:
        raise VertradesCardError("missing_or_ambiguous_procedure_number")
    return found[0]


def get_vertrades_lot(
    html: str | bytes, *, provider_id: str, lot_number: int,
) -> VertradesLotSection:
    if not re.fullmatch(r"[1-9]\d{0,11}", provider_id):
        raise VertradesCardError("invalid_provider_id")
    if not 1 <= lot_number <= 999_999:
        raise VertradesCardError("invalid_lot_number")

    lines = _lines(html)
    full_text = " ".join(lines)
    procedure = _procedure_number(full_text)
    headings = [(idx, int(match.group(1))) for idx, line in enumerate(lines)
                if (match := _HEADER.fullmatch(line))]
    if not headings:
        raise VertradesCardError("no_numbered_lot_sections")
    counts: dict[int, int] = {}
    for _, number in headings:
        counts[number] = counts.get(number, 0) + 1
    if counts.get(lot_number, 0) != 1:
        raise VertradesCardError(
            "duplicate_requested_lot_section" if counts.get(lot_number, 0) > 1
            else "requested_lot_missing_in_trade_card"
        )

    target_index = next(i for i, (_, number) in enumerate(headings) if number == lot_number)
    start = headings[target_index][0]
    end = headings[target_index + 1][0] if target_index + 1 < len(headings) else len(lines)
    section = lines[start:end]

    status = _one_labeled(section, _STATUS_LABEL, required=True)
    title = _one_labeled(section, _TITLE_LABEL, required=True)
    description = _one_labeled(section, _DESCRIPTION_LABEL, required=False) or title
    price = _money(_one_labeled(section, _PRICE_LABEL, required=True), required=True)
    deposit = _money(_one_labeled(section, _DEPOSIT_LABEL, required=False), required=False)
    assert status is not None and title is not None and price is not None

    asset_text = _clean(title + " " + description)
    cadastre = _CADASTRE.search(asset_text)
    area = _AREA.search(asset_text)
    address = _ADDRESS.search(description)
    application_start = _datetime_after(
        full_text, r"Начало\s+предоставления\s+заявок\s+на\s+участие",
    )
    application_deadline = _datetime_after(
        full_text, r"Окончание\s+предоставления\s+заявок\s+на\s+участие",
    )
    auction_at = _datetime_after(
        full_text, r"Начало\s+подачи\s+предложений\s+о\s+цене\s+имущества",
    )

    return VertradesLotSection(
        external_id=f"vertrades.ru:{provider_id}:{lot_number}",
        provider_id=provider_id,
        procedure_number=procedure,
        lot_number=lot_number,
        title=title[:500],
        description=description[:5000],
        initial_price_rub=price,
        status_text=status[:120],
        deposit_rub=deposit,
        address=_clean(address.group(1))[:500] if address else None,
        cadastral_number=cadastre.group(1) if cadastre else None,
        area_sqm=float(area.group(1).replace(",", ".")) if area else None,
        application_start_at=application_start,
        application_deadline=application_deadline,
        auction_at=auction_at,
    )
