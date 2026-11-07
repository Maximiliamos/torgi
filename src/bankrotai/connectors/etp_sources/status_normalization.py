from __future__ import annotations

import re


def _status(value: str) -> str:
    """Map explicit public-auction states without guessing intermediate stages."""
    lowered = re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip().lower()
    if re.search(
        r"(?:при[её]м\s+заявок\s+заверш|заверш[её]н\s+при[её]м\s+заявок)",
        lowered,
    ):
        return "unknown"
    if "приостанов" in lowered:
        return "paused"
    closed_markers = (
        "торги отменен",
        "торги отменён",
        "не состоял",
        "торги заверш",
        "торги окончен",
        "аннулирован",
    )
    if any(marker in lowered for marker in closed_markers):
        return "closed"
    scheduled_markers = (
        "торги объявлен",
        "статус торгов объявлен",
        "статус торгов по лоту объявлен",
        "извещени",
        "торги назначен",
        "ожидается начало приема заявок",
        "ожидается начало приёма заявок",
    )
    if any(marker in lowered for marker in scheduled_markers):
        return "scheduled"
    if (
        re.search(r"при[её]м\s+заявок(?!\s+заверш)", lowered)
        or "идут торги" in lowered
        or "заявки принимаются" in lowered
    ):
        return "active"
    return "unknown"
