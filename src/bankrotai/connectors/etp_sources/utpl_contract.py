"""Fail-closed field isolation for UTPL individual bankruptcy lot pages.

The procedure header, settlement instructions and price-period rows are not
individual lot fields. Only the section beginning 'Информация о лоте №N'
is allowed to supply lot identity, description, category and prices.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup


class UtplLotSchemaError(ValueError):
    pass


def _amount(section: str, label: str) -> float | None:
    pattern = (
        r"(?<![\w])" + label +
        r"\s*[:：]\s*(\d[\d\s\u00a0\u202f]*?(?:[.,]\d{1,2})?)"
        r"(?=\s*(?:Начальная цена|Текущая цена|Порядок|Тип снижения|$))"
    )
    match = re.search(pattern, section, re.I)
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


def parse_utpl_detail_fields(html: str | bytes) -> dict[str, object]:
    soup = BeautifulSoup(html, "html.parser")
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    sections = list(re.finditer(r"Информация о лоте\s*№\s*(\d+)\b", text, re.I))
    if len(sections) != 1:
        raise UtplLotSchemaError("UTPL requires exactly one individual lot section")
    section = text[sections[0].end():]
    number = sections[0].group(1)
    explicit_number = re.search(r"\bНомер:\s*(\d+)\b", section)
    if explicit_number and int(explicit_number.group(1)) != int(number):
        raise UtplLotSchemaError("UTPL lot section number mismatch")
    name = re.search(
        r"\bНаименование:\s*(.+?)\s*(?=Дата окончания|Статус:|Текущая цена|Начальная цена)",
        section, re.I,
    )
    if name is None or len(name.group(1).strip()) < 6:
        raise UtplLotSchemaError("UTPL individual lot name unverified")
    # Restrict prices to the lot's own labelled summary, never to the schedule
    # of future intervals or deposit instructions.
    summary = section.split("Порядок оформления участия", 1)[0]
    initial = _amount(summary, r"Начальная цена,?\s*руб\.?")
    current = _amount(summary, r"Текущая цена,?\s*руб\.?")
    status_match = re.search(
        r"\bСтатус:\s*(.+?)\s*(?=Текущая цена|Начальная цена|Порядок оформления)", summary, re.I,
    )
    classifier_match = re.search(
        r"Классификатор ЕФРСБ:\s*(.{1,110}?)(?=Интервалы снижения цены|Обеспечение заявки|$)",
        section, re.I,
    )
    classifier = classifier_match.group(1).strip() if classifier_match else ""
    title = name.group(1).strip()
    category_text = classifier.lower() if classifier else title.lower()
    # Rights to claim or lease are not a land/freehold sale merely because
    # the name refers to a flat, plot or cadastral number.
    if re.search(r"прав[оа]\s+требован|дебитор", category_text, re.I):
        category = "receivables"
    elif re.search(r"аренд|право пользования", category_text, re.I):
        category = "other"
    elif re.search(r"земельн|земл[ия]|участк", category_text, re.I):
        category = "land"
    elif re.search(r"недвижим|квартир|помещен|здани|жилой дом|гараж|сооружен", category_text, re.I):
        category = "real_estate"
    elif re.search(r"автомоб|транспорт|воздушн|судн|оборудован", category_text, re.I):
        category = "vehicle" if re.search(r"автомоб|транспорт|воздушн|судн", category_text, re.I) else "other"
    else:
        category = "other"
    return {
        "lot_number": number,
        "title": title,
        "category": category,
        "classifier": classifier or None,
        "initial_price": initial,
        "current_price": current,
        "source_status_text": status_match.group(1).strip() if status_match else None,
    }

def normalize_utpl_status(value: str | None) -> str:
    """Only official UTPL lot-state labels; never infer end from dates."""
    if value is None:
        return "unknown"
    label = re.sub(r"\s+", " ", value).strip().lower().replace("ё", "е")
    exact = {
        "прием заявок": "active",
        "извещение опубликовано": "scheduled",
        "не состоялся": "closed",
        "не состоялись": "closed",
        "окончен": "closed",
        "отменен": "closed",
        "приостановлен": "paused",
    }
    return exact.get(label, "unknown")
