"""Parse separate NISTP lots from provider-owned table_lot_N HTML sections.

This pure parser does no IO. The browser-confirmed shared URL identifies a
trade, while numbered tables identify individual assets and their prices.
Missing or contradictory identity/price evidence must fail closed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup, Tag


class NistpCardError(ValueError):
    """The shared card does not prove this requested lot."""


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
    native_lot_nid: str | None = None
    address: str | None = None

    @property
    def external_id(self) -> str:
        return f"nistp.ru:{self.trade_nid}:{self.lot_number}"


_TRADE_CODE = re.compile(
    r"Идентификационный\s+номер\s+(\d{3,9}-(?:ОАОФ|ОТПП|ЗТПП|ОКОФ|ОКЗФ))\b",
    re.IGNORECASE,
)
_TABLE_ID = re.compile(r"table_lot_([1-9]\d{0,4})\Z")
_CADASTRE = re.compile(r"(?<!\d)\d{2}:\d{2}:\d{5,9}:\d{1,9}(?!\d)")
_AREA = re.compile(r"\bплощадью\s+(\d{1,8}(?:[.,]\d{1,3})?)\s*кв\.?\s*м\b", re.IGNORECASE)
_HEADING = re.compile(r"\bЛот\s*№\s*(\d{1,5})\b", re.IGNORECASE)
_NATIVE_ANCHOR = re.compile(r"lot([1-9]\d{0,11})\Z")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _money(raw: str, *, allow_zero: bool = False) -> Decimal:
    cleaned = raw.replace("\u00a0", "").replace("\u202f", "").replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"\d{1,15}(?:\.\d{1,2})?", cleaned):
        raise NistpCardError("invalid_individual_lot_money")
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        raise NistpCardError("invalid_individual_lot_money") from exc
    if (not amount.is_finite() or amount >= Decimal("1e15")
            or (amount < 0 if allow_zero else amount <= 0)):
        raise NistpCardError("invalid_individual_lot_money")
    return amount


def _label_values(table: Tag) -> dict[str, str]:
    fields: dict[str, str] = {}
    # Some provider cards omit an explicit <tbody>; other cards contain it.
    # Consider only direct rows belonging to THIS numbered lot table, never
    # inner procedure/nested tables (whose prices belong to other entities).
    for row in table.select("tr"):
        if row.find_parent("table") is not table:
            continue
        cells = row.find_all("td", recursive=False)
        if len(cells) != 2 or "label" not in cells[0].get("class", []):
            continue
        name = _clean(cells[0].get_text(" ", strip=True)).casefold()
        value = _clean(cells[1].get_text(" ", strip=True))
        if name in fields:
            raise NistpCardError("duplicate_individual_lot_field")
        fields[name] = value
    return fields


def _field(fields: dict[str, str], label: str) -> str:
    value = fields.get(label.casefold(), "")
    if not value:
        raise NistpCardError("incomplete_numbered_lot_section")
    return value


def parse_nistp_shared_trade_card(
    html: str | bytes, *, trade_nid: str,
    target_lot_number: int | None = None,
) -> dict[int, NistpLotSection]:
    """Parse source-numbered tables, optionally validating only one lot's fields.

    The full-card mode still requires every table's fields to be valid.
    Targeted mode does not let a defective unrelated asset on a shared page
    prevent verification of a correctly numbered individual asset. Both
    modes refuse duplicated section numbers and native identities.
    """
    if not re.fullmatch(r"[1-9]\d{0,11}", trade_nid):
        raise NistpCardError("invalid_trade_nid")
    if target_lot_number is not None and (
        type(target_lot_number) is not int or not 1 <= target_lot_number <= 99_999
    ):
        raise NistpCardError("invalid_lot_number")
    soup = BeautifulSoup(html, "html.parser")
    # NISTP's modal has an earlier <h1 class=node_header> ("Купить лот
    # через агента"). Select the actual procedure heading by its code.
    trade_code = next((
        match for header in soup.select("h1.node_header")
        if (match := _TRADE_CODE.search(_clean(header.get_text(" ", strip=True))))
    ), None)
    if trade_code is None:
        raise NistpCardError("missing_trade_code")
    tables = soup.select('table[id^="table_lot_"]')
    if not tables:
        raise NistpCardError("missing_numbered_lot_sections")

    result: dict[int, NistpLotSection] = {}
    native_seen: set[str] = set()
    section_numbers_seen: set[int] = set()
    for table in tables:
        matched = _TABLE_ID.fullmatch(str(table.get("id") or ""))
        if not matched:
            raise NistpCardError("invalid_individual_lot_table_id")
        number = int(matched.group(1))
        if number in section_numbers_seen:
            raise NistpCardError("duplicate_lot_section_number")
        section_numbers_seen.add(number)
        # Check cross-section identity collisions even for unrelated malformed
        # field tables. Do not accept a forged duplicate of the requested lot.
        named_anchors = {
            str(a.get("name")) for a in table.select("thead a[name]")
        }
        natives = {
            m.group(1) for a in named_anchors
            if (m := _NATIVE_ANCHOR.fullmatch(a)) and int(m.group(1)) != number
        }
        if len(natives) > 1:
            raise NistpCardError("multiple_native_lot_identifiers")
        native = next(iter(natives), None)
        if native is not None:
            if native in native_seen:
                raise NistpCardError("duplicate_native_lot_identifier")
            native_seen.add(native)
        if target_lot_number is not None and number != target_lot_number:
            # Other sections can be incomplete or have a different contract.
            # Their title/price/status must never enter the requested lot.
            continue
        thead = table.select_one("thead th.table_header")
        heading = _HEADING.search(_clean(thead.get_text(" ", strip=True))) if thead else None
        if heading is None or int(heading.group(1)) != number:
            raise NistpCardError("lot_heading_does_not_match_table")
        if f"lot{number}" not in named_anchors:
            raise NistpCardError("lot_number_anchor_missing")
        fields = _label_values(table)
        declared_number = _field(fields, "Номер лота")
        if declared_number != str(number):
            raise NistpCardError("lot_number_field_mismatch")
        title = _field(fields, "Наименование имущества")
        descriptions = [
            value for label, value in fields.items()
            if (label.startswith(("cведения об имуществе", "сведения об имуществе"))
                and "описание" in label)
        ]
        if len(descriptions) != 1:
            raise NistpCardError("incomplete_numbered_lot_section")
        description = descriptions[0]
        price = _money(_field(fields, "Начальная цена"))
        deposit_text = fields.get("размер задатка, руб.", "")
        deposit = _money(deposit_text, allow_zero=True) if deposit_text else None
        status = _field(fields, "Статус торгов")
        if not re.search(r"\b(торги|при[её]м заявок)\b", status, re.IGNORECASE):
            raise NistpCardError("invalid_individual_lot_status")
        cadastre = _CADASTRE.search(description)
        area = _AREA.search(description)
        result[number] = NistpLotSection(
            trade_nid=trade_nid,
            trade_code=trade_code.group(1),
            lot_number=number,
            title=title[:500],
            description=description[:5000],
            cadastral_number=cadastre.group(0) if cadastre else None,
            area_sqm=float(area.group(1).replace(",", ".")) if area else None,
            initial_price_rub=price,
            deposit_rub=deposit,
            status_text=status,
            native_lot_nid=native,
            address=fields.get("местонахождение имущества") or None,
        )
    return result


def get_nistp_lot(
    html: str | bytes, *, trade_nid: str, lot_number: int,
) -> NistpLotSection:
    """Return precisely the requested numbered asset from the shared page."""
    if not 1 <= lot_number <= 99_999:
        raise NistpCardError("invalid_lot_number")
    sections = parse_nistp_shared_trade_card(
        html, trade_nid=trade_nid, target_lot_number=lot_number,
    )
    try:
        return sections[lot_number]
    except KeyError as exc:
        raise NistpCardError("requested_lot_missing_in_trade_card") from exc
