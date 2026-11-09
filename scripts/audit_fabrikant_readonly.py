from __future__ import annotations

"""Read-only Fabrikant source-contract probe.

The probe performs public GET requests only. It does not authenticate, submit
forms, write provider data, touch BankrotAI persistence, or follow redirects.
Output is intentionally sanitized: public procedure tokens, counts, route
shapes, labels, and SHA-256 digests only; no HTML dumps/cookies/headers.
"""

import argparse
import hashlib
import json
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag


BASE_URL = (
    "https://www.fabrikant.ru/procedure/search/sales"
    "?section_ids%5B%5D=6&page_limit=10"
)
PROCEDURE_RE = re.compile(r"/v2/trades/procedure/view/([^/?#]+)", re.I)
LOT_RE = re.compile(
    r"/v2/trades/procedure/lot/view/([^/?#]+)/([^/?#]+)(?:[/?#]|$)", re.I
)
PAGE_PARAM = "page_number"
MONEY_RE = re.compile(
    r"(?P<label>начальн\w*\s+цен\w*|текущ\w*\s+цен\w*|"
    r"размер\s+задатк\w*|шаг\s+(?:аукцион\w*|торг\w*))"
    r"[^\d]{0,50}(?P<value>\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)"
    r"\s*(?P<currency>₽|руб(?:\.|лей)?|р\.)?",
    re.I,
)
DATE_LABEL_RE = re.compile(
    r"(?:начал\w*|окончан\w*|дата\s+проведен\w*|прием\w*|приём\w*)"
    r"[^\d]{0,60}\d{2}[./-]\d{2}[./-]\d{4}(?:\s+\d{2}:\d{2})?",
    re.I,
)
STATUS_WORDS = (
    "прием заявок",
    "приём заявок",
    "идет прием заявок",
    "идёт приём заявок",
    "торги объявлены",
    "торги завершены",
    "завершена",
    "завершено",
    "отменена",
    "отменено",
    "приостановлена",
    "приостановлено",
    "публичное предложение",
)
INTERESTING_CONTROL = re.compile(
    r"(?:section|status|state|stage|type|kind|sale|bankrupt|page|limit)", re.I
)


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def safe_route(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path


def page_url(page: int) -> str:
    if page <= 1:
        return BASE_URL
    parsed = urlparse(BASE_URL)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query[PAGE_PARAM] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


@dataclass
class PageObservation:
    page: int
    http_status: int
    response_path: str
    content_sha256: str
    content_bytes: int
    procedure_ids: list[str]
    procedure_count: int
    duplicate_ids_on_page: list[str]
    visible_page_numbers: list[int]
    has_next_link: bool
    next_href_path: str | None
    selected_controls: list[dict[str, str]]
    total_hints: list[str]
    trade_route_shapes: list[str]


@dataclass
class DetailObservation:
    procedure_id: str
    http_status: int
    response_path: str
    content_sha256: str
    content_bytes: int
    lot_markers: list[str]
    lot_marker_count: int
    lot_route_shapes: list[str]
    money_labels: list[dict[str, str]]
    status_labels: list[str]
    date_labels: list[str]
    structured_lot_keys: list[str]
    structured_price_keys: list[str]
    structured_status_keys: list[str]


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "TORGI-Fabrikant-Audit/1.0 "
            "(+https://github.com/Maximiliamos/torgi; public read-only audit)"
        )
    })
    return s


def fetch(s: requests.Session, url: str, timeout: float) -> requests.Response:
    r = s.get(url, timeout=timeout, allow_redirects=False)
    return r


def selected_controls(soup: BeautifulSoup) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for node in soup.select("input[name], select[name]"):
        name = str(node.get("name") or "")
        if not INTERESTING_CONTROL.search(name):
            continue
        if node.name == "input":
            kind = str(node.get("type") or "text").lower()
            if kind in {"checkbox", "radio"} and not node.has_attr("checked"):
                continue
            value = str(node.get("value") or "")
            rows.append({"name": name[:80], "value": value[:120], "kind": kind})
            continue
        selected = node.select_one("option[selected]")
        if isinstance(selected, Tag):
            rows.append({
                "name": name[:80],
                "value": str(selected.get("value") or "")[:120],
                "kind": "select",
            })
    return rows[:30]


