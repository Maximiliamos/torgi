"""Fail-closed UTPL individual-lot extraction.

Only the provider's numbered individual-lot section may supply identity,
prices, status and property fields. Procedure headers, deposits, auction steps
and public-offer schedule rows are never reused as lot values.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from datetime import datetime
from typing import TypedDict

from bs4 import BeautifulSoup


class UtplLotSchemaError(ValueError):
    pass


class UtplDetailFields(TypedDict):
    lot_number: str
    procedure_number: str | None
    title: str
    category: str
    classifier: str | None
    property_kinds: tuple[str, ...]
    initial_price: float | None
    current_price: float | None
    source_status_text: str | None
    cadastral_number: str | None
    area: float | None
    vin: str | None
    address: str | None
    application_start: datetime | None
    application_deadline: datetime | None
    auction_at: datetime | None


_SPACE = re.compile(r"\s+")
_CADASTRAL = re.compile(r"(?<!\d)\d{2}:\d{1,3}:\d{5,9}:\d{1,9}(?!\d)")
_AREA = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", re.I)
_VIN = re.compile(r"(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Z0-9])", re.I)


def _text(value: str) -> str:
    return _SPACE.sub(" ", value.replace("\xa0", " ")).strip()


def _datetime_after(section: str, label: str) -> datetime | None:
    match = re.search(
        label + r"\s*[:：]\s*(\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)",
        section,
        re.I,
    )
    if match is None:
        return None
    raw = match.group(1)
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def _amount(section: str, label: str) -> float | None:
    match = re.search(
        r"(?<![\w])" + label
        + r"\s*[:：]\s*(\d[\d\s\u00a0\u202f]*?(?:[.,]\d{1,2})?)"
        + r"(?=\s*(?:Дата|Начальная цена|Текущая цена|Порядок|Тип снижения|"
          r"Шаг|Сведения об имуществе|Классификатор ЕФРСБ|$))",
        section,
        re.I,
    )
    if match is None:
        return None
    raw = re.sub(r"[\s\u00a0\u202f]", "", match.group(1)).replace(",", ".")
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return None
    if not amount.is_finite() or not Decimal(0) < amount < Decimal("1e15"):
        return None
    return float(amount)


def _structured_property_kinds(classifier: str, lot_text: str) -> tuple[str, ...]:
    source = classifier.lower()
    lot_lower = lot_text.lower()
    kinds: list[str] = []
    if re.search(
        r"прав[ао]\s+требован|уступк\w*\s+требован|дебитор",
        source + " " + lot_lower,
        re.I,
    ):
        kinds.append("receivables")
    if re.search(r"аренд|право пользования", source, re.I) or re.search(
        r"право\s+аренды|право\s+пользования", lot_lower, re.I
    ):
        kinds.append("lease")
    if re.search(r"земельн|земл[ия]|участк", source, re.I) or re.search(
        r"земельн\w*\s+участ", lot_lower, re.I
    ):
        kinds.append("land")
    real_estate_pattern = (
        r"недвижим|жил\w*\s+(?:здани|помещ)|нежил\w*|квартир|помещен|"
        r"здани|сооружен|незавершенн\w*\s+строительств|гараж"
    )
    if re.search(real_estate_pattern, source, re.I) or re.search(
        r"\b(?:здание|сооружение|нежилое помещение|жилой дом|квартира)\b",
        lot_lower,
        re.I,
    ):
        kinds.append("real_estate")
    if re.search(r"автомоб|транспорт|воздушн\w*\s+суд|самолет|прицеп", source, re.I) or re.search(
        r"\b(?:автомобиль|транспортное средство|прицеп)\b|\bVIN\s*[:№]",
        lot_text,
        re.I,
    ):
        kinds.append("vehicle")
    if re.search(
        r"машин|оборудован|специализированн\w*\s+техник|станок|подъемн",
        source,
        re.I,
    ) or re.search(
        r"\b(?:оборудование|станок|подъемник|топливораздаточн\w*\s+колонк|"
        r"резервуар|компрессор)\b",
        lot_lower,
        re.I,
    ):
        kinds.append("equipment")
    if re.search(r"товарн\w*\s+знак|интеллектуальн|патент", source, re.I):
        kinds.append("intellectual_property")
    return tuple(dict.fromkeys(kinds))


def _domain_category(kinds: tuple[str, ...], title: str) -> str:
    if "receivables" in kinds:
        return "receivables"
    if "real_estate" in kinds:
        return "real_estate"
    if "lease" in kinds:
        return "other"
    if "land" in kinds:
        return "land"
    if "vehicle" in kinds:
        return "vehicle"
    lowered = title.lower()
    if re.search(r"прав[оа]\s+требован|дебитор", lowered):
        return "receivables"
    if re.search(r"аренд|право пользования", lowered):
        return "other"
    if re.search(r"земельн|участок|кадастров", lowered):
        return "land"
    if re.search(r"квартир|помещен|здани|сооружен|жилой дом", lowered):
        return "real_estate"
    if re.search(r"автомоб|транспорт|vin", lowered):
        return "vehicle"
    return "other"


def parse_utpl_detail_fields(html: str | bytes) -> UtplDetailFields:
    soup = BeautifulSoup(html, "html.parser")
    full_text = _text(soup.get_text(" ", strip=True))
    sections = list(re.finditer(r"Информация о лоте\s*№\s*(\d+)\b", full_text, re.I))
    if len(sections) != 1:
        raise UtplLotSchemaError("UTPL requires exactly one individual lot section")

    section = full_text[sections[0].end():]
    number = sections[0].group(1)
    explicit_number = re.search(r"\bНомер:\s*(\d+)\b", section)
    if explicit_number and int(explicit_number.group(1)) != int(number):
        raise UtplLotSchemaError("UTPL lot section number mismatch")

    name = re.search(
        r"\bНаименование:\s*(.+?)\s*(?=Дата\s+(?:начала|окончания|проведения|подведения)|"
        r"Статус:|Текущая цена|Начальная цена|Порядок оформления)",
        section,
        re.I,
    )
    if name is None or len(name.group(1).strip()) < 6:
        raise UtplLotSchemaError("UTPL individual lot name unverified")
    title = _text(name.group(1))

    summary = section.split("Порядок оформления участия", 1)[0]
    initial = _amount(summary, r"Начальная цена,?\s*руб\.?")
    current = _amount(summary, r"Текущая цена,?\s*руб\.?")
    status_match = re.search(
        r"\bСтатус:\s*(.+?)\s*(?=Дата\s+(?:начала|окончания|проведения|подведения|"
        r"завершения)|Текущая цена|Начальная цена|Порядок оформления)",
        summary,
        re.I,
    )

    classifier_match = re.search(
        r"Классификатор ЕФРСБ:\s*(.{1,350}?)(?=Интервалы снижения цены|"
        r"Обеспечение заявки|Шаг,\s*%|Документы|$)",
        section,
        re.I,
    )
    classifier = _text(classifier_match.group(1)) if classifier_match else ""

    asset_match = re.search(
        r"Сведения об имуществе должника[^:]*:\s*(.{1,3000}?)(?=Классификатор ЕФРСБ:|"
        r"Интервалы снижения цены|Обеспечение заявки|Документы|$)",
        section,
        re.I,
    )
    asset_text = _text(asset_match.group(1)) if asset_match else title
    lot_text = _text(title + " " + asset_text)

    kinds = _structured_property_kinds(classifier, lot_text)
    category = _domain_category(kinds, title)

    cadastral_match = _CADASTRAL.search(lot_text)
    area_match = _AREA.search(lot_text)
    vin_match = _VIN.search(lot_text)
    address_match = re.search(
        r"\b(?:адрес(?:\s+(?:объекта|расположения))?|местоположение)\s*:\s*"
        r"(.{5,300}?)(?=\s+(?:кадастров|площад|назначение|вид права|"
        r"Классификатор)|$)",
        lot_text,
        re.I,
    )
    procedure_match = re.search(
        r"Информация\s+(?:об\s+аукционе|о\s+конкурсе|о\s+публичном\s+предложении)"
        r"\s*№\s*(\d+)\b",
        full_text,
        re.I,
    )

    return {
        "lot_number": number,
        "procedure_number": procedure_match.group(1) if procedure_match else None,
        "title": title,
        "category": category,
        "classifier": classifier or None,
        "property_kinds": kinds,
        "initial_price": initial,
        "current_price": current,
        "source_status_text": _text(status_match.group(1)) if status_match else None,
        "cadastral_number": cadastral_match.group(0) if cadastral_match else None,
        "area": float(area_match.group(1).replace(",", ".")) if area_match else None,
        "vin": vin_match.group(0).upper() if vin_match else None,
        "address": _text(address_match.group(1)) if address_match else None,
        "application_start": _datetime_after(
            section, r"Дата начала представления заявок на участие"
        ),
        "application_deadline": _datetime_after(
            section, r"Дата окончания представления заявок на участие"
        ),
        "auction_at": _datetime_after(section, r"Дата проведения"),
    }




def utpl_next_postback(html: str | bytes, current_page: int) -> tuple[str, str] | None:
    """Return only a provider-supplied postback for the next catalogue page.

    UTPL pagers show numeric links in blocks of ten. At a block boundary the
    next page can be exposed only through a forward control. The function never
    synthesizes an ASP.NET target; it accepts a literal postback present in HTML.
    """
    soup = BeautifulSoup(html, "html.parser")
    anchors = list(soup.select("a[href]"))

    def parsed(anchor) -> tuple[str, str] | None:
        href = str(anchor.get("href") or "")
        match = re.search(
            r"__doPostBack\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*\)",
            href,
        )
        return match.groups() if match else None

    wanted = str(current_page + 1)
    for anchor in anchors:
        if _text(anchor.get_text(" ", strip=True)) == wanted:
            value = parsed(anchor)
            if value is not None:
                return value

    for anchor in anchors:
        if _text(anchor.get_text(" ", strip=True)).lower() in {
            ">>", "»", "›", "далее", "следующая"
        }:
            value = parsed(anchor)
            if value is not None:
                return value
    return None

def normalize_utpl_status(value: str | None) -> str:
    """Map only explicit official UTPL lot-state labels; never infer from dates."""
    if value is None:
        return "unknown"
    label = _text(value).lower().replace("ё", "е")
    exact = {
        "прием заявок": "active",
        "извещение опубликовано": "scheduled",
        "не состоялся": "closed",
        "не состоялись": "closed",
        "окончен": "closed",
        "торги завершены": "closed",
        "отменен": "closed",
        "отменен организатором": "closed",
        "приостановлен": "paused",
    }
    return exact.get(label, "unknown")
