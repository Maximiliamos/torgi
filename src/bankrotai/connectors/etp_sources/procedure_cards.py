from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import Tag

from bankrotai.domain import NormalizedLot


def parse_sistematorg_procedure_lots(profile, html: str | bytes, *, page: int) -> list[NormalizedLot]:
    """Keep procedure identity separate from each numbered property lot."""
    from bankrotai.connectors.etp_sources.common import (
        SourceAccessError, _CADASTRAL_RE, _category, _clean, _parse_soup,
        _safe_provider_url,
    )

    soup = _parse_soup(html)
    result: list[NormalizedLot] = []
    seen: set[str] = set()
    lot_marker = re.compile(r"лот\s*№\s*(\d{1,5})\s*[:.\-]?\s*", re.I)
    property_words = re.compile(
        r"недвижим|земельн|участок|квартир|комнат|помещен|здани|"
        r"гараж|машино.?мест|жилой\s+дом|склад|сооружен", re.I,
    )
    for row in soup.select("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 3:
            continue
        urls = [urljoin(profile.listing_url, str(a.get("href") or "")) for a in row.select("a[href]")]
        for node in ([row] if row.has_attr("onclick") else []) + row.select("[onclick]"):
            urls.extend(
                urljoin(profile.listing_url, item) for item in
                re.findall(r"['\"]([^'\"]+)['\"]", str(node.get("onclick") or ""))
                if item.startswith(("/", "https://"))
            )
        trusted = []
        for url in urls:
            try:
                _safe_provider_url(profile, url)
            except SourceAccessError:
                continue
            match = profile.lot_url_re.search(url)
            if match:
                trusted.append((url, match))
        if not trusted:
            continue
        detail_url, match = trusted[0]
        trade_id = match.group(1)
        text = _clean(" ".join(cell.get_text(" ", strip=True) for cell in cells[1:]))
        count_match = re.search(r"всего\s+лотов\s*:\s*(\d{1,5})", text, re.I)
        reported_count = int(count_match.group(1)) if count_match else None
        markers = list(lot_marker.finditer(text))
        for index, marker in enumerate(markers):
            end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
            title = _clean(text[marker.end():end])
            external_id = f"{profile.source_id}:{trade_id}:{marker.group(1)}"
            if len(title) < 8 or not property_words.search(title) or external_id in seen:
                continue
            seen.add(external_id)
            cadastre = _CADASTRAL_RE.search(title)
            result.append(NormalizedLot(
                external_id=external_id, source="sistematorg", source_system=profile.source_id,
                title=title[:500], description=title[:5000], category=_category(title),
                region_slug=None, region_name=None, address=None,
                cadastral_number=cadastre.group(1) if cadastre else None, vin=None, area=None,
                start_price=None, current_price=None, auction_status="unknown",
                lot_url=detail_url, source_url=detail_url, detail_level="search",
                raw_data={
                    "listing_page": page, "procedure_id": trade_id,
                    "lot_number": marker.group(1), "trade_level_listing": True,
                    "reported_trade_lots": reported_count,
                    "individual_lot_identity_verified": False,
                    "per_lot_detail_verified": False, "price_unverified": True,
                    "status_unverified": True, "complete_source_inventory": False,
                    "safe_for_archival": False, "reconcile_missing": False,
                }, platform_name=profile.platform_name, platform_code=profile.platform_code,
            ))
    return result


def parse_procedure_card_lots(profile, html: str | bytes, *, page: int) -> list[NormalizedLot]:
    """Split shared procedure cards into independently identified property lots."""
    # Imported at call time by common.py, after that module is initialized.
    from bankrotai.connectors.etp_sources.common import (
        SourceAccessError, _CADASTRAL_RE, _category, _clean, _current_money,
        _money, _parse_soup, _safe_provider_url, _status,
    )

    soup = _parse_soup(html)
    marker_re = re.compile(r"\bлот\s*(?:№\s*)?(\d{1,5})\s*[:.\-]?\s*", re.I)
    property_re = re.compile(
        r"недвижим|земельн|участок|квартир|комнат|помещен|здани|"
        r"гараж|машино.?мест|жилой\s+дом|склад|сооружен|кадастров", re.I,
    )
    lots: list[NormalizedLot] = []
    seen: set[str] = set()
    containers: list[Tag] = list(soup.select("tr"))
    containers.extend(node for node in soup.select("article, li, div") if node.find("a", href=True))
    for container in containers:
        trusted = []
        raw_urls = [str(a.get("href") or "") for a in container.select("a[href]")]
        for node in ([container] if container.has_attr("onclick") else []) + container.select("[onclick]"):
            raw_urls.extend(re.findall(r"['\"]([^'\"]+)['\"]", str(node.get("onclick") or "")))
        for raw_url in raw_urls:
            href = urljoin(profile.listing_url, raw_url)
            try:
                _safe_provider_url(profile, href)
            except SourceAccessError:
                continue
            match = profile.lot_url_re.search(href)
            if match:
                trusted.append((href, match))
        if not trusted:
            continue
        text_value = _clean(container.get_text(" ", strip=True))
        markers = list(marker_re.finditer(text_value))
        if not markers:
            continue
        href, match = trusted[0]
        procedure_identity = "-".join(part for part in match.groups() if part)
        for index, marker in enumerate(markers):
            end = markers[index + 1].start() if index + 1 < len(markers) else len(text_value)
            segment = _clean(text_value[marker.end():end])
            if not procedure_identity or len(segment) < 8 or not property_re.search(segment):
                continue
            external_id = f"{profile.source_id}:{procedure_identity}:{marker.group(1)}"
            if external_id in seen:
                continue
            seen.add(external_id)
            cadastre = _CADASTRAL_RE.search(segment)
            lots.append(NormalizedLot(
                external_id=external_id, source=profile.source_id.removesuffix(".ru"),
                source_system=profile.source_id, title=segment[:500], description=segment[:5000],
                category=_category(segment), region_slug=None, region_name=None, address=None,
                cadastral_number=cadastre.group(1) if cadastre else None, vin=None, area=None,
                start_price=_money(segment), current_price=_current_money(segment),
                auction_status=_status(text_value), lot_url=href, source_url=href,
                detail_level="search", raw_data={
                    "listing_page": page, "procedure_identity": procedure_identity,
                    "lot_number": marker.group(1), "trade_level_listing": True,
                    "individual_lot_identity_verified": True,
                    "per_lot_detail_verified": False, "complete_source_inventory": False,
                    "safe_for_archival": False, "reconcile_missing": False,
                }, platform_name=profile.platform_name, platform_code=profile.platform_code,
            ))
    # Empty is fail-closed: generic fallback would collapse a procedure into one fake lot.
    return lots
