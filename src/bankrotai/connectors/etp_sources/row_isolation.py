"""Prevent per-row trade metadata from contaminating distinct lot records.

A HTML table row with multiple independently linked lots does not prove
that the row-level price, deposit, cadastral number or status belongs to
any one of them. Keep independent anchor-level evidence only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urljoin

from bs4 import Tag

if TYPE_CHECKING:
    from bankrotai.connectors.etp_sources.common import EtpSourceProfile


def has_multiple_lot_links(profile: EtpSourceProfile, container: Tag) -> bool:
    if container.name != "tr":
        return False
    lot_links: set[str] = set()
    for anchor in container.select("a[href]"):
        href = urljoin(profile.listing_url, str(anchor.get("href") or ""))
        matched = profile.lot_url_re.search(href)
        if matched is None:
            continue
        lot_links.add(matched.group(0))
        if len(lot_links) > 1:
            return True
    return False
