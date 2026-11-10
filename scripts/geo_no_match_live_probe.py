"""Bounded read-only probe for stale cadastral no-match failures."""
from __future__ import annotations

import json
import re
import sys

from sqlalchemy import select, text

from bankrotai.db import GeoFailure, ProcessedLot, SessionLocal
from bankrotai.geo import CADASTRAL_GEOCODER, GeoProviderUnavailable, NSPDTLSVerificationError


LOT_IDS = (
    5513, 5906,      # torgi-russia
    10138, 11046,    # torgi.gov
    331622, 331767,  # m-ets
    5, 20,           # tbankrot
    35750, 36152,    # bidexpert
)
CAD = re.compile(r"^\d{2}:\d{1,2}:\d{5,9}:\d{1,12}$")


def main() -> dict:
    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        rows = session.execute(
            select(
                ProcessedLot.id,
                ProcessedLot.source_system,
                ProcessedLot.cadastral_number,
                ProcessedLot.region_code,
                GeoFailure.status,
                GeoFailure.error_message,
            )
            .outerjoin(GeoFailure, GeoFailure.lot_id == ProcessedLot.id)
            .where(ProcessedLot.id.in_(LOT_IDS))
        ).all()

    results = []
    for lot_id, source, cadastral, region_code, failure_status, error_message in rows:
        cad = re.sub(r"\s+", "", str(cadastral or ""))
        item = {
            "lot_id": int(lot_id),
            "source_system": str(source or ""),
            "input": "valid_cadastre" if CAD.fullmatch(cad) else ("invalid_cadastre" if cad else "no_cadastre"),
            "cadastral_region": cad.split(":", 1)[0] if ":" in cad else None,
            "stored_region": str(region_code or "") or None,
            "failure_status": str(failure_status or "") or None,
            "failure_class": (
                "no_coordinates"
                if "no validated coordinates" in str(error_message or "").casefold()
                or "no coordinates" in str(error_message or "").casefold()
                else "other"
            ),
        }
        if not CAD.fullmatch(cad):
            item["live_nspd"] = "not_probed"
            results.append(item)
            continue
        try:
            found = CADASTRAL_GEOCODER._search_nspd_geoportal(cad)
        except (GeoProviderUnavailable, NSPDTLSVerificationError) as exc:
            item["live_nspd"] = "operational_failure"
            item["error_type"] = type(exc).__name__
        except Exception as exc:
            item["live_nspd"] = "provider_protocol_failure"
            item["error_type"] = type(exc).__name__
        else:
            observed = re.sub(r"\s+", "", str(found.cadastral_number or "")) if found else ""
            item["live_nspd"] = (
                "exact_match" if observed == cad else ("different_match" if observed else "no_match")
            )
            item["has_coordinates"] = bool(found and found.lat is not None and found.lon is not None)
        results.append(item)

    return {
        "report": "geo_no_match_live_nspd_probe",
        "read_only": True,
        "database_writes": 0,
        "selected": len(LOT_IDS),
        "found_rows": len(rows),
        "results": results,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=False, separators=(",", ":")))
    except Exception as exc:
        print(json.dumps({"report": "geo_no_match_live_nspd_probe", "error_type": type(exc).__name__}), file=sys.stderr)
        raise
