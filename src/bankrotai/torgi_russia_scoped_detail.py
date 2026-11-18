"""Current Next.js detail evidence belongs only to the requested lot record."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from bankrotai.torgi_russia_detail_fields import parse_detail_datetime
from bankrotai.torgi_russia_status import public_auction_status


def parse_structured_lot_page(html, page_url, client_cls):
    from bankrotai.torgi_russia import CADASTRAL_RE, TorgiRussiaDetails, normalize_cadastral_number

    records = client_cls._extract_public_lot_records(html, require_linked=False)
    if not records and not client_cls._extract_next_flight_text(html):
        return None  # Legacy DOM parser remains available for diagnostics.
    match = re.fullmatch(r"/lot/(\d+)/?", urlparse(page_url).path)
    expected = int(match.group(1)) if match else None
    record = next((r for r in records if r.get("id") == expected), None)
    if record is None:
        raise RuntimeError("Torgi Russia detail identity does not match requested lot")
    detail = client_cls.parse_detail_payload(record)
    description = detail.get("description") or None
    cadastres = detail.get("cadastral_numbers") or list(dict.fromkeys(
        normalize_cadastral_number(c) for c in CADASTRAL_RE.findall(
            str(record.get("title") or "") + " " + str(description or ""),
        )
    ))
    gis_link = None
    for item in record.get("source_links") or []:
        link = item.get("link") if isinstance(item, dict) else None
        parsed = urlparse(str(link or ""))
        if (parsed.scheme == "https" and parsed.hostname == "torgi.gov.ru"
                and re.match(r"^/new/public/lots/lot/[^/]+/", parsed.path)):
            gis_link = link
            break
    return TorgiRussiaDetails(
        torgi_russia_url=page_url, gis_torgi_url=gis_link,
        etp_url=detail.get("etp_url"), image_urls=detail.get("image_urls") or [],
        cadastral_numbers=cadastres, description=description,
        address=detail.get("address") or client_cls._property_address_from_description(str(record.get("title") or "")),
        auction_status=public_auction_status(detail.get("source_status")),
        application_start_at=parse_detail_datetime(detail.get("application_start_at")),
        application_deadline=parse_detail_datetime(detail.get("application_deadline")),
        auction_at=parse_detail_datetime(detail.get("auction_at")),
        detail_identity_verified=True, detail_external_id=f"torgi-russia:{expected}",
    )
