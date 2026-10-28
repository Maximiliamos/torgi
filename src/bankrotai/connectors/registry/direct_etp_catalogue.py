"""Read-only catalogue discovery across the user-selected 20 ETPs plus two extras.

A listing candidate is *not* a validated auction lot. Full source completeness,
status reconciliation and ingestion are deliberately disabled until per-site
contracts and exact detail records can be checked together on Home Windows.

Existing direct sources M-ETS and Lot-online are represented in this matrix but
are not scraped twice by this module. No third-party aggregators are queried.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup


PROPERTY_WORDS = re.compile(
    r"(?:недвижим|земельн|участок|квартир|комнат|помещени|здани|"
    r"гараж|машино.?мест|жилой дом|склад|торговый центр|сооружени|"
    r"доля в праве на|апартамент)",
    re.IGNORECASE,
)
CADASTRE = re.compile(r"(?<!\d)\d{2}:\d{1,3}:\d{5,9}:\d{1,9}(?!\d)")
TRADE_ID = re.compile(r"\b\d{3,9}-(?:ОАОФ|ОТПП|ЗТПП|ОКОФ|ОКЗФ)\b", re.IGNORECASE)
LOT_SECTIONS = re.compile(r"Лот\s*№\s*(\d{1,5})\s*[:.\-]\s*", re.IGNORECASE)
DECIMAL = re.compile(r"^\s*(\d[\d \u00a0\u202f]*)(?:,(\d{1,2}))?\s*$")
MARKERS = frozenset({"published", "active", "closed", "unknown"})
MAX_RESPONSE_BYTES = 2_000_000


@dataclass(frozen=True, slots=True)
class EtpProfile:
    source_id: str
    name: str
    catalogue_url: str
    strategy: str
    note: str = ""


# Precise starting URLs are provider-owned. Some root pages do not expose a
# catalogue; these profiles have explicit unsupported strategy (not fake data).
PROFILES: tuple[EtpProfile, ...] = (
    EtpProfile("m-ets.ru", "МЭТС", "https://meetings.m-ets.ru/", "existing",
               "Direct detail parser PR #964; full catalogue still pending"),
    EtpProfile("lot-online.ru", "РАД / Lot-online", "https://lot-online.ru/", "existing",
               "Already in nationwide ingestion; do not double-count"),
    EtpProfile("cdtrf.ru", "Центр дистанционных торгов", "https://bankrot.cdtrf.ru/", "links",
               "Public catalogue; may require JS to expose exact links"),
    EtpProfile("nistp.ru", "Новые информационные сервисы", "https://nistp.ru/bankrot/trade_list.php", "trade_rows",
               "Public trade table containing embedded numbered lots"),
    EtpProfile("alfalot.ru", "Альфалот",
               "https://etp.alfalot.ru/app/LotList/page?LotList.pageNum.lots=0&lotFilterState.activeFilter=SUPCOMING&lotFilterType.activeFilter=SSALE",
               "alfalot_lots", "Public upcoming-sale lots; current/paused require separate pages"),
    EtpProfile("rts-tender.ru", "РТС-тендер (имущественные торги)",
               "https://www.rts-tender.ru/property-sales", "research",
               "Confirmed official property-sales section; determine public lot API"),
    EtpProfile("roseltorg.ru", "Росэлторг / 178-ФЗ",
               "https://178fz.roseltorg.ru/", "research",
               "Confirmed public privatization section; find accessible sale listings"),
    EtpProfile("sberbank-ast.ru", "Сбербанк-АСТ — имущественные торги", "https://utp.sberbank-ast.ru/Bankruptcy", "research",
               "Select property-sale section, not state procurement"),
    EtpProfile("fabrikant.ru", "Фабрикант",
               "https://www.fabrikant.ru/procedure/search/sales?section_ids%5B%5D=6",
               "links", "Public bankruptcy-sale search; candidate extraction remains conservative"),
    EtpProfile("arbbitlot.ru", "АРБбитЛот", "https://torgi.arbbitlot.ru/", "links",
               "Public auctions/view pages; listing routes require confirmation"),
    EtpProfile("etpu.ru", "Уральская ЭТП", "https://bankrupt.etpu.ru/public/purchases-all/", "table_lots",
               "Public per-lot table"),
    EtpProfile("sistematorg.com", "Система Торг", "https://sistematorg.com/tradelist.php", "trade_rows",
               "Public trade table containing embedded numbered lots"),
    EtpProfile("centerr.ru", "Центр реализации", "https://bankrupt.centerr.ru/public/purchases-all/", "table_lots",
               "Public per-lot table"),
    EtpProfile("aukcioncenter.ru", "Аукцион-центр", "https://aukcioncenter.ru/etp/trade/list.html?show=current", "trade_rows",
               "Public trade table; confirm detail URLs"),
    EtpProfile("etp-profit.ru", "Профит", "https://etp-profit.ru/", "research",
               "Two concurrent platform versions after August 2026"),
    EtpProfile("ausib.ru", "Аукционы Сибири", "https://ausib.ru/", "research",
               "Public catalogue route not yet established"),
    EtpProfile("vertrades.ru", "Вердиктъ", "https://vertrades.ru/", "research",
               "Public catalogue route not yet established"),
    EtpProfile("utender.ru", "uTender", "https://utender.ru/", "research",
               "Public catalogue route not yet established"),
    EtpProfile("rus-on.ru", "RUSSIA OnLine", "https://rus-on.ru/bankrot/trade_list.php", "trade_rows",
               "Public bankruptcy trade register"),
    EtpProfile("electro-torgi.ru", "Electro-torgi", "https://bankrotstvo.electro-torgi.ru/", "research",
               "Confirm current public trade list route"),
    EtpProfile("zakazrf.ru", "ЗаказРФ (банкротство)", "https://bankrot.zakazrf.ru/public/purchases-all/", "table_lots",
               "Public per-lot table"),
    EtpProfile("utpl.ru", "Объединённая торговая площадка", "https://bankrupt.utpl.ru/public/purchases-all/", "table_lots",
               "Public per-lot table"),
)
PROFILE_BY_ID = {p.source_id: p for p in PROFILES}


class EtpCatalogError(ValueError):
    """Provider page does not prove a safe source observation."""


def safe_url(profile: EtpProfile, href: str) -> str:
    """Resolve on the provider's *exact* host, disallow host/path bypasses."""
    base = urlsplit(profile.catalogue_url)
    absolute = urljoin(profile.catalogue_url, href.strip())
    parsed = urlsplit(absolute)
    if (parsed.scheme != "https" or parsed.hostname != base.hostname
            or parsed.port is not None or parsed.username or parsed.password
            or parsed.fragment or not parsed.path.startswith("/")):
        raise EtpCatalogError("External or non-HTTPS source link is not allowed")
    return absolute


