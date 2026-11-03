from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import Tag

from bankrotai.domain import NormalizedLot


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
