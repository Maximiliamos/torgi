#!/usr/bin/env python3
"""TORGI direct-ETP browser evidence collector v6.

Read-only, sequential, bounded. Uses official public catalogues in the TORGI
ETP profile registry. Never bypasses bot challenges, CAPTCHA, login, TLS or 429.
Raw captures may contain personal information: keep ZIP private; share a
redacted summary until contents have been reviewed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

try:
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
except ImportError as exc:
    raise SystemExit("Install playwright: python -m pip install playwright && python -m playwright install chromium") from exc

from bankrotai.connectors.registry.direct_etp_catalogue import PROFILES

DETAIL_PATTERNS = {
    "m-ets.ru": r"^/\d{4,9}-\d{1,5}/?$",
    "cdtrf.ru": r"^/trades/\d{3,12}/?$",
    "rts-tender.ru": r"^/trades/lotCard/[0-9a-f-]{30,40}/?$",
    "nistp.ru": r"^/bankrot/(?:trade_view|lot_view)\.php$",
    "alfalot.ru": r"^/app/LotCard/page$",
    "arbbitlot.ru": r"^/public/(?:auctions|public-offers)/lots/view/\d+/?$",
    "etpu.ru": r"^/public/(?:auctions|public-offers)/lots/view/\d+/?$",
    "centerr.ru": r"^/public/(?:auctions|public-offers)/lots/view/\d+/?$",
    "zakazrf.ru": r"^/public/(?:auctions|public-offers)/lots/view/\d+/?$",
    "utpl.ru": r"^/public/(?:auctions|public-offers)/lots/view/\d+/?$",
    "sistematorg.com": r"^/trade_view\.php$",
    "rus-on.ru": r"^/bankrot/lot_view\.php$",
    "aukcioncenter.ru": r"^/trade/view/purchase/general\.html$",
    "vertrades.ru": r"^/showTrades$",
    "fabrikant.ru": r"^/v2/trades/procedure/view/[^/]+/?$",
    "sberbank-ast.ru": r"^/Catalog/NBT/GoodsView/\d+/0/0/\d+/?$",
    "roseltorg.ru": r"^/procedure/\d+/\d+/?$",
    "lot-online.ru": r"^/index\.php$",
}
CHALLENGE = re.compile("проверяем ваш браузер|anti.ddos|checking your browser|ddos-guard|captcha", re.I)
SENSITIVE_URL_KEY = re.compile("token|auth|session|cookie|secret|key|password|email|phone", re.I)
NOISE = re.compile("/news|/login|/auth|/account|/register|/support|/help|/company", re.I)
MAX_HTML = 3 * 1024 * 1024
MAX_API = 2 * 1024 * 1024


def public_url(url: str, host: str) -> bool:
    p = urlsplit(url)
    return p.scheme == "https" and p.hostname == host and not p.username and not p.password and p.port is None


def safe_url_label(url: str) -> str:
    """Never persist potentially confidential query values to diagnostics."""
    p = urlsplit(url)
    names = sorted(set(k.split("=")[0] for k in p.query.split("&") if k))
    names = [n for n in names if not SENSITIVE_URL_KEY.search(n)]
    return f"{p.scheme}://{p.hostname}{p.path}" + (f"?query_keys={','.join(names)}" if names else "")


def detail_urls(page, source: str, host: str) -> list[str]:
    pattern = DETAIL_PATTERNS.get(source)
    if not pattern:
        return []
    links = page.locator("a[href]").evaluate_all(
        "(els) => els.map(e => e.href).filter(Boolean).slice(0, 5000)"
    )
    selected = []
    for link in links:
        p = urlsplit(link)
        if (not public_url(link, host) or NOISE.search(p.path)
                or not re.fullmatch(pattern, p.path, flags=re.I)):
            continue
        if source == "lot-online.ru" and "dispatch=products.view" not in p.query:
            continue
        if source == "alfalot.ru" and "LotCard.lotEntity=" not in p.query:
            continue
        if source in {"sistematorg.com", "rus-on.ru", "aukcioncenter.ru",
                      "vertrades.ru", "nistp.ru"} and not p.query:
            continue
        if link not in selected:
            selected.append(link)
        if len(selected) >= 300:
            break
    return selected


def record_page(page, folder: Path, label: str, events: list, host: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(folder / (label + ".png")), full_page=False, timeout=15000)
    except Exception as exc:
        events.append({"event": "screenshot_failed", "stage": label, "error": type(exc).__name__})
    try:
        if public_url(page.url, host):
            html = page.content().encode("utf-8", "replace")
            if len(html) <= MAX_HTML:
                (folder / (label + ".html")).write_bytes(html)
            else:
                events.append({"event": "oversized_html", "stage": label, "bytes": len(html)})
    except Exception as exc:
        events.append({"event": "html_failed", "stage": label, "error": type(exc).__name__})


def challenge_present(page) -> bool:
    try:
        return bool(CHALLENGE.search(page.title() + " " + page.locator("body").inner_text(timeout=3000)[:2000]))
    except Exception:
        return False


def settle(page, folder: Path, events: list, host: str, seconds: int) -> bool:
    started = time.monotonic()
    while challenge_present(page):
        elapsed = int(time.monotonic() - started)
        events.append({"event": "challenge_waiting", "seconds": elapsed})
        record_page(page, folder, f"challenge_{elapsed:03d}", events, host)
        if elapsed >= seconds:
            return False
        page.wait_for_timeout(min(20000, max(1000, (seconds - elapsed) * 1000)))
        try:
            page.wait_for_load_state("domcontentloaded", timeout=8000)
        except BrowserTimeout:
            pass
    return True


def attempt_page_two(page, before: set[str], source: str, host: str, events: list) -> tuple[bool, str]:
    """Use actual paginator elements, never numbers found in lot descriptions."""
    candidates = [
        "nav[aria-label*='pagination' i] a",
        ".pagination a", ".pager a", "td.pager a",
        "a[rel=next]", "button[aria-label*='next' i]",
        ".pagination button", ".pagination__nav button",
    ]
    for selector in candidates:
        nodes = page.locator(selector)
        try:
            length = min(nodes.count(), 30)
        except Exception:
            continue
        for i in range(length):
            node = nodes.nth(i)
            try:
                label = node.inner_text(timeout=1500).strip().lower()
                if label not in {"2", "next", "далее", "следующая", "→", "›", "»"}:
                    continue
                node.click(timeout=12000)
                page.wait_for_timeout(1800)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except BrowserTimeout:
                    pass
                after = set(detail_urls(page, source, host))
                new_count = len(after - before)
                events.append({"event": "page2_attempt", "method": selector,
                               "previous_count": len(before),
                               "next_count": len(after),
                               "new_lot_urls": new_count})
                return bool(after and new_count and after != before), selector
            except Exception as exc:
                events.append({"event": "page2_click_failed", "method": selector,
                               "error": type(exc).__name__})
    return False, "none"


def scan(page, profile, base: Path, max_details: int, challenge_timeout: int) -> dict:
    host = urlsplit(profile.catalogue_url).hostname
    folder = base / profile.source_id
    folder.mkdir(parents=True, exist_ok=True)
    events: list[dict] = []
    payloads = folder / "network"
    payloads.mkdir(exist_ok=True)
    network_count = 0

    def on_response(resp):
        nonlocal network_count
        if network_count >= 100 or resp.status in (401, 403, 429):
            return
        if resp.request.resource_type not in {"xhr", "fetch"}:
            return
        mime = resp.headers.get("content-type", "").lower()
        if "application/json" not in mime:
            return
        # Preserve only same-host public responses; avoid binary, auth and login.
        if not public_url(resp.url, host) or NOISE.search(urlsplit(resp.url).path):
            return
        try:
            body = resp.body()
            if not body or len(body) > MAX_API:
                return
            data = json.loads(body)
            # Raw JSON can contain personal data: stored locally only.
            path = payloads / f"response_{network_count:03d}.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            network_count += 1
            events.append({"event": "json_response", "url": safe_url_label(resp.url),
                           "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)})
        except Exception as exc:
            events.append({"event": "json_save_failed", "error": type(exc).__name__})

    page.on("response", on_response)
    status = "unverified"
    first: list[str] = []
    page_two = False
    details = 0
    try:
        response = page.goto(profile.catalogue_url, wait_until="domcontentloaded", timeout=45000)
        code = response.status if response is not None else 0
        events.append({"event": "landing", "http_status": code,
                       "url": safe_url_label(page.url)})
        record_page(page, folder, "01_landing", events, host)
        if code == 429:
            status = "rate_limited"
        elif code in (401, 403):
            status = "access_denied"
        elif not settle(page, folder, events, host, challenge_timeout):
            status = "challenge_unresolved"
        else:
            first = detail_urls(page, profile.source_id, host)
            record_page(page, folder, "02_catalogue_first", events, host)
            events.append({"event": "first_page", "links": len(first)})
            if not first:
                status = "no_confirmed_lot_links"
            else:
                page_two, method = attempt_page_two(
                    page, set(first), profile.source_id, host, events
                )
                record_page(page, folder, "03_catalogue_second_attempt", events, host)
                status = "two_pages_distinct" if page_two else "single_page_only"
                # Visit a small number of first-party links, never submit forms.
                for index, link in enumerate(first[:max_details], 1):
                    try:
                        response = page.goto(link, wait_until="domcontentloaded", timeout=35000)
                        if response is None or response.status in (401, 403, 429):
                            events.append({"event": "detail_unavailable",
                                           "http_status": response.status if response else 0})
                            continue
                        if challenge_present(page):
                            events.append({"event": "detail_challenge"})
                            record_page(page, folder, f"lot_{index:02d}_challenge", events, host)
                            continue
                        record_page(page, folder, f"lot_{index:02d}", events, host)
                        details += 1
                    except Exception as exc:
                        events.append({"event": "detail_error", "error": type(exc).__name__})
    except Exception as exc:
        status = "navigation_error"
        events.append({"event": "navigation_error", "error": type(exc).__name__})
        record_page(page, folder, "error", events, host)
    finally:
        page.remove_listener("response", on_response)
        (folder / "events.json").write_text(
            json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return {"site": profile.source_id, "status": status, "first_page_links": len(first),
            "page_two_distinct": page_two, "detail_pages_saved": details,
            "json_responses_saved": network_count, "complete_catalogue_verified": False,
            "production_ready": False, "can_reconcile_missing": False}


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--sites", nargs="*", default=[])
    cli.add_argument("--out", type=Path, default=Path("etp-evidence"))
    cli.add_argument("--max-details", type=int, default=2)
    cli.add_argument("--challenge-timeout", type=int, default=90)
    cli.add_argument("--headless", action="store_true")
    args = cli.parse_args()
    if not 0 <= args.max_details <= 3 or not 30 <= args.challenge_timeout <= 180:
        cli.error("max-details must be 0..3 and challenge-timeout 30..180")
    known = {item.source_id: item for item in PROFILES}
    selected = args.sites or list(known)
    if any(item not in known for item in selected) or len(selected) != len(set(selected)):
        cli.error("Unknown or duplicated source id")
    root = args.out / datetime.now(timezone.utc).strftime("TORGI_ETP_V6_%Y%m%dT%H%M%SZ")
    root.mkdir(parents=True, exist_ok=False)
    outcomes = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        try:
            for source in selected:
                ctx = browser.new_context(ignore_https_errors=False, accept_downloads=False)
                page = ctx.new_page()
                try:
                    outcomes.append(scan(page, known[source], root, args.max_details,
                                         args.challenge_timeout))
                finally:
                    ctx.close()
                time.sleep(1.5)
        finally:
            browser.close()
    summary = {"collector_version": "6.0", "sites": outcomes,
               "complete_source_inventory": False, "database_writes": 0}
    (root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    target = root.with_suffix(".zip")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for f in root.rglob("*"):
            if f.is_file():
                archive.write(f, f.relative_to(root.parent))
    print("Raw evidence stored locally (may contain personal data):", target)
    print("Review/redact before publishing; no TORGI production actions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