def _clean(text: str, max_len: int = 700) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:max_len]


def _money(text: str) -> float | None:
    match = DECIMAL.fullmatch(text.strip())
    if not match:
        return None
    value = float(re.sub(r"[\s\u00a0\u202f]", "", match.group(1))
                  + ("." + match.group(2) if match.group(2) else ""))
    return value if value > 0 else None


def _candidate(profile: EtpProfile, *, trade: str, lot: str, title: str,
               url: str, price: float | None, status: str) -> dict:
    if not trade or not lot or not PROPERTY_WORDS.search(title):
        raise EtpCatalogError("Missing trade/lot identity or not property")
    canonical = safe_url(profile, url)
    match = CADASTRE.search(title)
    return {
        "source": profile.source_id,
        "external_id": f"{trade}:{lot}",
        "trade": trade,
        "lot": lot,
        "title": _clean(title),
        "catalogue_detail_link": canonical,
        "cadastral_number": match.group(0) if match else None,
        "listing_price_rub": price,
        "source_status_text": _clean(status, 80),
        "evidence": "public_catalogue_only",
        "verified_detail": False,
        "ready_for_ingestion": False,
    }


def _first_provider_link(profile: EtpProfile, cells: list) -> str:
    for cell in cells:
        for anchor in cell.select("a[href]"):
            try:
                return safe_url(profile, anchor["href"])
            except (EtpCatalogError, ValueError):
                continue
    raise EtpCatalogError("No first-party detail link in source row")


