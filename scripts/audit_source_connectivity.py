from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

if sys.platform == "win32":
    import truststore

    truststore.inject_into_ssl()

from bankrotai.connectors.registry import connector_registry
from bankrotai.services.ingestion import default_source_specs


async def audit_source(source_id: str, filters: Any, timeout: float) -> dict[str, Any]:
    connector = connector_registry.create(source_id)
    started = time.perf_counter()
    try:
        page = await asyncio.wait_for(connector.search(filters), timeout=timeout)
    except Exception as exc:  # evidence command must report every source independently
        return {
            "source": source_id,
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    metadata = dict(page.metadata)
    return {
        "source": source_id,
        "status": "bounded_page_ok",
        "items": len(page.items),
        "next_cursor": page.next_cursor,
        "metadata": metadata,
        "sample": [
            {
                "external_id": lot.external_id,
                "status": lot.auction_status,
                "has_title": bool(lot.title),
                "has_price": lot.current_price is not None or lot.start_price is not None,
                "has_image": bool((lot.raw_data or {}).get("image_urls") or (lot.raw_data or {}).get("images")),
            }
            for lot in page.items[:5]
        ],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


async def main_async(timeout: float) -> list[dict[str, Any]]:
    results = []
    for spec in default_source_specs():
        results.append(await audit_source(spec.source_id, spec.filters, timeout))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only bounded connectivity check for auction sources")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--output")
    args = parser.parse_args()
    rendered = json.dumps(asyncio.run(main_async(max(5.0, args.timeout))), ensure_ascii=False, indent=2, default=str)
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
