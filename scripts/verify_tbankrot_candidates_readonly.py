"""Read-only exact-ID TBankrot verifier producing hash-locked repair evidence.

This tool is intentionally bounded. It requires explicit lot IDs and stops on HTTP 429
instead of retrying a bulk collection.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time

from sqlalchemy import select, text

from bankrotai.db import ProcessedLot, SessionLocal, SourceLot
from bankrotai.domain import NormalizedLot
from bankrotai.logic import classify_category
from bankrotai.scrapers import TBankrotClient
from bankrotai.services.verified_source_repair import evidence_sha256


def _number(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _baseline(record: ProcessedLot, source: SourceLot) -> NormalizedLot:
    lot = NormalizedLot.from_processed_lot(record)
    # from_processed_lot historically maps missing money to zero; exact evidence
    # must preserve absence as absence.
    lot.start_price = _number(record.start_price)
    lot.current_price = _number(record.current_price)
    lot.raw_data = dict(source.raw_data or {})
    for name in (
        "platform_name", "platform_code", "procedure_number", "notice_number",
        "efresb_message_number", "debtor_name", "organizer_name",
        "auction_manager_name", "bankruptcy_case_number", "deposit_amount",
        "deposit_percent", "deposit_payment_details", "deposit_deadline",
        "application_start_at", "application_deadline", "auction_at",
        "auction_timezone", "published_at", "auction_step_amount",
        "auction_step_percent", "auction_type", "public_offer_schedule",
        "next_interval_price", "next_price_reduction_at",
        "document_completeness", "inspection_procedure", "organizer_contact",
    ):
        value = getattr(source, name, None)
        if isinstance(value, Decimal):
            value = float(value)
        setattr(lot, name, value)
    lot.lot_url = source.lot_url or record.lot_url
    lot.source_url = source.source_url or record.source_url
    return lot


def _selected_ids(args) -> list[int]:
    values: list[int] = []
    if args.lot_ids:
        values.extend(int(part.strip()) for part in args.lot_ids.split(",") if part.strip())
    if args.input:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
        items = payload.get("items", payload) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise ValueError("Input must be a list or an object with items")
        for item in items:
            if isinstance(item, dict) and item.get("lot_id") is not None:
                values.append(int(item["lot_id"]))
            elif isinstance(item, int):
                values.append(item)
    values = list(dict.fromkeys(values))
    if not values:
        raise ValueError("Explicit --lot-ids or --input is required")
    if len(values) > args.max_items:
        raise ValueError(f"Refusing {len(values)} lots; --max-items={args.max_items}")
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lot-ids", help="Comma-separated ProcessedLot IDs")
    parser.add_argument("--input", help="JSON list/object containing lot_id values")
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-items", type=int, default=20)
    parser.add_argument("--sleep-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if not 1 <= args.max_items <= 50:
        raise ValueError("--max-items must be between 1 and 50")

    ids = _selected_ids(args)
    client = TBankrotClient()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    stopped_on_429 = False

    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        for lot_id in ids:
            record = session.get(ProcessedLot, lot_id)
            if record is None:
                results.append({"lot_id": lot_id, "error": "processed_lot_missing"})
                continue
            if record.source_system != "tbankrot.ru":
                results.append({"lot_id": lot_id, "error": "not_tbankrot"})
                continue
            source = session.scalar(
                select(SourceLot).where(
                    SourceLot.source_system == record.source_system,
                    SourceLot.external_id == record.external_id,
                )
            )
            if source is None or source.processed_lot_id != record.id:
                results.append({"lot_id": lot_id, "error": "source_projection_link_missing"})
                continue
            url = source.lot_url or source.source_url or record.lot_url or record.source_url
            if not url:
                results.append({"lot_id": lot_id, "error": "detail_url_missing"})
                continue

            result = {
                "lot_id": record.id,
                "source_system": record.source_system,
                "external_id": record.external_id,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "detail_url": url,
            }
            try:
                response = client.session.get(url, timeout=client.timeout)
                if response.status_code == 429:
                    result["error"] = "http_429"
                    results.append(result)
                    stopped_on_429 = True
                    break
                response.raise_for_status()
                body = response.content
                result["detail_html_sha256"] = hashlib.sha256(body).hexdigest()
                fields = client.parse_detail_html(response.text, response.url or url)
                if not fields.get("detail_identity_verified"):
                    raise ValueError("TBankrot exact detail identity was not verified")

                lot = _baseline(record, source)
                raw = dict(lot.raw_data or {})
                observed: list[str] = []

                for field in ("title", "description", "address"):
                    value = fields.get(field)
                    if value is not None:
                        setattr(lot, field, value)
                        observed.append(field)

                numbers = fields.get("cadastral_numbers") or []
                if numbers:
                    lot.cadastral_number = numbers[0]
                    raw["cadastral_numbers"] = list(numbers)
                    observed.append("cadastral_number")

                for field in ("start_price", "current_price", "application_deadline", "auction_at"):
                    value = fields.get(field)
                    if value is not None:
                        setattr(lot, field, value)
                        observed.append(field)

                status = fields.get("auction_status")
                if status not in {None, "", "unknown"}:
                    lot.auction_status = str(status)
                    observed.append("auction_status")

                if any(name in observed for name in ("title", "description")):
                    lot.category = classify_category(lot.title, lot.description)
                    observed.append("category")

                raw.update({
                    "detail_identity_verified": True,
                    "detail_html_sha256": result["detail_html_sha256"],
                    "detail_source_url": response.url or url,
                    "repair_observed_fields": list(dict.fromkeys(observed)),
                })
                lot.raw_data = raw
                lot.detail_level = "detail"

                normalized = asdict(lot)
                result["normalized"] = normalized
                result["sha256"] = evidence_sha256(normalized)
                result["observed_fields"] = raw["repair_observed_fields"]
                result["success"] = True
            except Exception as exc:
                result["error"] = f"{type(exc).__name__}: {exc}"
            results.append(result)
            output.write_text(
                json.dumps(
                    {
                        "database_writes": 0,
                        "stopped_on_429": stopped_on_429,
                        "items": results,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)

        session.rollback()

    output.write_text(
        json.dumps(
            {
                "database_writes": 0,
                "stopped_on_429": stopped_on_429,
                "items": results,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(json.dumps({
        "selected": len(ids),
        "completed": len(results),
        "success": sum(bool(item.get("success")) for item in results),
        "stopped_on_429": stopped_on_429,
        "output": str(output),
    }))


if __name__ == "__main__":
    main()
