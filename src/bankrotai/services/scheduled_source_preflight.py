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


def _probe_nistp() -> dict[str, Any]:
    from bankrotai.connectors.etp_sources.common import EtpSearchFilters
    from bankrotai.connectors.registry import connector_registry
    from bankrotai.services.etp_nistp_schedule_guard import (
        catalogue_row_eligible_for_detail, independent_detail_proven,
    )

    async def run() -> dict[str, Any]:
        connector = connector_registry.create("nistp.ru")
        page = await connector.search(EtpSearchFilters(page=1), None)
        candidates = [
            lot for lot in page.items if catalogue_row_eligible_for_detail(lot)
        ][:2]
        if not candidates:
            raise RuntimeError("NISTP preflight found no eligible catalogue rows")
        for lot in candidates:
            try:
                await connector.enrich_lot(lot)
            except Exception:
                continue
            if independent_detail_proven(lot):
                return {
                    "success": True,
                    "source_system": "nistp.ru",
                    "verified_details": 1,
                    "external_id": lot.external_id,
                }
        raise RuntimeError("NISTP preflight verified zero independent details")

    return asyncio.run(run())


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
