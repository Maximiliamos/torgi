"""Read-only acceptance audit for the 22 direct ETP sources.

This does not scrape providers, write database rows or certify a source on its own.
It validates independently captured catalogue/detail/DB/map evidence. A reviewer
must still confirm that captured source cards match the live official website.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from bankrotai.connectors.etp_sources import PROFILES

PROFILES_BY_ID = {profile.source_id: profile for profile in PROFILES}
SOURCE_IDS = frozenset((*PROFILES_BY_ID, "lot-online.ru"))
FIELDS = ("title", "start_price", "current_price", "auction_status", "cadastral_number")
CADASTRE = re.compile(r"^\d{2}:\d{2}:(?:\d{1,7}:){1,3}\d{1,7}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _amount(value: object) -> Decimal | None:
    """No binary floats or ambiguous/localized amounts in acceptance evidence."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("price must be an exact decimal string")
    cleaned = str(value).strip()
    if not re.fullmatch(r"\d{1,14}(?:\.\d{1,2})?", cleaned):
        raise ValueError("price must be positive RUB amount with up to 2 decimals")
    amount = Decimal(cleaned)
    if amount <= 0 or amount >= Decimal("100000000000000"):
        raise ValueError("out-of-bounds price")
    return amount


def _index(rows: object, field: str, label: str, errors: list[str]) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    if not isinstance(rows, list):
        errors.append(f"{label}: expected array")
        return indexed
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get(field), str) or not row[field]:
            errors.append(f"{label}[{index}]: missing {field}")
            continue
        key = row[field]
        if key in indexed:
            errors.append(f"{label}: duplicate {field}={key}")
        indexed[key] = row
    return indexed


