"""Read-only transport and NISTP page-2 diagnostics: no cookies, raw HTML or PII.

Run on Home from the existing ingestion worker:
    python -m bankrotai.services.etp_transport_diagnostics

This performs one GET for AUSIB, Profit, Roseltorg and only the existing
connector's first/second-page GET+POST for NISTP. No DB or map writes.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from bankrotai.connectors.etp_sources import SOURCE_CONNECTOR_TYPES
from bankrotai.connectors.etp_sources.profiles import PROFILES

SOURCES = ("nistp.ru", "ausib.ru", "etp-profit.ru", "roseltorg.ru")
PROFILE_BY_SOURCE = {p.source_id: p for p in PROFILES}


def safe_url_label(url: str) -> str:
    """Never log query strings, credentials, fragments or one-time tokens."""
    p = urlsplit(url)
    if p.scheme not in {"http", "https"}:
        return "<non-http-redirect>"
    return f"{p.scheme}://{p.hostname or 'unknown'}{p.path[:130]}"


def classify_redirect(source: str, location: str, current_url: str) -> str:
    """Observation only; cannot authorize following any redirect."""
    absolute = urljoin(current_url, location)
    p = urlsplit(absolute)
    permitted = set(PROFILE_BY_SOURCE[source].allowed_hosts)
    if p.scheme != "https":
        return "untrusted_scheme"
    if p.username is not None or p.password is not None or p.port not in {None, 443}:
        return "untrusted_authority"
    if p.hostname not in permitted:
        return "outside_configured_hosts"
    return "allowed_host_observed_not_followed"


def _reply_meta(response: requests.Response) -> dict:
    location = response.headers.get("Location") or ""
    return {
        "http_status": response.status_code,
        "url": safe_url_label(response.url),
        "location": safe_url_label(urljoin(response.url, location)) if location else None,
        "content_type": (response.headers.get("content-type") or "").split(";")[0][:60],
        "body_sha256": hashlib.sha256(response.content).hexdigest(),
        "body_bytes": len(response.content),
    }


async def _nistp() -> dict:
    source = "nistp.ru"
    connector = SOURCE_CONNECTOR_TYPES[source](timeout=20)
    try:
        first = await asyncio.to_thread(connector._request, 1)
        second = await asyncio.to_thread(connector._request, 2)
        results = []
        from bankrotai.connectors.etp_sources.common import parse_listing_html

        for page_number, reply in ((1, first), (2, second)):
            soup = BeautifulSoup(reply.content, "html.parser")
            form = soup.find("form", id=connector.profile.form_id)
            title = soup.title.get_text(" ", strip=True)[:100] if soup.title else None
            try:
                count = len(parse_listing_html(connector.profile, reply.content, page=page_number))
                parser_error = None
            except Exception as exc:
                count, parser_error = 0, type(exc).__name__
            observed_form = {
                "exists": form is not None,
                "method": str(form.get("method") or "GET").upper() if form else None,
                "action": safe_url_label(urljoin(reply.url, str(form.get("action") or ""))) if form else None,
                "field_names": sorted({
                    str(node.get("name"))
                    for node in form.select("input[name]") if node.get("name")
                })[:80] if form else [],
            }
            page_links = {
                "pagenum_send_links": len(soup.select("[onclick*=pagenum_send]")),
                "postback_links": len(soup.select("a[href*=__doPostBack]")),
                "pagination_text_present": "Страница" in soup.get_text(" ", strip=True)
                or "Страницы" in soup.get_text(" ", strip=True),
            }
            results.append({
                **_reply_meta(reply), "page": page_number,
                "parsed_lots": count, "parser_error": parser_error,
                "html_title": title, "form": observed_form,
                "navigation": page_links,
            })
        return {"source": source, "state": "observed", "pages": results,
                "same_body": results[0]["body_sha256"] == results[1]["body_sha256"]}
    except Exception as exc:
        return {"source": source, "state": "error", "error_type": type(exc).__name__,
                "error_text": str(exc)[:250].split("https://")[0]}
    finally:
        connector.session.close()


def _transport(source: str) -> dict:
    profile = PROFILE_BY_SOURCE[source]
    with requests.Session() as session:
        session.headers["User-Agent"] = (
            "TORGI/1.0 (+https://github.com/Maximiliamos/torgi; public read-only connector)"
        )
        try:
            response = session.get(profile.listing_url, timeout=(8, 20), allow_redirects=False)
            info = _reply_meta(response)
            location = response.headers.get("Location")
            if location:
                info["redirect_classification"] = classify_redirect(
                    source, location, response.url
                )
            return {"source": source, "state": "observed", **info}
        except requests.exceptions.SSLError:
            return {
                "source": source, "state": "tls_validation_failed",
                "error_type": "SSLError",
                "trust_bundle_path": requests.certs.where(),
                "trust_bundle_available": os.path.isfile(requests.certs.where()),
                "tls_verification_disabled": False,
            }
        except Exception as exc:
            return {"source": source, "state": "connection_failed",
                    "error_type": type(exc).__name__}


def main() -> None:
    print(json.dumps({
        "report": "etp_transport_readonly_diagnostics",
        "database_writes": 0, "map_writes": 0, "redirects_followed": False,
        "nistp": asyncio.run(_nistp()),
        "other_sources": [_transport(x) for x in SOURCES if x != "nistp.ru"],
    }, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
