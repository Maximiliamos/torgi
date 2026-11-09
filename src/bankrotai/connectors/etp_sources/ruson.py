from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag


MOSCOW = ZoneInfo("Europe/Moscow")
_SPACE_RE = re.compile(r"\s+")
_CURRENCY_RE = re.compile(r"(?:₽|руб(?:\.|лей)?|RUB)", re.IGNORECASE)
_MONEY_FULL_RE = re.compile(
    r"^\s*(\d{1,3}(?:[\s\u00a0]\d{3})+|\d{1,14})(?:[.,](\d{1,2}))?\s*"
    r"(?:₽|руб(?:\.|лей)?|RUB)?\s*$",
    re.IGNORECASE,
)


def _clean(value: str) -> str:
    return _SPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()


def _key(value: str) -> str:
    return _clean(value).casefold().replace("ё", "е").strip(" :|")


def _parse_money(value: str | None) -> float | None:
    if not value:
        return None
    match = _MONEY_FULL_RE.fullmatch(_clean(value))
    if not match:
        return None
    whole = re.sub(r"[\s\u00a0]", "", match.group(1))
    fraction = match.group(2)
    raw = whole + ("." + fraction if fraction is not None else "")
    try:
        amount = float(raw)
    except ValueError:
        return None
    return amount if 0 < amount < 1e15 else None


