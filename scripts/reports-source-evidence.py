"""Refresh only exact identities from a bounded LotIssueReport export; no DB access."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, fields, MISSING
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import os

from bankrotai.domain import NormalizedLot
from bankrotai.logic import classify_category


def baseline(report):
    values = {field.name: report.get(field.name) for field in fields(NormalizedLot)
              if field.default is MISSING and field.default_factory is MISSING}
    values.update(category=classify_category(report["title"], report["description"] or ""),
                  region_slug=report.get("region_code"), detail_level="search", raw_data={})
    for key in ("start_price", "current_price"):
        values[key] = float(values[key]) if values[key] is not None else None
    return NormalizedLot(**values)


async def fetch(report):
    source, eid = report["source_system"], report["external_id"]
    if source == "tbankrot.ru":
        from bankrotai.connectors.registry.tbankrot import TBankrotConnector
        lot = baseline(report)
        # Force a real exact-ID detail read even when the persisted fields look complete.
        lot.application_deadline = lot.auction_at = None
        connector = TBankrotConnector()
        lot = await connector.enrich_lot(lot)
        if not lot.raw_data.get("detail_identity_verified"):
            raise ValueError("TBankrot exact panel identity was not verified")
        lot.raw_data["repair_observed_fields"] = [
            "title", "description", "category", "address", "cadastral_number", "auction_status",
            *[name for name, marker in (("start_price", "detail_start_price_verified"),
                                       ("current_price", "detail_current_price_verified")) if lot.raw_data.get(marker)],
            *[name for name in ("application_deadline", "auction_at") if getattr(lot, name) is not None],
        ]
        return lot
    if source == "torgi.gov.ru":
        from bankrotai.connectors.registry.torgi_gov import TorgiGovConnector
        return await TorgiGovConnector().fetch_lot(eid)
    if source == "bidexpert.ru":
        from bankrotai.connectors.registry.bidexpert import BidExpertConnector
        return await BidExpertConnector().fetch_lot(eid)
    if source == "m-ets.ru":
        from bankrotai.connectors.registry.mets_public import MetsConnector
        lot = await MetsConnector().fetch_lot(eid.removeprefix("m-ets.ru:"))
        if eid != "m-ets.ru:" + lot.external_id:
            raise ValueError("Legacy M-ETS identity mapping differs")
        lot.raw_data["verified_native_external_id"] = lot.external_id
        lot.external_id = eid
        return lot
    if source == "torgi-russia.ru":
        from bankrotai.connectors.registry.torgi_russia import TorgiRussiaConnector
        from bankrotai.torgi_russia import TorgiRussiaClient
        connector = TorgiRussiaConnector()
        html, url = connector.client.fetch_lot_page(eid)
        records = [item for item in TorgiRussiaClient._extract_public_lot_records(html, require_linked=False)
                   if str(item.get("id")) == eid.split(":", 1)[1]]
        if len(records) != 1:
            raise ValueError("Torgi Russia exact payload identity was not uniquely verified")
        lot = TorgiRussiaClient.parse_search_payload({"data": records})[0]
        # Reuse the just-observed exact page rather than requesting an unrelated snapshot.
        connector.client.fetch_lot_page = lambda _: (html, url)
        return await connector.enrich_lot(lot)
    if source == "lot-online.ru":
        from bankrotai.connectors.registry.lot_online import LotOnlineConnector
        lot = baseline(report)
        lot = await LotOnlineConnector().enrich_lot(lot)
        lot.raw_data["repair_observed_fields"] = ["address", "description", "cadastral_number", "application_start_at", "application_deadline", "auction_at"]
        return lot
    from bankrotai.connectors.etp_sources import SOURCE_CONNECTOR_TYPES
    connector = SOURCE_CONNECTOR_TYPES[source]()
    if source == "sistematorg.com" and eid.count(":") == 1:
        # Legacy procedure-only row is accepted only when exactly one numbered lot exists.
        from bankrotai.connectors.etp_sources.sistematorg_contract import parse_sistematorg_detail_lots
        trade = eid.split(":")[1]
        response = connector.client.fetch_detail(f"https://sistematorg.com/trade_view.php?trade_nid={trade}")
        lots = parse_sistematorg_detail_lots(response.content, detail_url=response.url)
        if len(lots) != 1:
            raise ValueError("Legacy Sistematorg procedure has multiple individual lots")
        lot = lots[0]
        lot.raw_data["verified_native_external_id"] = lot.external_id
        lot.external_id = eid
        return lot
    return await connector.fetch_lot(eid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--last-report", type=int, default=44)
    parser.add_argument("--public-tbankrot", action="store_true", help="Explicitly collect public fields without asserting authenticated completeness")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    reports = json.loads(Path(args.snapshot).read_text(encoding="utf-8-sig"))["items"]
    selected = {item["lot_id"]: item for item in reports if item["report_id"] <= args.last_report}
    if not selected or len(selected) > 100:
        raise ValueError("Unsafe exact report selection")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.public_tbankrot:
        os.environ.pop("TBANKROT_COOKIE_FILE", None)
    results = json.loads(output.read_text(encoding="utf-8"))["items"] if args.retry_failed and output.exists() else []
    successful = {item["lot_id"] for item in results if "normalized" in item}
    results = [item for item in results if item["lot_id"] in successful]
    for report in selected.values():
        if report["lot_id"] in successful:
            continue
        result = {"lot_id": report["lot_id"], "source_system": report["source_system"],
                  "external_id": report["external_id"], "observed_at": datetime.now(timezone.utc).isoformat()}
        try:
            lot = asyncio.run(fetch(report))
            if (lot.source_system, lot.external_id) != (report["source_system"], report["external_id"]):
                raise ValueError("Exact source identity mismatch")
            result["normalized"] = asdict(lot)
            result["sha256"] = hashlib.sha256(json.dumps(result["normalized"], sort_keys=True, default=str).encode()).hexdigest()
            print(json.dumps({"lot_id": report["lot_id"], "status": lot.auction_status,
                              "source": lot.source_system, "success": True}), flush=True)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"lot_id": report["lot_id"], "error": result["error"]}), flush=True)
        results.append(result)
        output.write_text(json.dumps({"database_writes": 0, "items": results}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        time.sleep(0.5)


if __name__ == "__main__":
    main()