def parse_catalogue_page(r: requests.Response, page: int) -> PageObservation:
    soup = BeautifulSoup(r.content, "html.parser")
    ids: list[str] = []
    route_shapes: Counter[str] = Counter()
    visible_pages: set[int] = set()
    next_href_path: str | None = None

    for anchor in soup.select("a[href]"):
        href = urljoin(r.url or BASE_URL, str(anchor.get("href") or ""))
        parsed = urlparse(href)
        m = PROCEDURE_RE.search(parsed.path)
        if m:
            ids.append(m.group(1))
        if "/v2/trades/" in parsed.path:
            route_shapes[parsed.path] += 1
        query = parse_qs(parsed.query)
        for raw in query.get(PAGE_PARAM, []):
            if raw.isdigit():
                visible_pages.add(int(raw))
        text = clean(anchor.get_text(" ", strip=True)).lower()
        if (
            str(page + 1) in query.get(PAGE_PARAM, [])
            or text in {"следующая", "далее", ">", "›", "»"}
        ):
            next_href_path = parsed.path + (("?" + parsed.query) if parsed.query else "")

    counts = Counter(ids)
    unique_ids = list(dict.fromkeys(ids))
    page_text = clean(soup.get_text(" ", strip=True))
    total_hints: list[str] = []
    for pattern in (
        r"(?:найдено|всего|результат\w*)[^\d]{0,30}\d{1,7}",
        r"\d{1,7}[^\d]{0,20}(?:процедур\w*|торг\w*|результат\w*)",
        r"страниц\w*[^\d]{0,20}\d{1,6}",
    ):
        for match in re.finditer(pattern, page_text, re.I):
            value = clean(match.group(0))
            if value not in total_hints:
                total_hints.append(value[:140])
            if len(total_hints) >= 10:
                break

    return PageObservation(
        page=page,
        http_status=r.status_code,
        response_path=safe_route(r.url or BASE_URL),
        content_sha256=hashlib.sha256(r.content).hexdigest(),
        content_bytes=len(r.content),
        procedure_ids=unique_ids,
        procedure_count=len(unique_ids),
        duplicate_ids_on_page=sorted(k for k, v in counts.items() if v > 1),
        visible_page_numbers=sorted(visible_pages),
        has_next_link=(page + 1 in visible_pages) or bool(next_href_path),
        next_href_path=next_href_path,
        selected_controls=selected_controls(soup),
        total_hints=total_hints,
        trade_route_shapes=sorted(route_shapes)[:50],
    )


def _short_matches(pattern: re.Pattern[str], text: str, limit: int = 12) -> list[str]:
    values: list[str] = []
    for m in pattern.finditer(text):
        value = clean(m.group(0))
        if value and value not in values:
            values.append(value[:160])
        if len(values) >= limit:
            break
    return values


def _json_key_census(soup: BeautifulSoup) -> tuple[list[str], list[str], list[str]]:
    lot: set[str] = set()
    price: set[str] = set()
    status: set[str] = set()
    key_re = re.compile(r'["\']([A-Za-z_][A-Za-z0-9_]{1,80})["\']\s*:')
    for script in soup.select("script"):
        raw = script.string or script.get_text(" ", strip=False)
        if not raw:
            continue
        for key in key_re.findall(raw):
            lowered = key.lower()
            if "lot" in lowered:
                lot.add(key)
            if any(token in lowered for token in ("price", "amount", "cost")):
                price.add(key)
            if any(token in lowered for token in ("status", "state", "stage")):
                status.add(key)
    return sorted(lot)[:40], sorted(price)[:40], sorted(status)[:40]


