"""Read-only Vertrades shared-procedure detail inspection.

This module intentionally does NOT enable detail enrichment: the public
/showTrades?id=... route is shared by several independently priced lots.
The output is evidence for a manual source-contract review, not ingestion.
Never retain or log the full provider page (it can contain personal data).
"""
from __future__ import annotations

import re
from bs4 import BeautifulSoup

_HEADER = re.compile(r"^Сведения\s+по\s+лоту\s*№\s*(\d{1,6})$", re.I)
_PRICE = re.compile(r"^Начальная\s+цена\s+продажи\s+имущества(?:,\s*руб\.)?$", re.I)
_STATUS = re.compile(r"^Статус\s+торгов\s+по\s+лоту$", re.I)
_VALUE = re.compile(r"^(\d[\d\s\u00a0]*,\d{2})(?:\s|$)")
_MAX_LENGTH = 2_000_000
_ALLOWED_STATUSES = frozenset({
    "Торги завершены", "Торги по лоту отменены",
    "Идет прием заявок", "Идёт приём заявок",
    "Прием заявок завершен", "Приём заявок завершён",
})


class VertradesDetailContractError(ValueError):
    """The shared procedure card cannot prove isolated individual lots."""


def _lines(html: str | bytes) -> list[str]:
    if len(html) > _MAX_LENGTH:
        raise VertradesDetailContractError("Page exceeds bounded audit size")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return [" ".join(s.split()) for s in soup.get_text("\n").splitlines() if s.strip()]


def inspect_shared_procedure(html: str | bytes) -> list[dict[str, str | int | None]]:
    """Identify clearly separated lot sections, without collecting PII.

    A verified per-lot status/price is only returned when the field occurs
    exactly once inside that lot's section. Missing or ambiguous fields are
    null, never inherited from the procedure or another lot.
    """
    lines = _lines(html)
    positions = [(i, int(m.group(1))) for i, line in enumerate(lines)
                 if (m := _HEADER.fullmatch(line))]
    if not positions:
        raise VertradesDetailContractError("No explicit individual lot headings")
    numbers = [num for _, num in positions]
    if len(set(numbers)) != len(numbers):
        raise VertradesDetailContractError("Duplicate lot section in shared procedure")
    results = []
    for j, (start, number) in enumerate(positions):
        end = positions[j + 1][0] if j + 1 < len(positions) else len(lines)
        section = lines[start + 1:end]
        statuses = [section[i + 1] for i, s in enumerate(section[:-1])
                    if _STATUS.fullmatch(s) and section[i + 1] in _ALLOWED_STATUSES]
        prices = []
        for i, s in enumerate(section[:-1]):
            if _PRICE.fullmatch(s):
                m = _VALUE.match(section[i + 1])
                if m:
                    prices.append(m.group(1).replace(" ", "").replace("\u00a0", ""))
        results.append({
            "lot_number": number,
            "source_status": statuses[0] if len(statuses) == 1 else None,
            "initial_price_rub": prices[0] if len(prices) == 1 else None,
        })
    return results
