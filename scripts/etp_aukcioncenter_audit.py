from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag

BASE = "https://aukcioncenter.ru"
LISTING = BASE + "/etp/trade/list.html"
ACTIVE_PARAMS = {
    "arbitrationManagerTitle": "",
    "debtorTitle": "",
    "number": "",
    "organizerTitle": "",
    "processStatus": "BID_SUBMISSION",
    "subject": "",
}
TRADE_RE = re.compile(r"\b(?P<trade>\d{3,9}-(?:ОАОФ|ОТПП|ЗТПП|ОКОФ|ОКЗФ|ОАЗФ))\b", re.I)
LOT_RE = re.compile(r"Лот\s*№\s*(?P<lot>\d{1,5})\s*[:.\-]?\s*", re.I)
PURCHASE_RE = re.compile(
    r"/(?:etp/)?trade/view/purchase/(?P<route>[A-Za-z0-9_.-]+\.html)(?:\?[^\"'<>\s]*)?",
    re.I,
)
PRICE_LABEL_RE = re.compile(
    r"(?:начальн\w*\s+(?:цен\w*|стоимост\w*)|текущ\w*\s+цен\w*|"
    r"цена\s+на\s+(?:этап|период)|минимальн\w*\s+цен\w*|цена\s+отсеч)",
    re.I,
)
STATUS_WORDS = (
    "идёт приём заявок", "идет прием заявок", "объявлены",
    "торги завершены", "торги отменены",
)
PROPERTY_RE = re.compile(
    r"недвижим|земельн|участок|квартир|помещен|здани|сооружен|"
    r"гараж|машино.?мест|жилой\s+дом|склад|кадастров",
    re.I,
)
NON_PROPERTY_RE = re.compile(
    r"автомоб|транспорт|оборудован|право\s+требован|дебитор|"
    r"дол[яи]\s+в\s+уставн|акци[яи]|ценн\w*\s+бумаг",
    re.I,
)


@dataclass(frozen=True)
class Row:
    trade: str
    provider_id: str | None
    lots: tuple[int, ...]
    status: str | None
    property_lots: tuple[int, ...]
    mixed_property_classes: bool
    route_names: tuple[str, ...]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _get(session: requests.Session, url: str, *, params: dict[str, str] | None = None) -> requests.Response:
    response = session.get(
        url,
        params=params,
        timeout=(8, 35),
        allow_redirects=False,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "TORGI-AukcionCenter-readonly-audit/2026-11-09",
        },
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP_{response.status_code}")
    if len(response.content) > 4_000_000:
        raise RuntimeError("response_too_large")
    # The legacy site can send an incorrect encoding header/meta.
    response.encoding = response.apparent_encoding or "windows-1251"
    return response


def _row_urls(row: Tag) -> list[str]:
    values: list[str] = []
    for node in row.select("a[href]"):
        values.append(urljoin(BASE, str(node.get("href") or "")))
    for node in [row, *row.select("[onclick]")]:
        onclick = str(node.get("onclick") or "")
        for candidate in re.findall(r"['\"]([^'\"]+)['\"]", onclick):
            if candidate.startswith(("/", "https://")):
                values.append(urljoin(BASE, candidate))
    return values


