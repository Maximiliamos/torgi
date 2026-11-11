"""Read-only production impact measurement for the branch property-address helper."""
from __future__ import annotations

import base64
from collections import Counter, defaultdict
import json
import os

from sqlalchemy import select, text

from bankrotai.db import ProcessedLot, SessionLocal


def _load_branch_helper() -> None:
    encoded = os.environ.get("PROPERTY_ADDRESS_HELPER_B64", "")
    if not encoded:
        raise RuntimeError("PROPERTY_ADDRESS_HELPER_B64 is required")
    source = base64.b64decode(encoded).decode("utf-8")
    exec(compile(source, "property_address_evidence.py", "exec"), globals())


def main() -> dict:
    _load_branch_helper()
    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        rows = session.execute(
            select(
                ProcessedLot.id,
                ProcessedLot.source_system,
                ProcessedLot.title,
                ProcessedLot.description,
                ProcessedLot.cadastral_number,
            ).where(
                ProcessedLot.duplicate_of_id.is_(None),
                ProcessedLot.is_archived.is_(False),
                ProcessedLot.auction_status.in_(("active", "scheduled")),
                ProcessedLot.current_geo_lat.is_(None),
                ProcessedLot.current_geo_lon.is_(None),
                ProcessedLot.address.is_(None),
            )
        ).all()

    repairable: Counter[str] = Counter()
    samples: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        candidate = extract_asset_property_address(
            title=row.title,
            description=row.description,
            cadastral_number=row.cadastral_number,
        )
        if not candidate:
            continue
        source = str(row.source_system or "unknown")
        repairable[source] += 1
        if len(samples[source]) < 5:
            samples[source].append(int(row.id))

    return {
        "report": "property_address_helper_impact",
        "read_only": True,
        "database_writes": 0,
        "eligible_without_stored_address": len(rows),
        "repairable_total": sum(repairable.values()),
        "repairable_by_source": dict(repairable.most_common()),
        "sample_lot_ids_by_source": {
            key: value for key, value in sorted(samples.items())
        },
    }


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, separators=(",", ":")))
