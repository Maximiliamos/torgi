"""Prepare bounded source-backed corrections from checked public HTML snapshots.

Reads snapshot payload JSON on stdin, performs SELECT-only identity lookups,
and outputs evidence for a separate preview/guarded transaction. No DB writes.
"""
import base64
from dataclasses import MISSING, fields
import hashlib
import json
import sys

from bs4 import BeautifulSoup
from sqlalchemy import select, text

from bankrotai.db import ProcessedLot, SessionLocal, SourceLot
from bankrotai.domain import NormalizedLot
from bankrotai.regions import REGIONS_BY_CODE
from bankrotai.services.asset_category import classify_category
from bankrotai.tbankrot_detail import parse_tbankrot_detail_html


def main():
    snapshots = json.load(sys.stdin)
    if not snapshots or len(snapshots) > 100:
        raise ValueError("Prepare requires 1..100 exact snapshots")
    evidence = []
    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        for receipt in snapshots:
            identity = receipt["external_id"]
            url = "https://tbankrot.ru/item?id=" + identity.split(":", 1)[1]
            if receipt["source_url"] != url or not identity.startswith("tbankrot:"):
                raise ValueError("Snapshot URL does not match source identity")
            body = base64.b64decode(receipt["snapshot_base64"])
            if hashlib.sha256(body).hexdigest() != receipt["sha256"]:
                raise ValueError("Stored snapshot content does not match receipt")
            html = body.decode("utf8", errors="replace")
            source_fields = parse_tbankrot_detail_html(html, url)
            if not source_fields.get("detail_identity_verified"):
                raise ValueError("Exact snapshot identity is absent")
            record = session.get(ProcessedLot, int(receipt["lot_id"]))
            if record is None or (record.source_system, record.external_id) != ("tbankrot.ru", identity):
                raise ValueError("Database and snapshot identity differ")
            source = session.scalar(select(SourceLot).where(
                SourceLot.source_system == record.source_system, SourceLot.external_id == identity,
            ))
            values = {}
            for field in fields(NormalizedLot):
                if hasattr(record, field.name):
                    values[field.name] = getattr(record, field.name)
                elif source is not None and hasattr(source, field.name):
                    values[field.name] = getattr(source, field.name)
                elif field.default is MISSING and field.default_factory is MISSING:
                    values[field.name] = None
            raw = dict(source.raw_data or {}) if source is not None else {}
            observed = {"detail_level", "category"}
            literal = set()
            for name in ("title", "description", "address", "start_price", "current_price",
                         "application_deadline", "auction_at"):
                if source_fields.get(name) not in (None, ""):
                    values[name] = source_fields[name]
                    observed.add(name)
                    literal.add(name)
            values["category"] = classify_category(values["title"], values["description"])
            values["detail_level"] = "detail"
            panel = BeautifulSoup(html, "html.parser").select_one("#lot_info")
            closed = any(any(marker in node.get_text(" ", strip=True).casefold() for marker in (
                "торги завершены", "торги состоялись", "торги не состоялись", "торги отменены", "лот снят с торгов",
            )) for node in panel.select("span.closed"))
            if closed:
                values["auction_status"] = "closed"
                observed.add("auction_status")
                literal.add("auction_status")
            numbers = source_fields.get("cadastral_numbers") or []
            if numbers:
                values["cadastral_number"] = numbers[0]
                raw["cadastral_numbers"] = numbers
                observed.add("cadastral_number")
                literal.add("cadastral_number")
                region = numbers[0].split(":", 1)[0]
                if region in REGIONS_BY_CODE:
                    values["region_slug"] = raw["region_code"] = region
                    values["region_name"] = REGIONS_BY_CODE[region].name
            values["source_url"] = values["lot_url"] = url
            observed.update({"source_url", "lot_url"})
            literal.update({"source_url", "lot_url"})
            raw.update(repair_observed_fields=sorted(observed),
                       repair_evidence_sha256=receipt["sha256"],
                       repair_evidence_checked_at=receipt["checked_at_utc"],
                       repair_source_access_scope="public_fields_only")
            verification = dict(raw.get("verified_source_fields") or {})
            verification.update({name: {"sha256": receipt["sha256"], "checked_at_utc": receipt["checked_at_utc"]}
                                 for name in literal})
            raw["verified_source_fields"] = verification
            values["raw_data"] = raw
            evidence.append({"lot_id": record.id, "normalized": values, "verification": {
                "identity_verified": True, "external_id": identity, "source_url": url,
                "sha256": receipt["sha256"], "checked_at_utc": receipt["checked_at_utc"],
                "observed_fields": sorted(literal), "derived_fields": ["category", "detail_level"],
            }})
    print(json.dumps({"items": evidence, "database_writes": 0}, ensure_ascii=True, default=str))


if __name__ == "__main__":
    main()
