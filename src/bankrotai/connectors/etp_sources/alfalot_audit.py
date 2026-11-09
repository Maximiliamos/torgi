"""Fail-closed, read-only evidence helpers for the legacy ALFALOT search.

These helpers deliberately do NOT provide a production listing contract.
GD is a browser filter identifier, not a proven HTTP API parameter.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from bs4 import BeautifulSoup

HOST = "etp.alfalot.ru"
SEARCH_URL = "https://etp.alfalot.ru/app/SearchLots/page"
STATE_ELEMENT_ID = "stateFilter_GD"
LOT_NUMBER = re.compile(r"\b(?P<trade>\d{8,12}[A-Z]{2})\.lot(?P<lot>\d+)\b", re.I)
_PROPERTY = re.compile(r"земельн\w*\s+участ|квартир|жил\w*\s+дом|здани|помещени|гараж|машино[\s-]?мест", re.I)
_OTHER = re.compile(
    r"автомобил|автотранспорт|VIN\b|оборудован|дебитор|прав[оа]?\s+требован|"
    r"аренд|дол[яг]?\s+в\s+устав|ценн\w*\s+бумаг|закупк|услуг[аи]",
    re.I,
)
_PRICE = re.compile(
    r"^\s*(\d[\d \u00a0]*)(?:[,.](\d{1,2}))?\s*(?:руб\.?|₽)?"
    r"(?:\s*\((?:без\s+учета\s+НДС|в\s*т\.?\s*ч\.?\s+НДС\s+\d+%)\))?\s*$",
    re.I,
)


class AlfalotContractError(ValueError):
    """Insufficient or contradictory ALFALOT evidence: never accept."""


@dataclass(frozen=True)
class LotReference:
    entity: str
    detail_url: str
    visible_lot_number: str | None
    row_text: str
    property_candidate: str


@dataclass(frozen=True)
class PageProof:
    page: int
    state_id: str
    state_label: str
    selected: bool
    entity_ids: tuple[str, ...]
    next_page_control: bool
    terminal_control: bool


ALFALOT_BLOCKED_REASON = (
    "ALFALOT active GD filter lacks a verified raw HTTP pagination and "
    "property-only catalogue contract; production import must fail closed"
)


def require_verified_active_http_contract() -> None:
    """Fail-closed integration hook; do not use the legacy SUPCOMING importer."""
    raise AlfalotContractError(ALFALOT_BLOCKED_REASON)


def verify_gd_filter_state(label: str, *, selected: bool) -> None:
    """Check the official accepting-applications filter, not a generic status."""
    if not selected or not re.search(r"при[её]м\w*\s+заяв", label, re.I):
        raise AlfalotContractError("GD accepting-applications state not confirmed")


def terminal_controls_confirmed(clickable: list[str], disabled: list[str]) -> bool:
    """Explicit disabled next/end UI controls are required, but not HTTP proof."""
    active = {value.strip() for value in clickable}
    inactive = {value.strip() for value in disabled}
    if active.intersection({">", ">>", "›", "»", "→"}):
        return False
    return bool(inactive.intersection({">", "›", "»"})) and bool(
        inactive.intersection({">>", "»"})
    )


def lot_entity_from_url(value: str) -> str | None:
    """Use the opaque per-lot entity, never the parent procedure identifier."""
    url = urlsplit(urljoin(SEARCH_URL, value))
    try:
        if (url.scheme != "https" or url.hostname != HOST or url.port is not None
                or url.username or url.password):
            return None
    except ValueError:  # malformed port
        return None
    # First-party pages sometimes carry a ;jsessionid path suffix. Never
    # persist its value in a canonical lot link or audit report.
    if url.path.split(";", 1)[0] != "/app/LotCard/page":
        return None
    query = parse_qs(url.query)
    values = query.get("LotCard.lotEntity", [])
    if len(values) != 1:
        return None
    entity = values[0]  # parse_qs already percent-decodes exactly once
    if not re.fullmatch(r"L[TF]:[a-zA-Z0-9._:-]{5,128}", entity):
        return None
    return entity


def canonical_lot_url(entity: str) -> str:
    if not re.fullmatch(r"L[TF]:[a-zA-Z0-9._:-]{5,128}", entity):
        raise AlfalotContractError("invalid ALFALOT opaque lot entity")
    return f"https://{HOST}/app/LotCard/page?LotCard.lotEntity={quote(entity, safe='')}"


def property_candidate(text: str) -> str:
    """Only a triage label: keywords do NOT prove a property-only scope."""
    property_hit = bool(_PROPERTY.search(text))
    other_hit = bool(_OTHER.search(text))
    if property_hit and other_hit:
        return "mixed_unverified"
    if other_hit:
        return "non_property_candidate"
    return "property_candidate_unverified" if property_hit else "unknown"


def extract_lot_references(html: str) -> list[LotReference]:
    """Extract individual links, rejecting rows that conflate different lots.

    A procedure page can list many independent lot links. Its aggregate row
    must not donate price, title or status to any one of them.
    """
    soup = BeautifulSoup(html, "html.parser")
    results: list[LotReference] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "")
        entity = lot_entity_from_url(href)
        if not entity or entity in seen:
            continue
        seen.add(entity)
        row = anchor.find_parent("tr")
        container = row or anchor.find_parent(["li", "article"]) or anchor
        row_text = container.get_text(" ", strip=True)
        entities = {
            item for link in container.select("a[href]")
            if (item := lot_entity_from_url(str(link.get("href") or "")))
        }
        # Preserve the entity, but do not attribute a multi-lot row's fields.
        isolated = (len(entities) <= 1)
        text = row_text if isolated else anchor.get_text(" ", strip=True)
        number = LOT_NUMBER.search(text)
        results.append(LotReference(
            entity=entity,
            detail_url=canonical_lot_url(entity),
            visible_lot_number=number.group() if number else None,
            row_text=text[:1200],
            property_candidate=property_candidate(text),
        ))
    return results


def parse_exact_price(value: str) -> int | None:
    """A labelled, standalone ruble cell only; never deposits or auction steps."""
    match = _PRICE.fullmatch(value.replace("\u202f", " ").strip())
    if not match:
        return None
    whole = int(match.group(1).replace(" ", "").replace("\u00a0", ""))
    frac = int((match.group(2) or "0").ljust(2, "0"))
    return whole * 100 + frac


def extract_detail_fields(html: str, detail_url: str) -> dict[str, object]:
    """Conservatively read labels on ONE individual LotCard, not OfferCard.

    An absent labelled field is unverified, never inferred from a nearby cell.
    Returns price in integer kopecks. The visible lot number must be checked
    against the corresponding catalogue row by the audit caller.
    """
    entity = lot_entity_from_url(detail_url)
    if not entity:
        raise AlfalotContractError("individual LotCard URL with opaque entity required")
    soup = BeautifulSoup(html, "html.parser")
    fields: dict[str, object] = {
        "entity": entity,
        "visible_lot_number": None,
        "start_price_kopecks": None,
        "status": None,
        "title": None,
        "procedure_number": None,
        "lot_number": None,
        "procedure_type": None,
        "application_start": None,
        "application_end": None,
        "cadastral_number": None,
        "area": None,
        "address": None,
        "property_type": None,
        "current_price_kopecks": None,
        "property_scope_proven": False,
    }
    page_title = soup.select_one("h1, h2")
    heading = page_title.get_text(" ", strip=True) if page_title else ""
    match = LOT_NUMBER.search(heading)
    if match:
        fields["visible_lot_number"] = match.group()
    # Only exact two-cell label:value rows. This prevents price inheritance
    # from an adjacent lot in a multi-lot procedure table.
    for row in soup.select("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) != 2:
            continue
        label = " ".join(cells[0].get_text(" ", strip=True).lower().split())
        value = " ".join(cells[1].get_text(" ", strip=True).split())
        if label in {
            "начальная цена", "начальная цена договора (лота)", "начальная цена лота",
            "начальная (минимальная) цена договора (лота)",
        }:
            parsed_price = parse_exact_price(value)
            previous = fields["start_price_kopecks"]
            if previous is not None and parsed_price != previous:
                raise AlfalotContractError("conflicting initial prices on individual lot detail")
            fields["start_price_kopecks"] = parsed_price
        elif label in {
            "текущая цена лота", "текущая цена",
            "текущая минимальная цена договора (лота)",
        }:
            parsed_current = parse_exact_price(value)
            previous = fields["current_price_kopecks"]
            if previous is not None and parsed_current != previous:
                raise AlfalotContractError("conflicting current prices on individual lot detail")
            fields["current_price_kopecks"] = parsed_current
        elif label in {"номер процедуры"}:
            fields["procedure_number"] = value or None
        elif label in {"номер лота"}:
            fields["lot_number"] = value if value.isdecimal() else None
        elif label in {"предмет договора (лота)", "наименование имущества"}:
            fields["title"] = value or None
        elif label in {"вид торговой процедуры"}:
            fields["procedure_type"] = value or None
        elif label in {"адрес", "местонахождение имущества", "адрес объекта"}:
            fields["address"] = value or None
        elif label in {"тип имущества", "вид имущества", "категория имущества"}:
            fields["property_type"] = value or None
        elif label in {"площадь", "площадь объекта"}:
            fields["area"] = value or None
        elif label in {"состояние лота", "статус лота"}:
            fields["status"] = value if value else None
        elif label in {"начало подачи заявок", "дата начала подачи заявок"}:
            fields["application_start"] = value or None
        elif label in {"окончание подачи заявок", "дата окончания подачи заявок"}:
            fields["application_end"] = value or None
    procedure = fields["procedure_number"]
    lot_number = fields["lot_number"]
    if isinstance(procedure, str) and isinstance(lot_number, str):
        visible = f"{procedure}.lot{lot_number}"
        existing = fields["visible_lot_number"]
        if existing is not None and existing != visible:
            raise AlfalotContractError("conflicting lot number between heading and labelled table")
        fields["visible_lot_number"] = visible
    text = soup.get_text(" ", strip=True)
    cadastres = re.findall(r"(?<!\d)\d{2}:\d{2}:\d{6,7}:\d+(?!\d)", text)
    if len(set(cadastres)) == 1:
        fields["cadastral_number"] = cadastres[0]
    return fields


def verify_walk(pages: list[PageProof]) -> tuple[str, tuple[str, ...]]:
    if not pages:
        raise AlfalotContractError("no catalogue pages")
    all_ids: list[str] = []
    for expected, page in enumerate(pages, 1):
        if page.page != expected:
            raise AlfalotContractError("page numbers are not contiguous from 1")
        if page.state_id != STATE_ELEMENT_ID:
            raise AlfalotContractError("unrecognized GD state element")
        verify_gd_filter_state(page.state_label, selected=page.selected)
        if not page.entity_ids or len(set(page.entity_ids)) != len(page.entity_ids):
            raise AlfalotContractError("missing or duplicated lot entity on page")
        if page.terminal_control == page.next_page_control:
            raise AlfalotContractError("exactly one of next/terminal must be evidenced")
        if page.terminal_control != (expected == len(pages)):
            raise AlfalotContractError("terminal page not reached or reached too early")
        for entity in page.entity_ids:
            if not re.fullmatch(r"L[TF]:[a-zA-Z0-9._:-]{5,128}", entity):
                raise AlfalotContractError("invalid opaque entity ID")
        all_ids.extend(page.entity_ids)
    if len(set(all_ids)) != len(all_ids):
        raise AlfalotContractError("cross-page duplicated entity ID")
    sorted_ids = tuple(sorted(all_ids))
    return hashlib.sha256("\n".join(sorted_ids).encode("utf-8")).hexdigest(), sorted_ids


def verify_two_walks(first: list[PageProof], second: list[PageProof]) -> dict[str, object]:
    first_digest, first_ids = verify_walk(first)
    second_digest, second_ids = verify_walk(second)
    if first_digest != second_digest or first_ids != second_ids:
        raise AlfalotContractError("two complete walks have different entity ID sets")
    return {"pages_per_walk": len(first), "distinct_ids": len(first_ids),
            "id_digest": first_digest, "terminal_verified": True,
            "property_scope_verified": False, "production_enabled": False}
