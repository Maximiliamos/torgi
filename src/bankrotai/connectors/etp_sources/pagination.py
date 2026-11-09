"""Pure pagination helpers shared by official ETP catalogue adapters.

This module cannot issue network requests or write data. An ETP's final-page
proof still requires source-specific evidence; these helpers only construct
navigation requests and inspect observed public pagination controls.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import Tag

if TYPE_CHECKING:
    from bankrotai.connectors.etp_sources.common import EtpSourceProfile


def get_form_params(form: Tag, page_field: str, page: int) -> list[tuple[str, str]]:
    """Browser-style GET form fields, retaining repeated checked filters."""
    params: list[tuple[str, str]] = []
    for node in form.select("input[name]"):
        field_type = str(node.get("type") or "").lower()
        if field_type in {"submit", "button", "image", "file"}:
            continue
        if field_type in {"checkbox", "radio"} and not node.has_attr("checked"):
            continue
        name = str(node.get("name"))
        value = str(page) if name == page_field else str(node.get("value") or "")
        params.append((name, value))
    if not any(name == page_field for name, _ in params):
        params.append((page_field, str(page)))
    return params


def has_next_page(profile: EtpSourceProfile, html: str | bytes, page: int) -> bool:
    # Local import avoids an import cycle while retaining the project's
    # established legacy-encoding parser and text normalization.
    from bankrotai.connectors.etp_sources.common import _clean, _parse_soup

    soup = _parse_soup(html)
    if profile.form_page_field:
        return any(
            _clean(node.get_text(" ", strip=True)) == str(page + 1)
            and re.search(rf"pagenum_send\(\s*{page + 1}\s*\)", str(node.get("onclick") or ""))
            for node in soup.select("[onclick]")
        )
    if profile.postback_pagination:
        links = [
            node for node in soup.select("a[href]")
            if "__doPostBack" in str(node.get("href") or "")
        ]
        if any(
            _clean(node.get_text(" ", strip=True)) == str(page + 1)
            for node in links
        ):
            return True
        # ETPU groups its ASP.NET pager in ten-page blocks. At the block
        # boundary the next page is represented by the provider's own ">>"
        # postback control instead of a visible numeric page link.
        if profile.source_id == "etpu.ru":
            return any(
                _clean(node.get_text(" ", strip=True)) in {">>", "»"}
                for node in links
            )
        return False
    if not profile.supports_get_pagination or not profile.page_parameter:
        return False
    expected = str(profile.first_page_value + page)
    for anchor in soup.select("a[href]"):
        query = parse_qs(urlparse(urljoin(profile.listing_url, str(anchor.get("href") or ""))).query)
        if expected in query.get(profile.page_parameter, []):
            return True
    return False



def postback_payload(
    profile: EtpSourceProfile,
    html: str | bytes,
    page: int,
    *,
    sequential_previous: bool,
) -> dict[str, str]:
    """Build the provider's ASP.NET postback payload for one sequential page."""
    from bankrotai.connectors.etp_sources.common import _clean, _parse_soup

    soup = _parse_soup(html)
    links = [
        node for node in soup.select("a[href]")
        if "__doPostBack" in str(node.get("href") or "")
    ]
    link = next(
        (node for node in links if _clean(node.get_text(" ", strip=True)) == str(page)),
        None,
    )
    if link is None and profile.source_id == "etpu.ru" and sequential_previous:
        link = next(
            (
                node for node in links
                if _clean(node.get_text(" ", strip=True)) in {">>", "»"}
            ),
            None,
        )
    if link is None:
        raise ValueError(f"page {page} postback target disappeared")
    postback = re.search(
        r"__doPostBack\('([^']*)','([^']*)'\)",
        str(link.get("href") or ""),
    )
    if not postback:
        raise ValueError(f"invalid page {page} postback")
    data = {
        str(node.get("name")): str(node.get("value") or "")
        for node in soup.select("input[type=hidden][name]")
    }
    data["__EVENTTARGET"], data["__EVENTARGUMENT"] = postback.groups()
    return data

def page_url(profile: EtpSourceProfile, page: int) -> str:
    if page <= 1 or not profile.page_parameter:
        return profile.listing_url
    parsed = urlparse(profile.listing_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query[profile.page_parameter] = [str(profile.first_page_value + page - 1)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