def parse_detail(r: requests.Response, procedure_id: str) -> DetailObservation:
    soup = BeautifulSoup(r.content, "html.parser")
    text = clean(soup.get_text(" ", strip=True))
    lot_markers = []
    for m in re.finditer(r"\bлот\s*(?:№|N)?\s*([A-Za-zА-Яа-я0-9._/-]{1,40})", text, re.I):
        marker = m.group(1).strip(".,;:")
        if marker and marker not in lot_markers:
            lot_markers.append(marker)
        if len(lot_markers) >= 30:
            break

    lot_routes: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = urljoin(r.url, str(anchor.get("href") or ""))
        parsed = urlparse(href)
        if re.search(r"(?:^|/)(?:lot|lots)(?:/|$)", parsed.path, re.I) or re.search(
            r"(?:lot_id|lotId|tradeLot)", parsed.query, re.I
        ):
            lot_routes.add(parsed.path)

    money: list[dict[str, str]] = []
    for m in MONEY_RE.finditer(text):
        row = {
            "label": clean(m.group("label")).lower()[:80],
            "value": clean(m.group("value"))[:40],
            "currency": clean(m.group("currency") or "")[:20],
        }
        if row not in money:
            money.append(row)
        if len(money) >= 20:
            break

    statuses: list[str] = []
    lower = text.lower()
    for status in STATUS_WORDS:
        if status in lower and status not in statuses:
            statuses.append(status)

    lot_keys, price_keys, status_keys = _json_key_census(soup)
    return DetailObservation(
        procedure_id=procedure_id,
        http_status=r.status_code,
        response_path=safe_route(r.url),
        content_sha256=hashlib.sha256(r.content).hexdigest(),
        content_bytes=len(r.content),
        lot_markers=lot_markers,
        lot_marker_count=len(lot_markers),
        lot_route_shapes=sorted(lot_routes)[:40],
        money_labels=money,
        status_labels=statuses[:20],
        date_labels=_short_matches(DATE_LABEL_RE, text),
        structured_lot_keys=lot_keys,
        structured_price_keys=price_keys,
        structured_status_keys=status_keys,
    )


def traverse(
    s: requests.Session,
    *,
    max_pages: int,
    timeout: float,
    pause: float,
) -> dict[str, Any]:
    pages: list[PageObservation] = []
    all_ids: list[str] = []
    seen_fingerprints: dict[str, int] = {}
    terminal_reason = "max_pages"
    terminal_page: int | None = None

    for page in range(1, max_pages + 1):
        r = fetch(s, page_url(page), timeout)
        obs = parse_catalogue_page(r, page)
        pages.append(obs)

        if r.status_code != 200:
            terminal_reason = f"http_{r.status_code}"
            terminal_page = page
            break
        prior = seen_fingerprints.get(obs.content_sha256)
        if prior is not None:
            terminal_reason = f"repeated_page_{prior}"
            terminal_page = page - 1 if page > 1 else 1
            break
        seen_fingerprints[obs.content_sha256] = page

        all_ids.extend(obs.procedure_ids)
        if not obs.procedure_ids:
            terminal_reason = "empty_page"
            terminal_page = page - 1 if page > 1 else 1
            break
        if not obs.has_next_link:
            terminal_reason = "no_next_link"
            terminal_page = page
            break
        time.sleep(pause)

    unique_ids = list(dict.fromkeys(all_ids))
    duplicates_across = sorted(k for k, v in Counter(all_ids).items() if v > 1)
    digest = hashlib.sha256("\n".join(unique_ids).encode("utf-8")).hexdigest()
    return {
        "pages": [asdict(x) for x in pages],
        "pages_checked": len(pages),
        "terminal_page": terminal_page,
        "terminal_reason": terminal_reason,
        "unique_procedure_ids": unique_ids,
        "unique_procedure_count": len(unique_ids),
        "duplicates_across_pages": duplicates_across,
        "id_digest_sha256": digest,
    }