def _provider_id(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.hostname != "aukcioncenter.ru":
        return None
    if not re.fullmatch(r"/(?:etp/)?trade/view/purchase/[A-Za-z0-9_.-]+\.html", parsed.path, re.I):
        return None
    ids = parse_qs(parsed.query).get("id", [])
    return ids[0] if len(ids) == 1 and re.fullmatch(r"[1-9]\d{5,12}", ids[0]) else None


def parse_rows(html: str) -> list[Row]:
    soup = BeautifulSoup(html, "html.parser")
    output: list[Row] = []
    for tr in soup.select("tr"):
        text = _clean(tr.get_text(" ", strip=True))
        trade_match = TRADE_RE.search(text)
        if not trade_match:
            continue
        urls = _row_urls(tr)
        ids = {pid for url in urls if (pid := _provider_id(url))}
        provider_id = next(iter(ids)) if len(ids) == 1 else None
        pieces = list(LOT_RE.finditer(text))
        lot_numbers = tuple(int(match.group("lot")) for match in pieces)
        if not lot_numbers:
            url_lots: set[int] = set()
            for value in urls:
                query = parse_qs(urlparse(value).query)
                for raw_lot in query.get("lotNumber", []):
                    if re.fullmatch(r"[1-9]\\d{0,4}", raw_lot):
                        url_lots.add(int(raw_lot))
            if len(url_lots) == 1:
                lot_numbers = (next(iter(url_lots)),)
        property_lots: list[int] = []
        classes: set[str] = set()
        if pieces:
            for index, match in enumerate(pieces):
                end = pieces[index + 1].start() if index + 1 < len(pieces) else len(text)
                segment = text[match.end():end]
                if PROPERTY_RE.search(segment):
                    property_lots.append(int(match.group("lot")))
                    classes.add("property")
                if NON_PROPERTY_RE.search(segment):
                    classes.add("non_property")
        else:
            if PROPERTY_RE.search(text):
                classes.add("property")
            if NON_PROPERTY_RE.search(text):
                classes.add("non_property")
        status = next((marker for marker in STATUS_WORDS if marker in text.lower()), None)
        route_names = tuple(sorted({
            match.group("route").lower()
            for value in urls
            for match in [PURCHASE_RE.search(urlparse(value).path + ("?" + urlparse(value).query if urlparse(value).query else ""))]
            if match
        }))
        output.append(Row(
            trade=trade_match.group("trade").upper(),
            provider_id=provider_id,
            lots=lot_numbers,
            status=status,
            property_lots=tuple(property_lots),
            mixed_property_classes=len(classes) > 1,
            route_names=route_names,
        ))
    return output


def _page_params(page: int) -> dict[str, str]:
    result = dict(ACTIVE_PARAMS)
    result["page"] = str(page)
    return result


def _declared_pages(html: str) -> list[int]:
    soup = BeautifulSoup(html, "html.parser")
    pages: set[int] = set()
    for anchor in soup.select("a[href]"):
        query = parse_qs(urlparse(urljoin(LISTING, str(anchor.get("href") or ""))).query)
        for value in query.get("page", []):
            if value.isdigit():
                pages.add(int(value))
    return sorted(pages)


def _digest(rows: list[Row]) -> str:
    stable = [
        {
            "trade": row.trade,
            "provider_id": row.provider_id,
            "lots": row.lots,
            "property_lots": row.property_lots,
            "status": row.status,
        }
        for row in rows
    ]
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _purchase_routes(html: str) -> list[dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[tuple[str, tuple[str, ...]], dict[str, object]] = {}
    candidates = [str(node.get("href") or "") for node in soup.select("a[href]")]
    candidates += re.findall(r"['\"]([^'\"]*/trade/view/purchase/[^'\"]+)['\"]", html)
    for raw in candidates:
        url = urljoin(BASE, raw.replace("&amp;", "&"))
        parsed = urlparse(url)
        if parsed.hostname != "aukcioncenter.ru":
            continue
        match = re.fullmatch(r"/(?:etp/)?trade/view/purchase/([A-Za-z0-9_.-]+\.html)", parsed.path, re.I)
        if not match:
            continue
        keys = tuple(sorted(parse_qs(parsed.query).keys()))
        found[(match.group(1).lower(), keys)] = {
            "route": match.group(1).lower(),
            "query_keys": list(keys),
        }
    return sorted(found.values(), key=lambda item: (str(item["route"]), item["query_keys"]))


def _text_features(html: str) -> dict[str, object]:
    soup = BeautifulSoup(html, "html.parser")
    text = _clean(soup.get_text(" ", strip=True))
    status_hits = [marker for marker in STATUS_WORDS if marker in text.lower()]
    price_labels = sorted({_clean(m.group(0)).lower() for m in PRICE_LABEL_RE.finditer(text)})
    lots = sorted({int(x) for x in re.findall(r"Лот\s*№\s*(\d{1,5})", text, re.I)})
    cads = sorted(set(re.findall(r"(?<!\d)\d{2}:\d{1,3}(?::\d{1,9}){2,4}(?!\d)", text)))
    selected_lot_values = sorted({
        str(node.get("value") or "")
        for node in soup.select(
            "select[name*=lot i] option[selected], input[name*=lot i][checked]"
        )
        if str(node.get("value") or "").isdigit()
    })
    semantic_labels = sorted({
        _clean(node.get_text(" ", strip=True)).lower()
        for node in soup.select("th, dt, label")
        if re.search(r"цен|лот|статус|состояни|заяв|аукцион|торг", node.get_text(" ", strip=True), re.I)
    })
    return {
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text_len": len(text),
        "lot_numbers_in_text": lots[:50],
        "selected_lot_values": selected_lot_values[:20],
        "price_labels": price_labels[:20],
        "semantic_labels": semantic_labels[:30],
        "status_hits": status_hits,
        "cadastral_count": len(cads),
        "purchase_routes": _purchase_routes(html),
    }


def _url_for_lot(base_url: str, lot: int) -> str:
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["lotNumber"] = [str(lot)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def run(*, pause: float, details: int) -> dict[str, object]:
    if pause < 0.2 or not 1 <= details <= 50:
        raise ValueError("unsafe audit limits")
    session = requests.Session()
    passes: list[dict[str, object]] = []
    first_pass_rows: list[Row] = []
    page_count = 0

    for pass_no in (1, 2):
        response = _get(session, LISTING, params=_page_params(1))
        page_numbers = _declared_pages(response.text)
        maximum = max(page_numbers or [1])
        all_rows: list[Row] = []
        pages: list[dict[str, object]] = []
        for page in range(1, maximum + 1):
            if page > 1:
                time.sleep(pause)
                response = _get(session, LISTING, params=_page_params(page))
            rows = parse_rows(response.text)
            pages.append({
                "page": page,
                "rows": len(rows),
                "digest": _digest(rows),
                "declared_pages": _declared_pages(response.text),
            })
            all_rows.extend(rows)
        ids = [
            f"{row.provider_id or 'NO_PROVIDER'}:{lot}"
            for row in all_rows
            for lot in (row.lots or (1,))
        ]
        duplicate_ids = sorted([value for value, count in Counter(ids).items() if count > 1])
        passes.append({
            "pass": pass_no,
            "pages": pages,
            "declared_last_page": maximum,
            "procedure_rows": len(all_rows),
            "individual_identity_candidates": len(ids),
            "unique_identity_candidates": len(set(ids)),
            "duplicate_identity_candidates": duplicate_ids[:20],
            "digest": _digest(all_rows),
        })
        if pass_no == 1:
            first_pass_rows = all_rows
            page_count = maximum
        time.sleep(pause)

    # Terminal proof: the declared last page must not advertise a higher page,
    # and page last+1 must not contain a new active procedure identity.
    terminal_response = _get(session, LISTING, params=_page_params(page_count + 1))
    terminal_rows = parse_rows(terminal_response.text)
    known_trades = {row.trade for row in first_pass_rows}
    new_after_terminal = sorted({row.trade for row in terminal_rows} - known_trades)

    # Select 30 distinct lot identities, prioritising property lots, then fill
    # with other classes. The general procedure page is fetched once per
    # provider id; lotNumber variants are fetched independently.
    property_candidates: list[tuple[Row, int]] = []
    other_candidates: list[tuple[Row, int]] = []
    candidate_seen: set[tuple[str, int]] = set()
    for row in first_pass_rows:
        if not row.provider_id:
            continue
        for lot in row.lots:
            key = (row.provider_id, lot)
            if key in candidate_seen:
                continue
            candidate_seen.add(key)
            target = property_candidates if lot in row.property_lots else other_candidates
            target.append((row, lot))
    selected = (property_candidates + other_candidates)[:details]

    detail_results: list[dict[str, object]] = []
    route_shapes: dict[str, set[tuple[str, ...]]] = {}
    isolated_payloads = 0
    base_cache: dict[str, dict[str, object]] = {}
    for index, (row, lot) in enumerate(selected):
        if index:
            time.sleep(pause)
        assert row.provider_id is not None
        base_url = f"{BASE}/trade/view/purchase/general.html?id={row.provider_id}"
        base_features = base_cache.get(row.provider_id)
        if base_features is None:
            base = _get(session, base_url)
            base_features = _text_features(base.text)
            base_cache[row.provider_id] = base_features
            for route in base_features["purchase_routes"]:
                route_shapes.setdefault(str(route["route"]), set()).add(tuple(route["query_keys"]))

        lot_response = _get(session, _url_for_lot(base_url, lot))
        lot_features = _text_features(lot_response.text)
        if lot_features["text_sha256"] != base_features["text_sha256"]:
            isolated_payloads += 1
        for route in lot_features["purchase_routes"]:
            route_shapes.setdefault(str(route["route"]), set()).add(tuple(route["query_keys"]))
        detail_results.append({
            "provider_id": row.provider_id,
            "trade": row.trade,
            "lot_number": lot,
            "property_hint": lot in row.property_lots,
            "base_text_sha256": base_features["text_sha256"],
            "lot_variant": lot_features,
        })

    return {
        "mode": "readonly_aukcioncenter_source_contract",
        "listing_url": LISTING,
        "active_filter": ACTIVE_PARAMS,
        "production_database_writes": 0,
        "archival_allowed": False,
        "passes": passes,
        "two_pass_digest_equal": passes[0]["digest"] == passes[1]["digest"],
        "terminal_probe": {
            "requested_page": page_count + 1,
            "rows": len(terminal_rows),
            "declared_pages": _declared_pages(terminal_response.text),
            "new_trade_ids_after_declared_last": new_after_terminal,
            # A clamp to page 1/last page is not terminal proof. The out-of-range
            # page must contain no catalogue procedure rows.
            "proven": len(terminal_rows) == 0,
        },
        "mixed_property_procedure_rows": sum(row.mixed_property_classes for row in first_pass_rows),
        "rows_without_provider_id": sum(row.provider_id is None for row in first_pass_rows),
        "individual_detail_variants_checked": len(detail_results),
        "property_detail_variants_checked": sum(bool(item["property_hint"]) for item in detail_results),
        "detail_variants_with_price_labels": sum(bool(item["lot_variant"]["price_labels"]) for item in detail_results),
        "detail_variants_with_status_labels": sum(bool(item["lot_variant"]["status_hits"]) for item in detail_results),
        "lot_number_variants_with_changed_payload": isolated_payloads,
        "purchase_route_shapes": {
            route: [list(keys) for keys in sorted(shapes)]
            for route, shapes in sorted(route_shapes.items())
        },
        "details": detail_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pause", type=float, default=0.35)
    parser.add_argument("--details", type=int, default=30)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = run(pause=args.pause, details=args.details)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)


if __name__ == "__main__":
    main()
