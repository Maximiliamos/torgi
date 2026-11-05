"""Read-only extraction of independent lots from a shared NISTP trade card.

A trade_nid identifies a whole trade, not a unique asset. This module never
requests a page or writes to storage. Do not enable live detail enrichment
until a provider HTML fixture (not only copied visible text) is validated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup


class NistpCardError(ValueError):
    """The shared card did not prove the requested lot or its financial fields."""


@dataclass(frozen=True, slots=True)
class NistpLotSection:
    trade_nid: str
    trade_code: str
    lot_number: int
    title: str
    description: str
    cadastral_number: str | None
    area_sqm: float | None
    initial_price_rub: Decimal
    deposit_rub: Decimal | None
    status_text: str

    @property
    def external_id(self) -> str:
        return f"nistp.ru:{self.trade_nid}:{self.lot_number}"


# A numbered section must contain the matching "Номер лота" field.
# Mentions of "Лот № ..." inside auction conditions are not section starts.
_LOT_HEADING = re.compile(
    r"(?<!\w)Лот\s*№\s*(?P<lot>\d{1,5})\s+Номер\s+лота\s+(?P=lot)\b",
    re.IGNORECASE,
)
_TRADE_CODE = re.compile(
    r"Идентификационный\s+номер\s+(\d{3,9}-(?:ОАОФ|ОТПП|ЗТПП|ОКОФ|ОКЗФ))\b",
    re.IGNORECASE,
)
_CADASTRE = re.compile(r"(?<!\d)\d{2}:\d{2}:\d{5,9}:\d{1,9}(?!\d)")
_AREA = re.compile(r"\bплощадью\s+(\d{1,8}(?:[.,]\d{1,3})?)\s*кв\.?\s*м\b", re.IGNORECASE)
_PRICE = re.compile(
    r"\bНачальная\s+цена\s+(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s+Величина\s+повышения",
    re.IGNORECASE,
)
_DEPOSIT = re.compile(
    r"\bРазмер\s+задатка,?\s*руб\.?\s+(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\b",
    re.IGNORECASE,
)
_TITLE = re.compile(
    r"\bНаименование\s+имущества\s+(.+?)(?=\s+[CС]ведения\s+об\s+имуществе\b)",
    re.IGNORECASE | re.DOTALL,
)
_DESCRIPTION = re.compile(
    r"[CС]ведения\s+об\s+имуществе\b.+?\bописание\s+(.+?)"
    r"(?=\s+Является\s+объектом\s+недвижимости\b|\s+Местонахождение\s+имущества\b)",
    re.IGNORECASE | re.DOTALL,
)
_STATUS = re.compile(
    r"\bСтатус\s+торгов\s+"
    r"(Торги\s+(?:объявлены|отменены|завершены|не\s+состоялись)"
    r"|При[её]м\s+заявок|Идут\s+торги)"
    r"(?=\s+Купить\s+лот|\s+Протоколы|$)",
    re.IGNORECASE,
)


def _money(raw: str) -> Decimal:
    try:
        amount = Decimal(raw.replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise NistpCardError("invalid_individual_lot_money") from exc
    if not amount.is_finite() or not (Decimal("0") < amount < Decimal("1e15")):
        raise NistpCardError("invalid_individual_lot_money")
    if amount != amount.quantize(Decimal("0.01")):
        raise NistpCardError("invalid_individual_lot_money_precision")
    return amount


def parse_nistp_shared_trade_card(
    html: str | bytes, *, trade_nid: str,
) -> dict[int, NistpLotSection]:
    """Keep all per-lot fields inside their own numbered section, fail closed."""
    if not re.fullmatch(r"[1-9]\d{0,11}", trade_nid):
        raise NistpCardError("invalid_trade_nid")
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("script, style, noscript"):
        node.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    trade_code = _TRADE_CODE.search(text)
    if not trade_code:
        raise NistpCardError("missing_trade_code")
    headings = list(_LOT_HEADING.finditer(text))
    if not headings:
        raise NistpCardError("missing_numbered_lot_sections")
    result: dict[int, NistpLotSection] = {}
    for index, heading in enumerate(headings):
        number = int(heading.group("lot"))
        if number in result:
            raise NistpCardError("duplicate_lot_section_number")
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[heading.end():end]
        title = _TITLE.search(section)
        description = _DESCRIPTION.search(section)
        price = _PRICE.search(section)
        deposit = _DEPOSIT.search(section)
        status = _STATUS.search(section)
        if not (title and description and price and status):
            raise NistpCardError("incomplete_numbered_lot_section")
        title_text = title.group(1).strip()
        description_text = description.group(1).strip()
        if not title_text or not description_text:
            raise NistpCardError("empty_individual_lot_asset")
        cadastral = _CADASTRE.search(description_text)
        area = _AREA.search(description_text)
        result[number] = NistpLotSection(
            trade_nid=trade_nid,
            trade_code=trade_code.group(1),
            lot_number=number,
            title=title_text[:500],
            description=description_text[:5000],
            cadastral_number=cadastral.group(0) if cadastral else None,
            area_sqm=float(area.group(1).replace(",", ".")) if area else None,
            initial_price_rub=_money(price.group(1)),
            deposit_rub=_money(deposit.group(1)) if deposit else None,
            status_text=status.group(1),
        )
    return result


def get_nistp_lot(
    html: str | bytes, *, trade_nid: str, lot_number: int,
) -> NistpLotSection:
    """Return precisely this lot, not the first asset in a shared trade card."""
    if not 1 <= lot_number <= 99_999:
        raise NistpCardError("invalid_lot_number")
    sections = parse_nistp_shared_trade_card(html, trade_nid=trade_nid)
    try:
        return sections[lot_number]
    except KeyError as exc:
        raise NistpCardError("requested_lot_missing_in_trade_card") from exc
