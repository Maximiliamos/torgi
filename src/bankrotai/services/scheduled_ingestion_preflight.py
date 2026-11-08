"""Read-only fail-fast probes for the scheduled seven-source refresh."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import requests

from bankrotai.connectors.registry import connector_registry
from bankrotai.connectors.registry.etp_incremental import _property_ready
from bankrotai.connectors.registry.mets_public import (
    BASE,
    MetsPublicPilot,
    parse_mets_lot,
)
from bankrotai.services.etp_cdtrf_canary import (
    fetch_verified_cdtrf_lot,
    preview_cdtrf_public_api,
)
from bankrotai.services.etp_mets_full_catalogue import _parse_page, _request_page
from bankrotai.services.source_resilience import probe_source_endpoint


HEAVY_SOURCES = frozenset({"m-ets.ru", "cdtrf.ru", "nistp.ru"})


def _probe_mets() -> dict[str, Any]:
    with requests.Session() as session:
        pilot = MetsPublicPilot(session=session)
        response = _request_page(session, 1, sleep=lambda _: None, attempts=2)
        parsed = _parse_page(response.text, 1)
        observations = list(parsed["observations"][:3])
        if not observations:
            raise RuntimeError("M-ETS preflight found no catalogue candidates")
        verified = 0
        rejection_codes: dict[str, int] = {}
        for external_id, catalogue_price, catalogue_status, _title in observations:
            try:
                url = f"{BASE}/{external_id}"
                lot = parse_mets_lot(pilot._get(url), url)
                expected_status = (
                    "active"
                    if catalogue_status == "Торги в стадии приема заявок"
                    else "scheduled"
                )
                detail_prices = {
                    Decimal(str(value))
                    for value in (lot.start_price, lot.current_price)
                    if value is not None
                }
                if (
                    lot.external_id != external_id
                    or lot.auction_status != expected_status
                    or Decimal(catalogue_price) not in detail_prices
                    or lot.category == "land"
                    or not _property_ready(lot)
                ):
                    rejection_codes["detail_contract"] = (
                        rejection_codes.get("detail_contract", 0) + 1
                    )
                    continue
                verified += 1
            except Exception as exc:
                code = type(exc).__name__
                rejection_codes[code] = rejection_codes.get(code, 0) + 1
        if verified < 1:
            raise RuntimeError(
                "M-ETS preflight detail verification collapsed: "
                + ",".join(f"{key}={value}" for key, value in sorted(rejection_codes.items()))
            )
        return {
            "source_system": "m-ets.ru",
            "success": True,
            "catalogue_candidates_sampled": len(observations),
            "verified_details": verified,
        }


def _probe_cdtrf() -> dict[str, Any]:
    with requests.Session() as session:
        preview = preview_cdtrf_public_api(1, session=session)
        candidates = list(preview.get("candidates") or [])[:3]
        if not candidates:
            raise RuntimeError("CDTRF preflight found no property candidates")
        verified = 0
        for candidate in candidates:
            lot = fetch_verified_cdtrf_lot(
                str(candidate["trade_id"]),
                str(candidate["trade_lot_id"]),
                session=session,
            )
            if lot.external_id != candidate["external_id"]:
                raise RuntimeError("CDTRF preflight detail identity mismatch")
            if lot.category == "land":
                continue
            verified += 1
        return {
            "source_system": "cdtrf.ru",
            "success": verified > 0,
            "catalogue_candidates_sampled": len(candidates),
            "verified_details": verified,
        }


async def _probe_spec(spec: Any) -> dict[str, Any]:
    source = str(spec.source_id)
    try:
        if source == "m-ets.ru":
            return await asyncio.to_thread(_probe_mets)
        if source == "cdtrf.ru":
            return await asyncio.to_thread(_probe_cdtrf)
        if source == "nistp.ru":
            connector = connector_registry.create(source)
            page = await connector.search(spec.filters, None)
            if not page.items:
                raise RuntimeError("NISTP preflight parsed zero catalogue rows")
            return {
                "source_system": source,
                "success": True,
                "catalogue_rows": len(page.items),
            }
        probe = await asyncio.to_thread(probe_source_endpoint, source)
        return {
            "source_system": source,
            "success": bool(probe.get("success")),
            "probe": probe,
        }
    except Exception as exc:
        return {
            "source_system": source,
            "success": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }


async def _run(specs: tuple[Any, ...]) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(4)

    async def bounded(spec: Any) -> dict[str, Any]:
        async with semaphore:
            return await _probe_spec(spec)

    results = list(await asyncio.gather(*(bounded(spec) for spec in specs)))
    failures = [row for row in results if not row.get("success")]
    return {
        "passed": not failures,
        "checked": len(results),
        "failures": failures,
        "results": results,
        "database_writes": 0,
    }


def run_scheduled_source_preflight(specs: tuple[Any, ...]) -> dict[str, Any]:
    return asyncio.run(_run(specs))


def scheduled_preflight_skip(mode: str, specs: tuple[Any, ...]) -> dict[str, Any] | None:
    if mode not in {"full", "incremental"}:
        return None
    preflight = run_scheduled_source_preflight(specs)
    if preflight.get("passed"):
        return None
    return {
        "status": "skipped",
        "reason": "scheduled_source_preflight_failed",
        "mode": mode,
        "preflight": preflight,
    }
