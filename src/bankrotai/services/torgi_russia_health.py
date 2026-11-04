"""Read-only Home health probe for the *actual* Torgi Russia SSR catalogue.

Do not treat HEAD/HTTP 200 on an unrelated domain as proof that source lots
can be read. Verify a known populated regional catalogue with the same
parser and region guard that the full ingestion run uses.
"""
from __future__ import annotations

from typing import Any


def probe_torgi_russia_catalogue(*, timeout_seconds: float = 5.0) -> dict[str, Any]:
    from bankrotai.scraper_contracts import TorgiRussiaSearchFilters
    from bankrotai.torgi_russia import (
        BASE_URL, PUBLIC_SEARCH_PATH, TorgiRussiaClient,
    )

    url = BASE_URL + PUBLIC_SEARCH_PATH
    client = TorgiRussiaClient(timeout=max(5.0, min(20.0, timeout_seconds * 2)))
    try:
        lots, metadata = client.search_lots(
            TorgiRussiaSearchFilters(
                category_id="6", history_only=False, page=1, region_id=23,
            )
        )
        # Region 23 was populated at Home (1,250 items, page 1=24) in the
        # read-only run on 4 Nov 2026. Empty/unknown SSR data is NOT proof of
        # recovery, and must leave the circuit blocked.
        if not lots:
            raise ValueError("Torgi Russia source probe empty regional catalogue")
        if len({lot.external_id for lot in lots}) != len(lots):
            raise ValueError("Torgi Russia source probe duplicate lot identities")
        if any(
            lot.source_system != "torgi-russia.ru"
            or lot.start_price is None
            or lot.auction_status in {"unknown", "", None}
            or not lot.region_name
            for lot in lots
        ):
            raise ValueError("Torgi Russia source probe invalid lot data")
        total = metadata.get("total")
        if total is None or int(total) < len(lots):
            raise ValueError("Torgi Russia source probe invalid total count")
        if int(metadata.get("total_pages") or 0) < 1:
            raise ValueError("Torgi Russia source probe missing pagination proof")
        return {
            "success": True, "source_system": "torgi-russia.ru",
            "http_status": 200, "url": url,
            "region_filter_id": 23,
            "validated_lots": len(lots),
            "catalogue_total": int(total),
            "details_or_archive_verified": False,
        }
    except Exception as exc:
        # Operator diagnostics can retain the error class and bounded reason,
        # never arbitrary page content, cookies or user/session tokens.
        return {
            "success": False, "source_system": "torgi-russia.ru",
            "url": url, "error_type": type(exc).__name__,
            "error": "Torgi Russia public SSR catalogue probe failed: "
                     + type(exc).__name__,
        }
    finally:
        client.session.close()