def evaluate_source(report: dict) -> dict:
    """Validate source-level evidence; never infer truth from DB counts alone."""
    errors: list[str] = []
    warnings: list[str] = []
    source = report.get("source_id")
    if source not in SOURCE_IDS:
        return {"source_id": source, "status": "rejected", "errors": ["unregistered ETP"], "warnings": []}
    profile = PROFILES_BY_ID.get(source)
    if profile and profile.blocked_reason:
        errors.append("official public listing/detail contract remains blocked")

    pages = report.get("catalogue_pages")
    total_pages = report.get("total_pages")
    if type(total_pages) is not int or total_pages < 1 or total_pages > 100000:
        errors.append("total_pages: positive verified integer required")
        total_pages = 0
    catalogue_ids: list[str] = []
    if not isinstance(pages, list) or not pages:
        errors.append("catalogue_pages: evidence missing")
        pages = []
    observed_pages: set[int] = set()
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            errors.append(f"page[{index}]: not an object")
            continue
        number = page.get("number")
        if type(number) is not int or number <= 0 or number in observed_pages:
            errors.append(f"page[{index}]: missing/duplicate page number")
        else:
            observed_pages.add(number)
        if not SHA256.fullmatch(str(page.get("snapshot_sha256") or "")):
            errors.append(f"page[{index}]: no captured response hash")
        ids = page.get("lot_ids")
        if not isinstance(ids, list):
            errors.append(f"page[{index}]: no lot_ids")
            continue
        for value in ids:
            if not isinstance(value, str) or not value:
                errors.append(f"page[{index}]: invalid lot identity")
            else:
                catalogue_ids.append(value)
    if total_pages and observed_pages != set(range(1, total_pages + 1)):
        errors.append("page coverage: missing, repeated or out-of-range page")
    if report.get("last_page_proven") is not True:
        errors.append("last page not independently proven")
    if len(catalogue_ids) != len(set(catalogue_ids)):
        errors.append("duplicate/shifted catalogue lot identities across pages")
    if not catalogue_ids:
        errors.append("catalogue contains no verifiable lot identities")
    if source != "lot-online.ru" and any(not value.startswith(source + ":") for value in catalogue_ids):
        errors.append("source-scoped external_id missing")
    catalogue_set = set(catalogue_ids)

    details = _index(report.get("detail_lots"), "external_id", "detail_lots", errors)
    persisted = _index(report.get("database_lots"), "external_id", "database_lots", errors)
    if set(details) != catalogue_set:
        errors.append(f"detail coverage mismatch: missing={len(catalogue_set - set(details))}, unexpected={len(set(details) - catalogue_set)}")
    if set(persisted) != catalogue_set:
        errors.append(f"DB coverage mismatch: missing={len(catalogue_set - set(persisted))}, unexpected={len(set(persisted) - catalogue_set)}")
    allowed_hosts = set(profile.allowed_hosts) if profile else {
        "lot-online.ru", "www.lot-online.ru", "catalog.lot-online.ru"
    }

    for key in catalogue_set & set(details):
        row = details[key]
        uri = row.get("official_url")
        if not isinstance(uri, str):
            errors.append(f"{key}: official detail URL missing")
        else:
            parsed = urlparse(uri)
            if (parsed.scheme != "https" or parsed.hostname not in allowed_hosts or
                parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)):
                errors.append(f"{key}: detail URL not on approved HTTPS host")
        if not SHA256.fullmatch(str(row.get("detail_sha256") or "")):
            errors.append(f"{key}: detail capture hash missing")
        if not str(row.get("title") or "").strip():
            errors.append(f"{key}: detail title missing")
        if row.get("auction_status") not in {"active", "scheduled", "paused", "closed", "cancelled"}:
            errors.append(f"{key}: unknown/unproven individual-lot status")
        number = row.get("cadastral_number")
        if number is not None and not CADASTRE.fullmatch(str(number)):
            errors.append(f"{key}: malformed cadastral number")
        for field in ("start_price", "current_price"):
            try:
                _amount(row.get(field))
            except (ValueError, InvalidOperation):
                errors.append(f"{key}: invalid {field} evidence")
        if key in persisted:
            db_row = persisted[key]
            if db_row.get("source_system") != source:
                errors.append(f"{key}: wrong DB source_system")
            for field in FIELDS:
                expected, actual = row.get(field), db_row.get(field)
                if field in {"start_price", "current_price"}:
                    try:
                        expected, actual = _amount(expected), _amount(actual)
                    except (ValueError, InvalidOperation):
                        errors.append(f"{key}: invalid DB {field}")
                        continue
                if expected != actual:
                    errors.append(f"{key}: detail/DB mismatch in {field}")

    map_rows = _index(report.get("map_lots", []), "external_id", "map_lots", errors)
    for key, row in map_rows.items():
        if key not in catalogue_set:
            errors.append(f"{key}: orphan map marker")
        if row.get("source_system") != source:
            errors.append(f"{key}: map source mismatch")
        lat, lon = row.get("latitude"), row.get("longitude")
        numeric_coordinates = (
            type(lat) in (float, int) and type(lon) in (float, int)
        )
        if not numeric_coordinates or not (
            -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180
        ):
            errors.append(f"{key}: invalid map coordinates")
        if row.get("geo_evidence") not in {"exact_cadastre", "verified_address", "verified_coordinates"}:
            errors.append(f"{key}: map coordinates lack verifiable provenance")
    if len(map_rows) < len(catalogue_set):
        warnings.append(f"without validated map coordinates: {len(catalogue_set) - len(map_rows)}")

    reviewer = report.get("operator_review")
    if not (isinstance(reviewer, dict) and reviewer.get("live_official_cards_checked") is True
            and str(reviewer.get("reviewed_by") or "").strip()
            and str(reviewer.get("checked_at_utc") or "").strip()):
        errors.append("independent operator/live official-card review missing")
    return {
        "source_id": source, "status": "accepted" if not errors else "rejected",
        "pages": len(observed_pages), "catalogue_lots": len(catalogue_set),
        "detail_lots": len(details), "database_lots": len(persisted), "map_lots": len(map_rows),
        "errors": errors, "warnings": warnings,
    }


def evaluate_batch(document: dict) -> dict:
    reports = document.get("sources")
    if not isinstance(reports, list):
        raise ValueError("input must contain sources[]")
    seen: set[str] = set()
    outcomes = []
    for report in reports:
        if not isinstance(report, dict):
            outcomes.append({"status": "rejected", "errors": ["source entry must be an object"]})
            continue
        source = str(report.get("source_id") or "")
        result = evaluate_source(report)
        if source in seen:
            result["status"] = "rejected"
            result["errors"].append("source provided more than once")
        seen.add(source)
        outcomes.append(result)
    return {
        "contract": "etp-22-readonly-audit-v1",
        "accepted": sum(row["status"] == "accepted" for row in outcomes),
        "rejected": sum(row["status"] != "accepted" for row in outcomes),
        "missing_sources": sorted(SOURCE_IDS - seen),
        "results": outcomes,
        "writes_to_production": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only evidence audit for 22 Russian ETP sources")
    parser.add_argument("--input", type=Path, required=True, help="Independent catalogue/detail/DB/map export JSON")
    parser.add_argument("--output", type=Path, help="Write a machine-readable acceptance report")
    args = parser.parse_args()
    document = json.loads(args.input.read_text(encoding="utf-8"))
    results = evaluate_batch(document)
    formatted = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(formatted + "\n", encoding="utf-8")
    print(formatted)
    raise SystemExit(1 if results["rejected"] or results["missing_sources"] else 0)


if __name__ == "__main__":
    main()
