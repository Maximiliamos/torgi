"""Lot-scoped cadastral evidence and Moscow timestamps from official JSON."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from bankrotai.extractors import extract_cadastral_numbers


def collect_torgi_gov_cadastres(payload: dict[str, Any], text: str) -> list[str]:
    wanted = {"cadastralnumbers", "cadastralnumber", "cadnumber", "cadnumbers"}
    found: list[str] = []

    def append(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                append(child)
        elif isinstance(value, list):
            for child in value:
                append(child)
        elif value is not None:
            found.extend(extract_cadastral_numbers(str(value)))

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).casefold() in wanted:
                    append(child)
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    found.extend(extract_cadastral_numbers(text))
    return list(dict.fromkeys(found))


def parse_torgi_gov_date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for pattern in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(text[:19], pattern)
                    break
                except ValueError:
                    continue
            else:
                return None
    return parsed.astimezone(ZoneInfo("Europe/Moscow")).replace(tzinfo=None) if parsed.tzinfo is not None else parsed