def _parse_lot_tables(profile: EtpProfile, soup: BeautifulSoup, limit: int) -> list[dict]:
    items = []
    for tr in soup.select("tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 9:
            continue
        trade = _clean(cells[0].get_text(" ", strip=True))
        lot = _clean(cells[2].get_text(" ", strip=True))
        title = _clean(cells[3].get_text(" ", strip=True))
        if not re.fullmatch(r"\d{4,12}", trade) or not re.fullmatch(r"\d{1,5}", lot):
            continue
        try:
            link = _first_provider_link(profile, cells[:4])
            items.append(_candidate(
                profile, trade=trade, lot=lot, title=title, url=link,
                price=_money(cells[4].get_text(" ", strip=True)),
                status=cells[8].get_text(" ", strip=True),
            ))
        except EtpCatalogError:
            continue
        if len(items) >= limit:
            break
    return items


def _parse_trade_tables(profile: EtpProfile, soup: BeautifulSoup, limit: int) -> list[dict]:
    items = []
    for tr in soup.select("tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 3:
            continue
        text = _clean(tr.get_text(" ", strip=True), 12_000)
        match = TRADE_ID.search(text)
        if not match:
            continue
        pieces = list(LOT_SECTIONS.finditer(text))
        if not pieces:
            continue
        try:
            link = _first_provider_link(profile, cells)
        except EtpCatalogError:
            continue
        for index, part in enumerate(pieces):
            end = pieces[index + 1].start() if index + 1 < len(pieces) else len(text)
            title = text[part.end():end]
            try:
                items.append(_candidate(
                    profile, trade=match.group(0), lot=part.group(1), title=title,
                    url=link, price=None, status="unknown",
                ))
            except EtpCatalogError:
                continue
            if len(items) >= limit:
                return items
    return items


def _parse_provider_links(profile: EtpProfile, soup: BeautifulSoup, limit: int) -> list[dict]:
    """Conservative candidates only when an explicit trade/lot ID is visible."""
    items = []
    for anchor in soup.select("a[href]"):
        text = _clean(anchor.get_text(" ", strip=True))
        match_trade = TRADE_ID.search(text)
        match_lot = LOT_SECTIONS.search(text)
        if not match_trade or not match_lot:
            continue
        try:
            items.append(_candidate(
                profile, trade=match_trade.group(0), lot=match_lot.group(1),
                title=text[match_lot.end():], url=anchor["href"],
                price=None, status="unknown",
            ))
        except EtpCatalogError:
            continue
        if len(items) >= limit:
            break
    return items



ALFALOT_ID = re.compile(r"(?P<trade>[0-9A-Z/]{7,30})\.lot(?P<lot>\d{1,5})(?:\.lot\d{1,5})?", re.IGNORECASE)


def _parse_alfalot_lots(profile: EtpProfile, soup: BeautifulSoup, limit: int) -> list[dict]:
    """Parse official upcoming-sales listing; no assumption about full coverage."""
    items = []
    for tr in soup.select("tr"):
        description = _clean(tr.get_text(" ", strip=True), 4500)
        identity = ALFALOT_ID.search(description)
        if not identity or not PROPERTY_WORDS.search(description):
            continue
        card_url = None
        for link in tr.select("a[href]"):
            href = str(link["href"])
            if "/LotCard/page" not in href or "LotCard.lotEntity=" not in href:
                continue
            try:
                card_url = safe_url(profile, href)
            except EtpCatalogError:
                continue
            break
        if not card_url:
            continue
        try:
            items.append(_candidate(
                profile, trade=identity.group("trade"), lot=identity.group("lot"),
                title=description[identity.end():], url=card_url,
                price=None, status="unknown",
            ))
        except EtpCatalogError:
            continue
        if len(items) >= limit:
            break
    return items


def parse_catalogue(profile: EtpProfile, html: str, *, limit: int = 30) -> list[dict]:
    if profile.strategy not in {"trade_rows", "table_lots", "links", "alfalot_lots"}:
        raise EtpCatalogError("No verified HTML extractor for this source")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    soup = BeautifulSoup(html, "html.parser")
    if profile.strategy == "table_lots":
        rows = _parse_lot_tables(profile, soup, limit)
    elif profile.strategy == "alfalot_lots":
        rows = _parse_alfalot_lots(profile, soup, limit)
    elif profile.strategy == "trade_rows":
        rows = _parse_trade_tables(profile, soup, limit)
    else:
        rows = _parse_provider_links(profile, soup, limit)
    unique: dict[str, dict] = {}
    for item in rows:
        unique.setdefault(item["external_id"], item)
    return list(unique.values())[:limit]


def fetch_catalogue(profile: EtpProfile, *, session: requests.Session,
                    timeout: tuple[int, int] = (5, 20)) -> str:
    """Exactly one provider-owned HTTPS page, no redirects and no auth."""
    safe_url(profile, profile.catalogue_url)
    response = session.get(
        profile.catalogue_url, timeout=timeout,
        headers={"User-Agent": "TORGI-direct-etp-catalogue-pilot/0.1",
                 "Accept": "text/html"},
        allow_redirects=False,
    )
    if response.status_code in (401, 403, 429):
        raise EtpCatalogError(f"HTTP {response.status_code}: access/rate limit; stop")
    if response.status_code in (301, 302, 303, 307, 308):
        raise EtpCatalogError("Site redirect requires approved canonical URL")
    response.raise_for_status()
    if "text/html" not in response.headers.get("Content-Type", "").lower():
        raise EtpCatalogError("Expected a public HTML catalogue")
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise EtpCatalogError("Public response exceeded 2 MB")
    return response.text


def batch_probe(*, session: requests.Session, ids: tuple[str, ...] | None = None,
                limit: int = 30, delay_seconds: float = 1.0) -> dict:
    """One GET per candidate site, one combined review matrix; no raw HTML stored."""
    if not 1 <= limit <= 100 or delay_seconds < 0.5:
        raise ValueError("Unsafe batch probe limits")
    requested = ids or tuple(PROFILE_BY_ID)
    if len(set(requested)) != len(requested) or any(x not in PROFILE_BY_ID for x in requested):
        raise ValueError("Unknown or duplicate source ID")
    results = []
    for index, sid in enumerate(requested):
        p = PROFILE_BY_ID[sid]
        if index:
            time.sleep(delay_seconds)
        if p.strategy == "existing":
            results.append({
                "source": sid, "status": "existing_connector",
                "note": p.note, "candidates": [],
            })
            continue
        if p.strategy == "research":
            results.append({
                "source": sid, "status": "needs_public_catalogue_contract",
                "catalogue_url": p.catalogue_url,
                "note": p.note, "candidates": [],
            })
            continue
        try:
            html = fetch_catalogue(p, session=session)
            candidates = parse_catalogue(p, html, limit=limit)
            results.append({
                "source": sid, "status": "candidates_unverified" if candidates else "no_matches_or_changed_html",
                "catalogue_url": p.catalogue_url,
                "note": p.note, "candidates": candidates,
            })
        except (requests.RequestException, EtpCatalogError, ValueError) as exc:
            results.append({
                "source": sid, "status": "blocked_or_parser_error",
                "catalogue_url": p.catalogue_url,
                "error_type": type(exc).__name__, "candidates": [],
            })
    return {
        "mode": "readonly_multi_etp_catalogue_pilot",
        "expected_source_count": len(PROFILES),
        "queried_source_count": len(requested),
        "source_completeness_proven": False,
        "can_reconcile_missing_or_archive": False,
        "can_publish_to_map": False,
        "database_mutations": 0,
        "sources": results,
    }
