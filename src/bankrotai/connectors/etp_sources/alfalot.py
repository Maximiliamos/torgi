"""Public GD sales catalogue, verified against the browser's searchServlet request."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone

from bankrotai.connectors.base import ConnectorHealth, ConnectorPage
from bankrotai.connectors.etp_sources.alfalot_audit import (
    canonical_lot_url, extract_detail_fields, lot_entity_from_url,
)
from bankrotai.connectors.etp_sources.common import EtpSearchFilters, HtmlEtpConnector, SourceSchemaError
from bankrotai.domain import NormalizedLot
from bankrotai.extractors import extract_area, extract_cadastral_numbers
from bankrotai.services.asset_category import classify_category
from bankrotai.services.property_address_evidence import extract_asset_property_address
from bankrotai.services.real_estate_filter import is_sale_real_estate_lot

API_URL = "https://etp.alfalot.ru/searchServlet"
PAGE_SIZE = 20


def catalogue_params(page):
    # The servlet's max is a row count, not an exclusive end offset. The UI
    # sends an increasing max on later pages, which repeats its final rows.
    return {name: json.dumps(value, separators=(",", ":")) for name, value in {
        "query": {"types": ["SALE", "DEBTOR_SALE"]}, "filter": {"state": ["GD"]},
        "sort": {"placementDate": False},
        "limit": {"min": (page-1)*PAGE_SIZE, "max": PAGE_SIZE, "updateTotalCount": True},
    }.items()}


def parse_catalogue(payload, *, page):
    if not isinstance(payload, dict) or not isinstance(payload.get("list"), list):
        raise SourceSchemaError("alfalot.ru: public catalogue schema changed")
    rows, total, count = payload["list"], payload.get("totalCount"), payload.get("count")
    if (not isinstance(total, int) or isinstance(total, bool) or total < 0
            or count != len(rows) or len(rows) > PAGE_SIZE
            or len(rows) != min(PAGE_SIZE, max(0, total-(page-1)*PAGE_SIZE))):
        raise SourceSchemaError("alfalot.ru: catalogue window/count is not proven")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise SourceSchemaError("alfalot.ru: malformed individual catalogue row")
        entity = lot_entity_from_url(row.get("lotLink") or "")
        if (entity is None or entity.split(":", 1)[1] != row.get("uuid") or entity in seen
                or row.get("type") not in {"SALE", "DEBTOR_SALE"}
                or not re.fullmatch(r"\d{8,12}[A-Z]{2}", str(row.get("identifier", "")))
                or not isinstance(row.get("lotNumber"), int) or row["lotNumber"] < 1):
            raise SourceSchemaError("alfalot.ru: individual catalogue identity is not proven")
        seen.add(entity)
    return rows, total


def _date(value):
    if not isinstance(value, str):
        return None
    match = re.search(r"\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}", value)
    return datetime.strptime(match.group(), "%d.%m.%Y %H:%M") if match else None


def normalize_detail(row, content, *, checked_at):
    entity = lot_entity_from_url(row["lotLink"])
    if entity is None:
        raise SourceSchemaError("alfalot.ru: invalid individual detail identity")
    url = canonical_lot_url(entity)
    fields = extract_detail_fields(content.decode("utf-8"), url)
    expected = f"{row['identifier']}.lot{row['lotNumber']}"
    if fields["visible_lot_number"] != expected or not fields["title"]:
        raise SourceSchemaError("alfalot.ru: catalogue/detail lot-number mismatch")
    title = str(fields["title"])
    cadastres = extract_cadastral_numbers(title)
    cad = cadastres[0] if cadastres else None
    address = (fields["address"] if isinstance(fields["address"], str) else None) or extract_asset_property_address(
        title=title, description=title, cadastral_number=cad)
    status_text = str(fields["status"] or "").casefold().replace("ё", "е")
    if "прием заявок" in status_text and not any(word in status_text for word in ("заверш", "окончен")):
        status = "active"
    elif "объявлен" in status_text:
        status = "scheduled"
    elif "приостанов" in status_text:
        status = "paused"
    elif any(word in status_text for word in ("торги заверш", "лот продан", "отменен", "торги не состоял", "торги состоял")):
        status = "closed"
    else:
        status = "unknown"
    initial_value, current_value = fields["start_price_kopecks"], fields["current_price_kopecks"]
    initial = initial_value if isinstance(initial_value, int) else None
    current = current_value if isinstance(current_value, int) else None
    sha = hashlib.sha256(content).hexdigest()
    proof = {"sha256": sha, "checked_at_utc": checked_at.isoformat()}
    verified = {name: {**proof, "value": value} for name, value in (
        ("title", title[:500]), ("cadastral_number", cad), ("address", address),
        ("auction_status", status),
    ) if value not in (None, "", "unknown")}
    return NormalizedLot(
        external_id="alfalot.ru:"+entity, source="alfalot", source_system="alfalot.ru",
        title=title[:500], description=title[:5000], category=classify_category(title, ""),
        region_slug=None, region_name=None, address=address, cadastral_number=cad,
        vin=None, area=extract_area(title) if len(cadastres) <= 1 else None, start_price=initial/100 if initial is not None else None,
        current_price=current/100 if current is not None else None, auction_status=status,
        lot_url=url, source_url=url, detail_level="detail", procedure_number=row["identifier"],
        application_start_at=_date(row.get("gdStartDate")),
        application_deadline=_date(row.get("gdEndDate")), auction_timezone="Europe/Moscow",
        raw_data={"cadastral_numbers": cadastres, "native_lot_entity": entity,
            "lot_number": row["lotNumber"], "source_status_text": fields["status"],
            "catalogue_scope": "public_GD_SALE_DEBTOR_SALE", "complete_source_inventory": False,
            "safe_for_archival": False, "reconcile_missing": False,
            "detail_identity_verified": True, "detail_enrichment_status": "success",
            "detail_start_price_verified": initial is not None,
            "detail_current_price_verified": current is not None,
            "detail_status_verified": status != "unknown", "detail_enrichment_version": 1,
            "verified_source_fields": verified, "source_response_sha256": sha},
    )


class AlfalotConnector(HtmlEtpConnector):
    _seen_entities: set[str]
    _catalogue_rows: dict[str, dict]
    safe_for_reconciliation = False
    detail_enrichment_version = 1
    capabilities = frozenset({"search", "fetch_lot", "detail_enrichment", "healthcheck"})

    def _request(self, page):
        return self._read_request("get", API_URL, params=catalogue_params(page),
                                  timeout=self.timeout, allow_redirects=False)

    async def search(self, filters, cursor=None):
        normalized = filters if isinstance(filters, EtpSearchFilters) else EtpSearchFilters(**(filters or {}))
        page = max(1, int(cursor or normalized.page))
        if page == 1 or not hasattr(self, "_seen_entities"):
            self._seen_entities, self._catalogue_rows = set(), {}
        response = await asyncio.to_thread(self._request, page)
        rows, total = parse_catalogue(response.json(), page=page)
        lots = []
        for row in rows:
            entity = lot_entity_from_url(row["lotLink"])
            if entity is None:
                raise SourceSchemaError("alfalot.ru: invalid individual catalogue identity")
            if entity in self._seen_entities:
                raise SourceSchemaError("alfalot.ru: repeated individual entity across pages")
            self._seen_entities.add(entity)
            self._catalogue_rows[entity] = row
            # Scope comes from this individual asset, never the procedure title.
            category = classify_category(row.get("title") or "", "")
            if category in {"other", "car", "equipment", "receivable", "commodity"}:
                continue
            lot = await self.fetch_lot("alfalot.ru:"+entity)
            if is_sale_real_estate_lot(lot):
                lots.append(lot)
        more = (page-1)*PAGE_SIZE+len(rows) < total
        return ConnectorPage(items=lots, next_cursor=str(page+1) if more else None, metadata={
            "source": self.source_id, "page": page, "catalogue_items": len(rows), "total_items": total,
            "items_on_page": len(lots), "complete_source_inventory": False, "safe_for_archival": False,
            "reconcile_missing": False, "catalogue_scope": "public_GD_SALE_DEBTOR_SALE",
            "terminal_verified": not more, "response_url": response.url})

    async def fetch_lot(self, external_id):
        prefix = "alfalot.ru:"
        entity = external_id[len(prefix):] if external_id.startswith(prefix) else ""
        row = getattr(self, "_catalogue_rows", {}).get(entity)
        if row is None:
            raise SourceSchemaError("alfalot.ru: observed individual catalogue binding required")
        response = await asyncio.to_thread(self._read_request, "get", canonical_lot_url(entity),
                                           timeout=self.timeout, allow_redirects=False)
        return normalize_detail(row, response.content, checked_at=datetime.now(timezone.utc))

    async def enrich_lot(self, lot):
        if (lot.raw_data or {}).get("detail_identity_verified") and lot.detail_level == "detail":
            return lot
        return await self.fetch_lot(lot.external_id)

    async def healthcheck(self):
        try:
            response = await asyncio.to_thread(self._request, 1)
            rows, total = parse_catalogue(response.json(), page=1)
            return ConnectorHealth("ok", "Public GD sales catalogue verified",
                                   {"source": self.source_id, "rows": len(rows), "total": total})
        except Exception as exc:
            return ConnectorHealth("degraded", str(exc), {"source": self.source_id})
