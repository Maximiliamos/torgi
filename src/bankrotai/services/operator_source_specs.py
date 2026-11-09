"""Operator-only full source specifications kept outside the ingestion core."""
from __future__ import annotations

from typing import Any


def operator_source_full_specs(
    source_id: str,
    default_specs: tuple[Any, ...],
) -> tuple[Any, ...]:
    if source_id == "cdtrf.ru":
        from bankrotai.connectors.registry.cdtrf_sync import operator_sync_spec

        return (operator_sync_spec(),)

    if source_id in {"rus-on.ru", "sistematorg.com"}:
        from bankrotai.connectors.etp_sources import EtpSearchFilters
        from bankrotai.services.ingestion_models import SourceSyncSpec

        return (
            SourceSyncSpec(
                source_id,
                EtpSearchFilters(page=1),
                reconcile_missing=False,
                max_batches=3500,
                require_source_end=True,
            ),
        )

    specs = tuple(spec for spec in default_specs if spec.source_id == source_id)
    if specs:
        return specs
    raise ValueError(f"Unsupported source-only full sync: {source_id}")
