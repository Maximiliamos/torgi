from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_SPACE_RE = re.compile(r"\s+")
_NUMBER_RE = re.compile(r"\d{1,3}(?:[\s\u00a0]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?")


@dataclass(frozen=True, slots=True)
class LabelledDetailFields:
    title: str | None = None
    address: str | None = None
    status: str | None = None
    start_price: float | None = None
    current_price: float | None = None


def _clean(value: str) -> str:
    return _SPACE_RE.sub(" ", value.replace("\xa0", " ")).strip(" :")


def _amount(value: str) -> float | None:
    match = _NUMBER_RE.search(_clean(value))
    if match is None:
        return None
    raw = re.sub(r"[\s\u00a0]", "", match.group(0)).replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        return None
    return amount if 0 < amount < 1e15 else None


def _address_from_title(title: str | None) -> str | None:
    if not title:
        return None
    match = re.search(
        r"(?:по\s+адресу|адрес\s*\(?местонахождени[ея]\)?|местонахождение)\s*:\s*"
        r"(.{8,350}?)(?=\s*,?\s*(?:кадастров\w*|кад\.?\s*№|вид\s+права|площадь)\b|$)",
        title,
        re.IGNORECASE,
    )
    return _clean(match.group(1)) if match else None


def extract_labelled_detail_fields(soup: Any) -> LabelledDetailFields:
    values: dict[str, list[str]] = {}
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        for index in range(0, len(cells) - 1, 2):
            label = _clean(cells[index].get_text(" ", strip=True)).lower()
            value = _clean(cells[index + 1].get_text(" ", strip=True))
            if label and value:
                values.setdefault(label, []).append(value)

    def first(pattern: str, *, longest: bool = False) -> str | None:
        matched: list[str] = []
        for label, candidates in values.items():
            if re.fullmatch(pattern, label, re.IGNORECASE):
                matched.extend(candidate for candidate in candidates if candidate)
        return max(matched, key=len) if longest and matched else (matched[0] if matched else None)

    title = first(
        r"(?:наименование(?:\s+имущества)?|предмет\s+(?:договора\s*\(лота\)|торгов)|"
        r"сведения\s+об\s+имуществе.*)",
        longest=True,
    )
    address = first(
        r"(?:адрес\s+местонахождения\s+имущества|место(?:нахождение|\s+нахождения)\s+имущества|"
        r"адрес\s*\(местонахождение\))",
        longest=True,
    ) or _address_from_title(title)
    status = first(r"статус(?:\s+торгов(?:\s+по\s+лоту)?)?")
    start_price_text = first(
        r"(?:начальная|стартовая)(?:\s*\([^)]*\))?\s+цена(?:\s+продажи\s+имущества)?(?:,?\s*руб\.?)?"
    )
    current_price_text = first(r"текущая\s+цена(?:,?\s*руб\.?)?")
    return LabelledDetailFields(
        title=title,
        address=address,
        status=status,
        start_price=_amount(start_price_text) if start_price_text else None,
        current_price=_amount(current_price_text) if current_price_text else None,
    )
