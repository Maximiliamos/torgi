"""Property-scoped address evidence shared by ETP detail adapters and geocoding.

Only the title and the specific asset's description are eligible inputs.
Whole-page "Address" labels may belong to the seller or trading venue.
This module does not fetch anything or change database records.
"""
from __future__ import annotations

import re

from bankrotai.extractors import extract_address
from bankrotai.regions import normalize_region_code, region_code_from_text


_STREET = re.compile(
    r"\b(?:ул\.?|улица|пр-?кт|пр-т|проспект|шоссе|пер\.?|переулок|"
    r"проезд|наб\.?|набережная|площадь|бульвар|б-р|тракт)\b",
    re.IGNORECASE,
)
_HOUSE = re.compile(
    r"\b(?:д\.|дом|вл\.|владение)\s*[№#]?\s*\d",
    re.IGNORECASE,
)
_UNIT = re.compile(
    r"\b(?:кв\.|квартира|пом\.|помещение|офис|м\s*/\s*м)\s*\d",
    re.IGNORECASE,
)
_LOCALITY = re.compile(
    r"\b(?:г\.|город|д\.|деревня|с\.|село|п\.|поселок|посёлок)\s*\S",
    re.IGNORECASE,
)
_LEGAL_TAIL = re.compile(
    r"\.\s+(?:В\s+залоге\b|Находится\s+в\s+(?:совместной|долевой)\s+"
    r"собственности\b|Обременения\s*:)",
    re.IGNORECASE,
)


def _clean_address(value: str) -> str:
    value = _LEGAL_TAIL.split(value, maxsplit=1)[0]
    value = re.split(
        r"\.\s+(?=(?:Площадь|Общая\s+площадь|Начальная\s+цена|"
        r"Назначение|Категория|Обременения)\b)",
        value, maxsplit=1, flags=re.IGNORECASE,
    )[0]
    return " ".join(value.split()).strip(" ,.;:-")[:350]


def _extract_property_scoped_address(text: str) -> str | None:
    """Extract address phrases that are safe only inside an asset description."""
    clean = " ".join(text.split())
    # Cadastral/land descriptions often use this exact Rosreestr-style phrase.
    # It is unsafe as a whole-page rule because a generic postal address may
    # belong to the debtor/organizer, but title/description here are already
    # scoped to the sold property.
    landmark = re.search(
        r"\bпочтовый\s+адрес\s+ориентира\s*:\s*(.+?)"
        r"(?=\s+(?:Площадь|Кадастров(?:ый|ые)\s+номер|Категория)\s*:|$)",
        clean,
        re.IGNORECASE,
    )
    if landmark:
        value = _clean_address(landmark.group(1))
        if value:
            return value
    return extract_address(clean)


def property_address_precision(value: str | None) -> int:
    """A conservative evidence score; not a geocoder confidence value."""
    if not value:
        return 0
    street = bool(_STREET.search(value))
    house = bool(_HOUSE.search(value))
    region = bool(region_code_from_text(value))
    locality = bool(_LOCALITY.search(value))
    unit = bool(_UNIT.search(value))
    return 4 * (street and house) + 2 * street + 2 * house + region + locality + unit


def extract_asset_property_address(
    *, title: str | None = None, description: str | None = None,
    cadastral_number: str | None = None,
) -> str | None:
    """Extract explicit *asset* address, never a generic seller/venue label.

    Ignore addresses conflicting with a known cadastral region. If the title
    and description each assert conflicting numbered street/building addresses
    without an independently verified identity, refuse to choose either.
    """
    cad_region = None
    match = re.fullmatch(r"(\d{2}):\d{1,2}:\d{5,9}:\d{1,12}", (cadastral_number or "").strip())
    if match:
        cad_region = normalize_region_code(match.group(1))

    candidates: list[tuple[int, str]] = []
    for text in (title, description):
        if not text:
            continue
        address = _extract_property_scoped_address(text)
        if not address:
            continue
        address = _clean_address(address)
        detected = region_code_from_text(address)
        if cad_region and detected and detected != cad_region:
            continue
        candidates.append((property_address_precision(address), address))
    if not candidates:
        return None
    def street_house_key(value: str) -> tuple[str, str] | None:
        street = re.search(
            r"\b(ул\.?|улица|пр-?кт|пр-т|проспект|шоссе|переулок|пер\.|"
            r"проезд|набережная|площадь)\s+([^,;.]+)",
            value, re.IGNORECASE,
        )
        house = re.search(
            r"\b(?:д\.|дом|вл\.|владение)\s*[№#]?\s*(\d+[а-яa-z]?)",
            value, re.IGNORECASE,
        )
        if not street or not house:
            return None
        kind = street.group(1).casefold().strip(".")
        kind = {
            "ул": "улица", "пр-кт": "проспект", "пркт": "проспект",
            "пр-т": "проспект", "пер": "переулок",
        }.get(kind, kind)
        name = re.sub(r"[^а-яёa-z0-9]+", "", street.group(2).casefold())
        return kind + name, house.group(1).casefold()

    signatures = {
        signature for _, address in candidates
        if (signature := street_house_key(address)) is not None
    }
    # Two different building claims in the same verified lot mean review,
    # not an arbitrary ranking between title and description.
    if len(signatures) > 1:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def prefer_evidenced_property_address(
    stored_address: str | None, *, title: str | None = None,
    description: str | None = None, cadastral_number: str | None = None,
) -> str | None:
    """Prefer a more specific per-asset address over a stale generic address.

    An explicit asset address containing street and building is authoritative
    over a conflicting generic field, unless source fields conflict. A vague
    asset address only fills a missing stored value; never downgrades it.
    """
    evidenced = extract_asset_property_address(
        title=title, description=description, cadastral_number=cadastral_number,
    )
    if not evidenced:
        return stored_address
    if not stored_address:
        return evidenced
    if property_address_precision(evidenced) >= 8:
        return evidenced
    return stored_address
