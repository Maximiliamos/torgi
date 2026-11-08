"""Bounded fail-fast reachability gate for scheduled source sweeps."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from bankrotai.services.source_resilience import probe_source_endpoint


def run_scheduled_source_preflight(specs: tuple[Any, ...]) -> dict[str, Any]:
    """Probe each selected source in parallel before creating a heavy run."""
    source_ids = tuple(dict.fromkeys(str(spec.source_id) for spec in specs))
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(source_ids)))) as executor:
        futures = {
            executor.submit(
                probe_source_endpoint, source_id, timeout_seconds=5.0
            ): source_id
            for source_id in source_ids
        }
        for future in as_completed(futures):
            source_id = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "success": False,
                    "source_system": source_id,
                    "error": str(exc)[:500],
                }
            results[source_id] = result
    failed = sorted(
        source_id
        for source_id, result in results.items()
        if not bool(result.get("success"))
    )
    return {
        "success": not failed,
        "checked": len(source_ids),
        "failed_sources": failed,
        "results": results,
    }
