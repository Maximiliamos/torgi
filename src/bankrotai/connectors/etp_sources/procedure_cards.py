from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from bs4 import Tag

from bankrotai.domain import NormalizedLot


def parse_fabrikant_cards(profile, html: str | bytes, *, page: int) -> list[NormalizedLot]:
    """Parse Next-rendered sale cards by stable lot id, not procedure slug."""
    from bankrotai.connectors.etp_sources.common import (
        SourceAccessError, _CADASTRAL_RE, _category, _clean,
        _parse_soup, _safe_provider_url, _status,
    )

    soup = _parse_soup(html)
    property_words = re.compile(
        r"недвижим|земельн|участок|квартир|комнат|помещен|здани|гараж|кадастров", re.I
    )
    result: list[NormalizedLot] = []
    seen: set[str] = set()
    for card in soup.select("div[data-id]"):
        lot_id = str(card.get("data-id") or "")
        if not lot_id.isdigit() or lot_id in seen:
            continue
        text = _clean(card.get_text(" ", strip=True))
        identity = re.search(r"№\s*(\d{3,12})-(\d{1,5})\b", text)
        if not identity or not property_words.search(text):
            continue
        href = next((urljoin(profile.listing_url, str(a.get("href") or ""))
                     for a in card.select("a[href]") if "/procedure/view/" in str(a.get("href"))), "")
        try:
            _safe_provider_url(profile, href)
        except SourceAccessError:
            continue
        title_node = next((a for a in card.select("a[href]")
                           if "/procedure/view/" in str(a.get("href"))), None)
        title = _clean(title_node.get_text(" ", strip=True)) if title_node else text
        if len(title) < 8:
            continue
        price = None
        for value in card.stripped_strings:
            amount = re.fullmatch(
                r"\s*(\d[\d\s\u00a0]*)(?:[,.](\d{1,2}))?\s*(?:RUB|₽|руб\.?)\s*",
                value, re.I,
            )
            if amount:
                price = float(
                    re.sub(r"[\s\u00a0]", "", amount.group(1))
                    + ("." + amount.group(2) if amount.group(2) else "")
                )
                break
        seen.add(lot_id)
        cadastre = _CADASTRAL_RE.search(title)
        result.append(NormalizedLot(
            external_id=f"{profile.source_id}:{lot_id}", source="fabrikant",
            source_system=profile.source_id, title=title[:500], description=text[:5000],
            category=_category(title), region_slug=None, region_name=None, address=None,
            cadastral_number=cadastre.group(1) if cadastre else None, vin=None, area=None,
            start_price=price, current_price=None, auction_status=_status(text),
            lot_url=href, source_url=href, detail_level="search", raw_data={
                "listing_page": page, "procedure_number": identity.group(1),
                "lot_number": identity.group(2), "provider_lot_id": lot_id,
                "per_lot_detail_verified": False, "complete_source_inventory": False,
                "safe_for_archival": False, "reconcile_missing": False,
            }, platform_name=profile.platform_name, platform_code=profile.platform_code,
        ))
    if result:
        return result

    # Next.js may return the cards only as public React Flight data rather
    # than materialized HTML. Decode the JSON chunk strings, but never execute
    # their JavaScript wrapper.
    flight_parts: list[str] = []
    for script in soup.select("script"):
        payload = script.get_text("", strip=False)
        if "self.__next_f.push(" not in payload:
            continue
        start = payload.find("(") + 1
        end = payload.rfind(")")
        if start <= 0 or end <= start:
            continue
        try:
            decoded = json.loads(payload[start:end])
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, list) and len(decoded) >= 2 and isinstance(decoded[1], str):
            flight_parts.append(decoded[1])

    flight = "\n".join(flight_parts)
    markers = list(re.finditer(r'"data-id":(\d+)', flight))
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(flight)
        card = flight[marker.start():end]
        lot_id = marker.group(1)
        if lot_id in seen or not re.search(rf'"lotId":{re.escape(lot_id)}\b', card):
            continue
        url_match = re.search(
            r'"url":"(https://fabrikant\.ru/v2/trades/procedure/view/[^"?#]+)"', card
        )
        name_match = re.search(
            r'"name":"((?:\\.|[^"\\])*)","lotId":' + re.escape(lot_id), card
        )
        procedure = re.search(r'"procedureNumber":(\d+)', card)
        sequence = re.search(r'"sequenceNumber":(\d+)', card)
        if url_match is None or name_match is None or procedure is None or sequence is None:
            continue
        try:
            title = json.loads('"' + name_match.group(1) + '"')
        except json.JSONDecodeError:
            continue
        if not property_words.search(title):
            continue
        href = url_match.group(1)
        try:
            _safe_provider_url(profile, href)
        except SourceAccessError:
            continue
        price_match = re.search(
            r'"font-heading text-base font-bold","children":\["([\d\s\u00a0]+)"', card
        )
        price = None
        if price_match:
            raw_price = re.sub(r"[\s\u00a0]", "", price_match.group(1))
            if raw_price.isdigit():
                price = float(raw_price)
        seen.add(lot_id)
        cadastre = _CADASTRAL_RE.search(title)
        result.append(NormalizedLot(
            external_id=f"{profile.source_id}:{lot_id}", source="fabrikant",
            source_system=profile.source_id, title=title[:500], description=title[:5000],
            category=_category(title), region_slug=None, region_name=None, address=None,
            cadastral_number=cadastre.group(1) if cadastre else None, vin=None, area=None,
            start_price=price, current_price=None, auction_status=_status(card),
            lot_url=href, source_url=href, detail_level="search", raw_data={
                "listing_page": page, "procedure_number": procedure.group(1),
                "lot_number": sequence.group(1), "provider_lot_id": lot_id,
                "next_flight_evidence": True, "per_lot_detail_verified": False,
                "complete_source_inventory": False, "safe_for_archival": False,
                "reconcile_missing": False,
            }, platform_name=profile.platform_name, platform_code=profile.platform_code,
        ))
    return result


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
    containers: list[Tag] = [
        row for row in soup.select("tr")
        if row.has_attr("onclick") or (row.find("a", href=True) and row.find("tr") is None)
    ]
    containers.extend(
        node for node in soup.select("article, li, div")
        if node.find("a", href=True) and node.find("tr") is None
    )
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
        href, match = trusted[0]
        procedure_identity = "-".join(part for part in match.groups() if part)
        markers = list(marker_re.finditer(text_value))
        if markers:
            segments = [
                (
                    marker.group(1),
                    _clean(text_value[
                        marker.end():markers[index + 1].start()
                        if index + 1 < len(markers) else len(text_value)
                    ]),
                )
                for index, marker in enumerate(markers)
            ]
        else:
            # Legacy table catalogues omit the explicit "Lot No. 1" marker
            # for ordinary one-lot procedures.  The procedure URL plus the
            # implicit first lot is still a stable per-lot identity.
            segments = [("1", text_value)]
        for lot_number, segment in segments:
            if not procedure_identity or len(segment) < 8 or not property_re.search(segment):
                continue
            external_id = f"{profile.source_id}:{procedure_identity}:{lot_number}"
            if external_id in seen:
                continue
            seen.add(external_id)
            cadastre = _CADASTRAL_RE.search(segment)
            start_price = _money(segment)
            # Electro-torgi labels a single-procedure card with
            # ``1 lot for the sum <amount> RUB`` instead of ``initial price``.
            # The amount is attributable only when the provider explicitly
            # states that the procedure has exactly one lot.  Never spread a
            # procedure total over several numbered lots.
            if start_price is None and profile.source_id == "electro-torgi.ru":
                total = re.search(
                    r"\b1\s+лот\s+на\s+сумму\s+(\d[\d\s\u00a0]*)(?:[.,](\d{1,2}))?\s*₽",
                    text_value,
                    re.I,
                )
                if total and len(segments) == 1:
                    start_price = float(
                        re.sub(r"[\s\u00a0]", "", total.group(1))
                        + ("." + total.group(2) if total.group(2) else "")
                    )
            lots.append(NormalizedLot(
                external_id=external_id, source=profile.source_id.removesuffix(".ru"),
                source_system=profile.source_id, title=segment[:500], description=segment[:5000],
                category=_category(segment), region_slug=None, region_name=None, address=None,
                cadastral_number=cadastre.group(1) if cadastre else None, vin=None, area=None,
                start_price=start_price, current_price=_current_money(segment),
                auction_status=_status(text_value), lot_url=href, source_url=href,
                detail_level="search", raw_data={
                    "listing_page": page, "procedure_identity": procedure_identity,
                    "lot_number": lot_number, "trade_level_listing": True,
                    "individual_lot_identity_verified": True,
                    "per_lot_detail_verified": False, "complete_source_inventory": False,
                    "safe_for_archival": False, "reconcile_missing": False,
                }, platform_name=profile.platform_name, platform_code=profile.platform_code,
            ))
    # Empty is fail-closed: generic fallback would collapse a procedure into one fake lot.
    return lots
