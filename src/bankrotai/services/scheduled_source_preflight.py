"""Bounded fail-fast parser/detail gate for scheduled source sweeps."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests


def _probe_mets() -> dict[str, Any]:
    from bankrotai.connectors.registry.mets_public import MetsPublicPilot
    from bankrotai.connectors.registry.mets_sync import MetsSourceCrawler, MetsSyncFilters

    session = requests.Session()
    try:
        crawler = MetsSourceCrawler(MetsPublicPilot(session=session))
        page = asyncio.run(crawler.search(MetsSyncFilters(), None))
        verified = len(page.items) + int(page.metadata.get("detail_skipped_unchanged") or 0)
        if verified < 1:
            raise RuntimeError("M-ETS preflight verified zero property details")
        return {
            "success": True,
            "source_system": "m-ets.ru",
            "verified_details": verified,
            "catalogue_page": 1,
        }
    finally:
        session.close()


def _probe_cdtrf() -> dict[str, Any]:
    from bankrotai.services.etp_cdtrf_canary import (
        fetch_verified_cdtrf_lot, preview_cdtrf_public_api,
    )

    session = requests.Session()
    try:
        preview = preview_cdtrf_public_api(1, session=session)
        candidates = list(preview.get("candidates") or [])[:5]
        if not candidates:
            raise RuntimeError("CDTRF preflight found no property candidates")
        failures: list[str] = []
        for hit in candidates:
            try:
                lot = fetch_verified_cdtrf_lot(
                    str(hit["trade_id"]), str(hit["trade_lot_id"]), session=session,
                )
                if lot.category == "land":
                    failures.append("standalone_land_out_of_scope")
                    continue
                return {
                    "success": True,
                    "source_system": "cdtrf.ru",
                    "verified_details": 1,
                    "external_id": lot.external_id,
                }
            except Exception as exc:
                failures.append(type(exc).__name__)
        raise RuntimeError(
            "CDTRF preflight verified zero details: " + ",".join(failures)
        )
    finally:
        session.close()


async def _probe_nistp_connector(
    connector: Any,
    *,
    max_pages: int = 5,
    max_detail_attempts: int = 10,
) -> dict[str, Any]:
    """Find one independently proven live NISTP lot with bounded sampling.

    Page 1 can legitimately contain catalogue rows whose shared trade cards do
    not prove the individual asset. Those rows are conservative rejects, not
    proof that the whole source is unavailable.
    """
    from bankrotai.connectors.etp_sources.common import EtpSearchFilters
    from bankrotai.services.etp_nistp_schedule_guard import (
        catalogue_row_eligible_for_detail, independent_detail_proven,
    )

    if not 1 <= max_pages <= 10:
        raise ValueError("NISTP preflight max_pages must be 1..10")
    if not 1 <= max_detail_attempts <= 20:
        raise ValueError("NISTP preflight max_detail_attempts must be 1..20")

    cursor: str | None = None
    pages_checked = 0
    eligible_seen = 0
    detail_attempts = 0
    failures: list[str] = []
    for page_number in range(1, max_pages + 1):
        page = await connector.search(EtpSearchFilters(page=page_number), cursor)
        pages_checked += 1
        for lot in page.items:
            if not catalogue_row_eligible_for_detail(lot):
                continue
            eligible_seen += 1
            if detail_attempts >= max_detail_attempts:
                break
            detail_attempts += 1
            try:
                enriched = await connector.enrich_lot(lot)
            except Exception as exc:
                failures.append(type(exc).__name__)
                continue
            candidate = enriched if enriched is not None else lot
            if independent_detail_proven(candidate):
                return {
                    "success": True,
                    "source_system": "nistp.ru",
                    "verified_details": 1,
                    "external_id": candidate.external_id,
                    "pages_checked": pages_checked,
                    "eligible_seen": eligible_seen,
                    "detail_attempts": detail_attempts,
                }
            failures.append("independent_detail_not_proven")

        if detail_attempts >= max_detail_attempts or not page.next_cursor:
            break
        cursor = page.next_cursor
        await asyncio.sleep(0.25)

    if eligible_seen == 0:
        raise RuntimeError(
            f"NISTP preflight found no eligible catalogue rows across {pages_checked} page(s)"
        )
    failure_summary = ",".join(failures[-10:]) or "none"
    raise RuntimeError(
        "NISTP preflight verified zero independent details: "
        f"pages={pages_checked} eligible={eligible_seen} "
        f"attempted={detail_attempts} failures={failure_summary}"
    )


def _probe_nistp() -> dict[str, Any]:
    from bankrotai.connectors.registry import connector_registry

    connector = connector_registry.create("nistp.ru")
    return asyncio.run(_probe_nistp_connector(connector))

def _probe_one(source_id: str) -> dict[str, Any]:
    from bankrotai.services.source_resilience import probe_source_endpoint

    reachability = probe_source_endpoint(source_id, timeout_seconds=5.0)
    if not bool(reachability.get("success")):
        return reachability
    if source_id == "m-ets.ru":
        return _probe_mets()
    if source_id == "cdtrf.ru":
        return _probe_cdtrf()
    if source_id == "nistp.ru":
        return _probe_nistp()
    return reachability


def run_scheduled_source_preflight(specs: tuple[Any, ...]) -> dict[str, Any]:
    """Prove bounded live access before creating a heavy scheduled run."""
    source_ids = tuple(dict.fromkeys(str(spec.source_id) for spec in specs))
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(source_ids)))) as executor:
        futures = {executor.submit(_probe_one, source_id): source_id for source_id in source_ids}
        for future in as_completed(futures):
            source_id = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "success": False,
                    "source_system": source_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
            results[source_id] = result
    failed = sorted(
        source_id for source_id, result in results.items()
        if not bool(result.get("success"))
    )
    return {
        "success": not failed,
        "checked": len(source_ids),
        "failed_sources": failed,
        "results": results,
    }