def resolve_individual_lots(
    s: requests.Session,
    procedure_ids: list[str],
    *,
    timeout: float,
    pause: float,
) -> dict[str, Any]:
    lot_urls: list[str] = []
    lot_ids: list[str] = []
    unresolved: list[str] = []
    for procedure_id in procedure_ids:
        procedure_url = (
            "https://www.fabrikant.ru/v2/trades/procedure/view/" + procedure_id
        )
        response = fetch(s, procedure_url, timeout)
        if response.status_code != 200:
            unresolved.append(procedure_id)
            continue
        soup = BeautifulSoup(response.content, "html.parser")
        found = 0
        for anchor in soup.select("a[href]"):
            href = urljoin(response.url or procedure_url, str(anchor.get("href") or ""))
            parsed = urlparse(href)
            match = LOT_RE.search(parsed.path)
            if not match:
                continue
            lot_id = f"{match.group(1)}:{match.group(2)}"
            url = (
                "https://www.fabrikant.ru/v2/trades/procedure/lot/view/"
                f"{match.group(1)}/{match.group(2)}"
            )
            if lot_id not in lot_ids:
                lot_ids.append(lot_id)
                lot_urls.append(url)
            found += 1
        if found == 0:
            unresolved.append(procedure_id)
        time.sleep(pause)
    digest = hashlib.sha256("\n".join(lot_ids).encode("utf-8")).hexdigest()
    return {
        "unique_lot_ids": lot_ids,
        "unique_lot_count": len(lot_ids),
        "lot_urls": lot_urls,
        "unresolved_procedure_ids": unresolved,
        "unresolved_procedure_count": len(unresolved),
        "lot_id_digest_sha256": digest,
    }


def out_of_range_probe(
    s: requests.Session,
    terminal_page: int | None,
    *,
    timeout: float,
) -> dict[str, Any] | None:
    if terminal_page is None:
        return None
    target = terminal_page + 1
    r = fetch(s, page_url(target), timeout)
    obs = parse_catalogue_page(r, target)
    return asdict(obs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=200)
    parser.add_argument("--detail-limit", type=int, default=30)
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--pause", type=float, default=0.4)
    args = parser.parse_args()

    if not (1 <= args.max_pages <= 500):
        raise SystemExit("--max-pages must be 1..500")
    if not (0 <= args.detail_limit <= 100):
        raise SystemExit("--detail-limit must be 0..100")
    if not (1 <= args.passes <= 2):
        raise SystemExit("--passes must be 1..2")

    s = session()
    traversals = [
        traverse(s, max_pages=args.max_pages, timeout=args.timeout, pause=args.pause)
        for _ in range(args.passes)
    ]

    first = traversals[0]
    resolved = resolve_individual_lots(
        s,
        first["unique_procedure_ids"],
        timeout=args.timeout,
        pause=args.pause,
    )
    details: list[DetailObservation] = []
    for lot_id, url in list(zip(
        resolved["unique_lot_ids"], resolved["lot_urls"], strict=True
    ))[: args.detail_limit]:
        r = fetch(s, url, args.timeout)
        details.append(parse_detail(r, lot_id))
        time.sleep(args.pause)

    stable_two_passes = False
    if len(traversals) == 2:
        stable_two_passes = (
            traversals[0]["unique_procedure_ids"] == traversals[1]["unique_procedure_ids"]
            and traversals[0]["id_digest_sha256"] == traversals[1]["id_digest_sha256"]
            and traversals[0]["terminal_page"] == traversals[1]["terminal_page"]
        )

    report = {
        "source": "fabrikant.ru",
        "mode": "public_read_only",
        "database_writes": 0,
        "map_writes": 0,
        "production_enabled": False,
        "base_url_path": urlparse(BASE_URL).path,
        "base_query_keys": sorted(parse_qs(urlparse(BASE_URL).query).keys()),
        "passes": traversals,
        "stable_two_passes": stable_two_passes,
        "out_of_range_after_terminal": out_of_range_probe(
            s, first["terminal_page"], timeout=args.timeout
        ),
        "individual_lots": {
            key: value for key, value in resolved.items() if key != "lot_urls"
        },
        "details_checked": len(details),
        "details": [asdict(x) for x in details],
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
