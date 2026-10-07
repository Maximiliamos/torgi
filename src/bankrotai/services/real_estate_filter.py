"""Behavior-preserving source-agnostic real-estate eligibility rules.

Split from scrapers.py (BAT-308). Import through scrapers.py continues to work.
No provider/network/DB dependency belongs in this module.
"""

from __future__ import annotations

import re

from bankrotai.domain import NormalizedLot


REAL_ESTATE_CATEGORIES = {
    "land", "real_estate", "apartment", "house", "commercial",
    "commercial_room", "commercial_building", "commercial_building_with_land",
    "parking", "unfinished", "complex", "office", "retail", "living",
}
REAL_ESTATE_TERMS = tuple(term.lower() for term in (
    "\u043d\u0435\u0434\u0432\u0438\u0436", "\u0437\u0435\u043c\u0435\u043b", "\u0443\u0447\u0430\u0441\u0442", "\u0437\u0434\u0430\u043d",
    "\u043f\u043e\u043c\u0435\u0449\u0435\u043d", "\u043a\u0432\u0430\u0440\u0442\u0438\u0440", "\u0436\u0438\u043b\u043e\u0439 \u0434\u043e\u043c", "\u0434\u043e\u043c\u043e\u0432\u043b\u0430\u0434",
    "\u0433\u0430\u0440\u0430\u0436", "\u043c\u0430\u0448\u0438\u043d\u043e-\u043c\u0435\u0441\u0442", "\u043c\u0430\u0448\u0438\u043d\u043e\u043c\u0435\u0441\u0442", "\u0441\u043e\u043e\u0440\u0443\u0436\u0435\u043d",
    "\u0438\u043c\u0443\u0449\u0435\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0439 \u043a\u043e\u043c\u043f\u043b\u0435\u043a\u0441", "\u043a\u0430\u0434\u0430\u0441\u0442\u0440\u043e\u0432",
))
MOVABLE_TERMS = tuple(term.lower() for term in (
    "\u0430\u0432\u0442\u043e\u043c\u043e\u0431\u0438\u043b", "\u0442\u0440\u0430\u043d\u0441\u043f\u043e\u0440\u0442\u043d\u043e\u0435 \u0441\u0440\u0435\u0434\u0441\u0442\u0432\u043e", "\u0434\u0432\u0438\u0436\u0438\u043c\u043e\u0435 \u0438\u043c\u0443\u0449\u0435\u0441\u0442\u0432\u043e",
))
RENTAL_TERMS = tuple(term.lower() for term in (
    "аренда", "аренды", "аренду", "арендный", "арендатор", "субаренда",
    "право заключения договора аренды", "договор найма", "право пользования",
))

_DISALLOWED_SHARE_RE = re.compile(r"\bдол(?:я|и|ю|ей|е)\b", re.IGNORECASE)
_DISALLOWED_HERITAGE_RE = re.compile(
    r"\b(?:объект(?:ом|а|ы)?\s+культурного\s+наследия|культурного\s+наследия|"
    r"выявленн(?:ый|ого|ому)\s+объект|ансамбл(?:ь|я|ем|и)|"
    r"памятник(?:ом|а|и)?\s+(?:архитектуры|истории)|ОКН)\b",
    re.IGNORECASE,
)


def has_disallowed_real_estate_terms(*values: object) -> bool:
    """Reject partial ownership and heritage assets from search and maps."""
    text = " ".join(str(value) for value in values if value).casefold()
    return bool(_DISALLOWED_SHARE_RE.search(text) or _DISALLOWED_HERITAGE_RE.search(text))


def is_real_estate_lot(lot: NormalizedLot) -> bool:
    category = (lot.category or "").lower()
    if category in REAL_ESTATE_CATEGORIES:
        return True
    if category in {"car", "vehicle", "transport", "movable"}:
        return False
    raw = lot.raw_data if isinstance(lot.raw_data, dict) else {}
    category_values = raw.get("category_titles") or raw.get("categories") or raw.get("category_display") or ""
    if isinstance(category_values, (list, tuple)):
        category_text = " ".join(str(item) for item in category_values)
    else:
        category_text = str(category_values)
    text = " ".join((lot.title or "", lot.description or "", lot.address or "", category_text)).lower()
    if any(term in text for term in MOVABLE_TERMS) and not any(term in text for term in REAL_ESTATE_TERMS):
        return False
    return any(term in text for term in REAL_ESTATE_TERMS)


def is_sale_real_estate_lot(lot: NormalizedLot) -> bool:
    """Keep real-estate sales and reject rentals even if a source mislabels them."""
    if not is_real_estate_lot(lot):
        return False
    raw = lot.raw_data if isinstance(lot.raw_data, dict) else {}
    text = " ".join(
        str(value)
        for value in (
            lot.title,
            lot.description,
            lot.address,
            lot.auction_type,
            raw.get("trade_type"),
            raw.get("typeTransaction"),
            raw.get("bidding_form"),
        )
        if value
    ).casefold()
    return not any(term in text for term in RENTAL_TERMS) and not has_disallowed_real_estate_terms(text)
