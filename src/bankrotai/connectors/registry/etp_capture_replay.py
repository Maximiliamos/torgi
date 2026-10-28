"""Replay browser evidence for direct ETP pilots without production mutations.

The output is a *diagnostic*, not a normalized auction inventory. The capture
contains personal data: no raw HTML, names, network bodies or screenshots are
copied to the report. Never use the report to reconcile missing source lots.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from bankrotai.connectors.registry.direct_etp_catalogue import (
    EtpCatalogError,
    PROFILE_BY_ID,
    PROFILES,
    parse_catalogue,
)

MAX_HTML_BYTES = 4 * 1024 * 1024
MAX_SUMMARY_BYTES = 2 * 1024 * 1024
MAX_FILES = 15_000
MAX_ARCHIVE_BYTES = 600 * 1024 * 1024
PROVEN_TYPES = frozenset({"catalogue_with_details", "catalogue_only"})
ALLOWED_DOMAINS = frozenset(PROFILE_BY_ID)


class CaptureEvidenceError(ValueError):
    """Invalid or misleading saved ETP evidence."""


def _read_member(archive: zipfile.ZipFile, member: str, limit: int) -> bytes:
    try:
        meta = archive.getinfo(member)
    except KeyError as exc:
        raise CaptureEvidenceError(f"Required capture item missing: {member}") from exc
    if meta.file_size > limit:
        raise CaptureEvidenceError("Capture member exceeds size cap")
    with archive.open(meta) as stream:
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise CaptureEvidenceError("Capture member exceeds size cap")
    return body


def _locate_summary(archive: zipfile.ZipFile) -> tuple[str, dict]:
    members = archive.infolist()
    if len(members) > MAX_FILES or sum(x.file_size for x in members) > MAX_ARCHIVE_BYTES:
        raise CaptureEvidenceError("Capture exceeds bounded offline inspection limits")
    candidates = [x.filename for x in members if re.fullmatch(r"[^/]+/summary\.json", x.filename)]
    if len(candidates) != 1:
        raise CaptureEvidenceError("Expected exactly one capture root/summary.json")
    name = candidates[0]
    data = json.loads(_read_member(archive, name, MAX_SUMMARY_BYTES))
    if not isinstance(data, dict) or not isinstance(data.get("sites"), list):
        raise CaptureEvidenceError("Malformed capture summary")
    return name.removesuffix("summary.json"), data


def _candidate_page(archive: zipfile.ZipFile, root: str, source: str, index: int) -> str:
    name = f"{root}{source}/pages/{index:03d}_catalogue_page_{index - 1}/page.html"
    return _read_member(archive, name, MAX_HTML_BYTES).decode("utf-8", "replace")


def _observation(source: str, html: str) -> list[dict]:
    profile = PROFILE_BY_ID[source]
    if source == "m-ets.ru":
        from bankrotai.connectors.registry.mets_public import discover_lot_urls

        return [
            {"catalogue_detail_link": url, "external_id": url.rsplit("/", 1)[-1],
             "ready_for_ingestion": False}
            for url in discover_lot_urls(html, limit=50)
        ]
    if profile.strategy in {"existing", "research"}:
        return []
    return parse_catalogue(profile, html, limit=100)


def _identities(rows: list[dict]) -> set[str]:
    return {
        str(row["external_id"] or row["catalogue_detail_link"])
        for row in rows if row.get("external_id") or row.get("catalogue_detail_link")
    }


def _safe_url(link: str, source: str) -> bool:
    parsed = urlsplit(link)
    host = urlsplit(PROFILE_BY_ID[source].catalogue_url).hostname
    return (parsed.scheme == "https" and parsed.hostname == host
            and parsed.username is None and parsed.password is None
            and parsed.port is None)


def inspect_capture(path: str | Path) -> dict:
    """Check page identities and evidence gaps; never extract a ZIP to disk."""
    sites: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        root, summary = _locate_summary(archive)
        seen_sites = set()
        for row in summary["sites"]:
            if not isinstance(row, dict):
                raise CaptureEvidenceError("Malformed source summary row")
            source = row.get("site")
            if source not in ALLOWED_DOMAINS or source in seen_sites:
                raise CaptureEvidenceError("Unrecognized or duplicated ETP source")
            seen_sites.add(source)
            status = row.get("status")
            result = {
                "source": source, "capture_status": status,
                "captured_detail_pages": int(row.get("details_saved") or 0),
                "captured_api_bodies": int(row.get("api_bodies_saved") or 0),
                "page_2_claimed": bool(row.get("distinct_pages")),
                "catalogue_candidates": 0,
                "page_2_distinct_in_parser": False,
                "source_complete": False,
                "production_ready": False,
                "can_reconcile_missing": False,
                "issues": [],
            }
            if status not in PROVEN_TYPES:
                result["issues"].append("catalogue_unavailable_or_unverified")
                sites.append(result)
                continue
            try:
                page1 = _candidate_page(archive, root, source, 2)
                items1 = _observation(source, page1)
                ids1 = _identities(items1)
                if any(not _safe_url(str(item["catalogue_detail_link"]), source)
                       for item in items1 if item.get("catalogue_detail_link")):
                    raise CaptureEvidenceError("Unsafe cross-site candidate link")
                result["catalogue_candidates"] = len(ids1)
                if len(ids1) != len(items1):
                    result["issues"].append("duplicated_or_missing_lot_identity")
                if not ids1:
                    result["issues"].append("no_verified_parser_candidates")
                if result["page_2_claimed"]:
                    page2 = _candidate_page(archive, root, source, 3)
                    if hashlib.sha256(page1.encode()).digest() == hashlib.sha256(page2.encode()).digest():
                        result["issues"].append("catalogue_page_2_is_identical")
                    else:
                        items2 = _observation(source, page2)
                        ids2 = _identities(items2)
                        if ids2 and not (ids1 & ids2):
                            result["page_2_distinct_in_parser"] = True
                        else:
                            result["issues"].append("page_2_identities_not_proven")
                else:
                    result["issues"].append("page_2_not_captured")
            except (CaptureEvidenceError, EtpCatalogError, ValueError, KeyError) as exc:
                result["issues"].append("capture_validation_failed:" + type(exc).__name__)
            # Catalogue-only evidence never authorizes a DB write or archival.
            result["issues"].append("lot_details_and_status_require_live_acceptance")
            sites.append(result)
    return {
        "mode": "read_only_etp_browser_capture_replay",
        "source_count": len(sites),
        "expected_source_count": len(PROFILES),
        "complete_source_inventory": False,
        "database_writes": 0,
        "archive_or_map_publication_allowed": False,
        "sources": sites,
    }
