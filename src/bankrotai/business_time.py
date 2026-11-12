from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def to_moscow_datetime(value: Any) -> datetime | None:
    """Return lot-facing business time as naive Europe/Moscow wall clock."""
    if isinstance(value, datetime):
        parsed = value
    elif value in (None, ""):
        return None
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = None
        if parsed is None:
            for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(text[:19], fmt)
                    break
                except ValueError:
                    continue
        if parsed is None:
            return None
    assert parsed is not None
    return parsed.astimezone(MOSCOW).replace(tzinfo=None) if parsed.tzinfo else parsed
