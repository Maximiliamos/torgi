"""Read the actual public Sberbank catalogue; its legacy HEAD route returns 500."""
from __future__ import annotations

import asyncio
from typing import Any


async def _probe(connector: Any) -> dict[str, Any]:
    from bankrotai.connectors.etp_sources.common import EtpSearchFilters

    page = await connector.search(EtpSearchFilters(), None)
    if not page.items or len({lot.external_id for lot in page.items}) != len(page.items):
        raise ValueError("Sberbank catalogue has no unique property sample")
    for lot in page.items[:3]:
        detail = await connector.enrich_lot(lot)
        raw = detail.raw_data or {}
        if (detail.external_id == lot.external_id
                and detail.source_system == "sberbank-ast.ru"
                and detail.start_price is not None and detail.start_price > 0
                and raw.get("detail_identity_verified") is True
                and raw.get("detail_start_price_verified") is True
                and raw.get("detail_status_verified") is True):
            return {"success": True, "source_system": "sberbank-ast.ru",
                    "http_status": 200, "validated_lots": len(page.items),
                    "verified_details": 1, "external_id": detail.external_id,
                    "details_or_archive_verified": False}
    raise ValueError("Sberbank catalogue sample has no independently verified detail")


def probe_sberbank_ast_catalogue(*, timeout_seconds: float = 5.0) -> dict[str, Any]:
    from bankrotai.connectors.registry import connector_registry

    connector = connector_registry.create("sberbank-ast.ru")
    connector.timeout = max(5.0, min(20.0, timeout_seconds * 2))
    try:
        return asyncio.run(_probe(connector))
    except Exception as exc:
        return {"success": False, "source_system": "sberbank-ast.ru",
                "error_type": type(exc).__name__,
                "error": "Sberbank public catalogue/detail probe failed: " + type(exc).__name__}
    finally:
        connector.session.close()