def _money_format(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _clean(value)
    numeric = _CURRENCY_RE.sub("", cleaned).strip()
    if "," in numeric:
        return "decimal_comma"
    if "." in numeric:
        return "decimal_dot"
    return "integer"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    match = re.search(
        r"(\d{2}\.\d{2}\.(?:\d{2}|\d{4}))(?:\s+(\d{2}:\d{2}(?::\d{2})?))?",
        value,
    )
    if not match:
        return None
    raw = match.group(1) + " " + (match.group(2) or "00:00")
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%y %H:%M:%S", "%d.%m.%y %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def _to_utc_naive(local_value: datetime) -> datetime:
    return local_value.replace(tzinfo=MOSCOW).astimezone(timezone.utc).replace(tzinfo=None)


def _scalar_fields(soup: BeautifulSoup) -> dict[str, str]:
    fields: dict[str, str] = {}
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        label = _key(cells[0].get_text(" ", strip=True))
        value = _clean(" ".join(cell.get_text(" ", strip=True) for cell in cells[1:]))
        if label and value and label not in fields:
            fields[label] = value
    return fields


def _field(fields: dict[str, str], *labels: str) -> str | None:
    wanted = tuple(_key(label) for label in labels)
    for label in wanted:
        if label in fields:
            return fields[label]
    for key, value in fields.items():
        if any(key.startswith(label) for label in wanted):
            return value
    return None


def _fallback_label(text: str, label: str, *, stop: str) -> str | None:
    match = re.search(
        label + r"\s*[:|]?\s*(.+?)(?=\s+(?:" + stop + r")\s*[:|]?\s*|\Z)",
        text,
        re.IGNORECASE,
    )
    return _clean(match.group(1)) if match else None


def _source_category(categories: str, asset_text: str) -> str:
    explicit = _key(categories)
    asset = _key(asset_text)
    if re.search(r"транспорт|автомоб|авто\b|vehicle", explicit):
        return "vehicle"
    if re.search(r"дебитор|прав[оа]\s+требован", explicit):
        return "receivables"
    if "земель" in explicit and not re.search(r"жил|недвиж", explicit):
        return "land"
    if re.search(r"жил|недвиж|квартир|помещен|здани|гараж|машино.?мест", explicit):
        return "real_estate"
    if re.search(r"автомоб|транспорт|\bvin\b|прицеп|снегоход", asset):
        return "vehicle"
    if re.search(r"прав[оа]\s+требован|дебитор", asset):
        return "receivables"
    if re.search(r"квартир|комнат|жилой\s+дом|недвижим|помещен|здани|гараж|машино.?мест", asset):
        return "real_estate"
    if re.search(r"земельн|участок|кадастров", asset):
        return "land"
    return "other"


def _extract_address(location: str | None, description: str) -> str | None:
    if location:
        cleaned = _clean(location)
        return cleaned[:500] if cleaned else None
    match = re.search(
        r"\bадрес(?:\s*\(местонахождение\))?\s*:\s*(.+?)"
        r"(?=(?:,\s*)?(?:площад|кадастров|вид\s+права|$))",
        description,
        re.IGNORECASE,
    )
    if not match:
        return None
    value = _clean(match.group(1)).strip(" ,.;")
    return value[:500] if value else None


def _public_offer_schedule(soup: BeautifulSoup) -> list[dict[str, Any]]:
    for table in soup.select("table"):
        rows = table.select("tr")
        header_index: int | None = None
        columns: dict[str, int] = {}
        for index, row in enumerate(rows):
            cells = row.find_all(["th", "td"], recursive=False)
            labels = [_key(cell.get_text(" ", strip=True)) for cell in cells]
            joined = " | ".join(labels)
            if "цена на периоде" not in joined or "дата начала" not in joined or "дата окончания" not in joined:
                continue
            header_index = index
            for pos, label in enumerate(labels):
                if "дата начала" in label:
                    columns["start"] = pos
                elif "дата окончания" in label:
                    columns["end"] = pos
                elif "цена на периоде" in label:
                    columns["price"] = pos
                elif "задат" in label:
                    columns["deposit"] = pos
            break
        if header_index is None or not {"start", "end", "price"} <= columns.keys():
            continue
        result: list[dict[str, Any]] = []
        for row in rows[header_index + 1:]:
            cells = row.find_all(["th", "td"], recursive=False)
            max_index = max(columns.values())
            if len(cells) <= max_index:
                continue
            values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
            local_start = _parse_datetime(values[columns["start"]])
            local_end = _parse_datetime(values[columns["end"]])
            price = _parse_money(values[columns["price"]])
            if local_start is None or local_end is None or price is None:
                continue
            # Source publishes minute-precision inclusive period ends (e.g. 13:59).
            # Store an exclusive UTC boundary one minute later so there is no false
            # one-minute gap before the next source period starts at 14:00.
            end_exclusive = local_end + timedelta(minutes=1)
            item: dict[str, Any] = {
                "starts_at": _to_utc_naive(local_start).isoformat(),
                "ends_at": _to_utc_naive(end_exclusive).isoformat(),
                "price": price,
                "source_timezone": "Europe/Moscow",
            }
            if "deposit" in columns:
                deposit = _parse_money(values[columns["deposit"]])
                if deposit is not None:
                    item["deposit_amount"] = deposit
            result.append(item)
        if result:
            return result
    return []


def _active_period(
    schedule: list[dict[str, Any]],
    observed_at_utc: datetime,
) -> tuple[float, datetime | None, float | None] | None:
    for index, period in enumerate(schedule):
        try:
            starts_at = datetime.fromisoformat(str(period["starts_at"]))
            ends_at = datetime.fromisoformat(str(period["ends_at"]))
            price = float(period["price"])
        except (KeyError, TypeError, ValueError):
            continue
        if starts_at <= observed_at_utc < ends_at:
            next_price = (
                float(schedule[index + 1]["price"])
                if index + 1 < len(schedule) else None
            )
            return price, ends_at, next_price
    return None


def parse_ruson_detail_fields(
    soup: BeautifulSoup,
    *,
    observed_at_utc: datetime | None = None,
) -> dict[str, Any]:
    """Parse RUSSIA OnLine lot fields without borrowing values from unrelated page sections."""
    fields = _scalar_fields(soup)
    text = _clean(soup.get_text(" ", strip=True))

    title = _field(fields, "Наименование имущества") or _fallback_label(
        text,
        r"Наименование имущества",
        stop=r"C?ведения об имуществе|Является объектом недвижимости|Начальная цена",
    )
    description = _field(
        fields,
        "Cведения об имуществе (предприятии) должника, выставляемом на торги, его составе, характеристиках, описание",
        "Сведения об имуществе (предприятии) должника, выставляемом на торги, его составе, характеристиках, описание",
    ) or title or ""
    location = _field(fields, "Местонахождение имущества", "Место нахождения имущества")
    categories = _field(fields, "Категории лота") or ""
    status_text = _field(fields, "Статус торгов") or ""

    initial_raw = _field(fields, "Начальная цена")
    if initial_raw is None:
        initial_raw = _fallback_label(
            text,
            r"Начальн\w*\s+цен\w*",
            stop=r"Шаг аукциона|Размер задатка|Категории лота|Статус торгов",
        )
    start_price = _parse_money(initial_raw)

    schedule = _public_offer_schedule(soup)
    now_utc = observed_at_utc or datetime.now(timezone.utc).replace(tzinfo=None)
    if now_utc.tzinfo is not None:
        now_utc = now_utc.astimezone(timezone.utc).replace(tzinfo=None)
    active = _active_period(schedule, now_utc)

    source_type = _field(fields, "Тип торгов") or ""
    source_type_key = _key(source_type)
    if "публич" in source_type_key:
        auction_type = "public_offer"
        detail_template = "public_offer"
    elif "аукцион" in source_type_key:
        auction_type = "auction"
        detail_template = "auction"
    else:
        auction_type = None
        detail_template = "unknown"

    heading = next(
        (_clean(node.get_text(" ", strip=True)) for node in soup.select("h1, h2, h3")
         if "торгов" in _key(node.get_text(" ", strip=True))),
        "",
    )
    procedure_match = re.search(r"торгов\s+([0-9]+-[A-ZА-Я]+)", heading, re.IGNORECASE)

    return {
        "title": title,
        "asset_text": _clean(" ".join(part for part in (title or "", description, location or "", categories) if part)),
        "address": _extract_address(location, description),
        "category": _source_category(categories, " ".join((title or "", description))),
        "source_categories": categories or None,
        "status_text": status_text,
        "start_price": start_price,
        "initial_price_format": _money_format(initial_raw),
        "initial_price_had_currency": bool(initial_raw and _CURRENCY_RE.search(initial_raw)),
        "deposit_amount": _parse_money(_field(fields, "Размер задатка")),
        "auction_step_amount": _parse_money(_field(fields, "Шаг аукциона")),
        "application_start_at": _parse_datetime(_field(
            fields,
            "Дата начала представления заявок на участие",
            "Дата начала приема заявок",
            "Дата начала приёма заявок",
        )),
        "application_deadline": _parse_datetime(_field(
            fields,
            "Дата окончания представления заявок на участие",
            "Дата окончания приема заявок",
            "Дата окончания приёма заявок",
        )),
        "auction_at": _parse_datetime(_field(fields, "Дата проведения")),
        "auction_type": auction_type,
        "public_offer_schedule": schedule or None,
        "current_price": active[0] if active else None,
        "next_price_reduction_at": active[1] if active else None,
        "next_interval_price": active[2] if active else None,
        "current_price_source": "public_offer_schedule" if active else None,
        "procedure_number": procedure_match.group(1) if procedure_match else None,
        "detail_template": detail_template,
    }
