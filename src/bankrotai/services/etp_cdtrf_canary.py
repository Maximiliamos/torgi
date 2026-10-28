"""CDTRF property canary: official public API and Nuxt-validated details.

No scheduled ingestion, no missing-lot archiving. An administrator may inspect
a single JSON page or, if explicitly enabled, import one exact trade/lot pair.
"""
from __future__ import annotations

import asyncio
import os
import re

import requests
from fastapi import APIRouter, Depends, HTTPException, Path, Query

from bankrotai.connectors.registry.captured_etp_api import parse_cdtrf_api_page
from bankrotai.connectors.registry.cdtrf_public import parse_detail
from bankrotai.domain import NormalizedLot
from bankrotai.logic import classify_category

API = "https://torgi.cdtrf.ru/api/Trade/trades"
BASE = "https://torgi.cdtrf.ru/trades/"
REQUEST_TIMEOUT = (3, 8)
MAX_RESPONSE = 2_000_000
PROPERTY = frozenset({
    "apartment", "house", "land", "parking", "commercial_room",
    "commercial_building", "commercial_building_with_land",
    "unfinished", "complex", "real_estate",
})
ID = re.compile(r"^[1-9]\d{2,11}$")


class CdtrfCanaryError(ValueError):
    pass


def _http_get(session: requests.Session, url: str, *, accepts: str) -> requests.Response:
    if url != API and not (url.startswith(BASE) and ID.fullmatch(url[len(BASE):])):
        raise CdtrfCanaryError("Unknown CDTRF URL")
    response = session.get(
        url, allow_redirects=False, timeout=REQUEST_TIMEOUT,
        headers={"Accept": accepts, "User-Agent": "TORGI-CDTRF-canary/1"},
    )
    if response.status_code != 200:
        raise CdtrfCanaryError(f"CDTRF HTTP {response.status_code}")
    if len(response.content) > MAX_RESPONSE:
        raise CdtrfCanaryError("CDTRF response exceeds size cap")
    return response


def preview_cdtrf_public_api(page: int = 1, *, session: requests.Session | None = None) -> dict:
    """Official JSON filter from observed browser network; no PII extraction."""
    if page not in (1, 2):
        raise CdtrfCanaryError("Canary preview limited to the first two pages")
    endpoint = f"{API}?Declare=true&RecieveReq=true&PageSize=20&PageNum={page}"
    own = session or requests.Session()
    # Checked URL plus literal query keys, no external redirect.
    response = own.get(
        endpoint, allow_redirects=False, timeout=REQUEST_TIMEOUT,
        headers={"Accept": "application/json", "User-Agent": "TORGI-CDTRF-canary/1"},
    )
    if response.status_code != 200 or len(response.content) > MAX_RESPONSE:
        raise CdtrfCanaryError("CDTRF API inaccessible or oversized")
    if "json" not in response.headers.get("Content-Type", "").lower():
        raise CdtrfCanaryError("CDTRF API is not JSON")
    data = parse_cdtrf_api_page(response.json(), page_num=page)
    return {"source": "cdtrf.ru", "page": page,
            "candidate_count": len(data["property_candidates"]),
            "candidates": data["property_candidates"],
            "complete_source_inventory": False, "database_writes": 0,
            "safe_for_archival": False, "ready_for_production_sync": False}


def fetch_verified_cdtrf_lot(trade_id: str, lot_id: str,
                             *, session: requests.Session | None = None) -> NormalizedLot:
    if not ID.fullmatch(trade_id) or not ID.fullmatch(lot_id):
        raise CdtrfCanaryError("Invalid CDTRF trade/lot numeric identifier")
    url = BASE + trade_id
    own = session or requests.Session()
    html = _http_get(own, url, accepts="text/html")
    if "text/html" not in html.headers.get("Content-Type", "").lower():
        raise CdtrfCanaryError("Expected HTML detail")
    detail = parse_detail(html.text, url)
    if detail["trade_id"] != trade_id or detail["trade_lot_id"] != lot_id:
        raise CdtrfCanaryError("Detail lot ID differs from the requested lot")
    title = detail["title"]
    category = classify_category(title, title)
    status = detail["source_status_code"]
    mapped = {"Declare": "scheduled", "RecieveReq": "active"}.get(status)
    if (category not in PROPERTY or not detail.get("cadastral_number")
            or detail.get("initial_price_rub") is None or mapped is None):
        raise CdtrfCanaryError("Insufficient property, price, cadastral or status evidence")
    return NormalizedLot(
        external_id=f"cdtrf:{lot_id}", source="cdtrf.ru",
        source_system="cdtrf.ru", title=title, description=title,
        category=category, region_slug=None, region_name=None,
        address=detail.get("region_label") or None,
        cadastral_number=detail["cadastral_number"],
        vin=None, area=None,
        start_price=float(detail["initial_price_rub"]),
        current_price=None, auction_status=mapped,
        lot_url=url, source_url=url, detail_level="public_detail",
        raw_data={
            "evidence": "url_html_nuxt_identity_match",
            "trade_id": trade_id, "trade_lot_id": lot_id,
            "source_status_text": detail.get("source_status_text"),
            "source_status_code": status,
            "region_id": detail.get("region_id"),
            "price_periods": detail.get("price_periods", []),
            "current_price_unverified": True,
            "complete_source_inventory": False,
        },
        procedure_number=trade_id, platform_name="ЦДТ",
    )


def create_cdtrf_canary_router(require_admin):
    router = APIRouter(prefix="/api/etp/pilots", tags=["ETP canary"])

    @router.get("/cdtrf.ru/preview")
    async def preview(page: int = Query(1, ge=1, le=2), _actor=Depends(require_admin)):
        try:
            return await asyncio.to_thread(preview_cdtrf_public_api, page)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="CDTRF public API unavailable") from exc

    @router.post("/cdtrf.ru/import/{trade_id}/{lot_id}")
    async def import_exact(
        trade_id: str = Path(pattern=r"^[1-9]\d{2,11}$"),
        lot_id: str = Path(pattern=r"^[1-9]\d{2,11}$"),
        actor=Depends(require_admin),
    ):
        if os.environ.get("ETP_CDTRF_CANARY_IMPORT_ENABLED") != "1":
            raise HTTPException(status_code=403, detail="CDTRF canary import disabled")
        try:
            lot = await asyncio.to_thread(fetch_verified_cdtrf_lot, trade_id, lot_id)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="CDTRF detail verification failed") from exc
        from bankrotai.db import session_scope
        from bankrotai.logic import log_action, persist_lot
        with session_scope() as session:
            persisted = persist_lot(session, lot)
            log_action(
                session, str(actor.id), "import_verified_etp_canary", "lot",
                str(persisted.id),
                {"source_system": "cdtrf.ru", "trade_id": trade_id,
                 "lot_id": lot_id, "reconcile_missing": False},
            )
            return {"id": persisted.id, "source_system": "cdtrf.ru",
                    "external_id": lot.external_id,
                    "requires_geocoding_and_map_publication": True,
                    "automatic_sync_enabled": False,
                    "missing_lot_archival_enabled": False}
    return router
