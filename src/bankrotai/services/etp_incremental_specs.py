"""Explicit, opt-in two-page ETP sync specs, never default or archive-safe."""
from __future__ import annotations

from bankrotai.connectors.registry.etp_incremental import EtpIncrementalFilters
from bankrotai.services.ingestion import SourceSyncSpec


def etp_incremental_source_specs() -> tuple[SourceSyncSpec, ...]:
    """A bounded two-page property window for approved manual source pilots.

    NOT a complete-source scan. Never use missing-lot reconciliation here.
    """
    return (
        SourceSyncSpec(
            "m-ets.ru", EtpIncrementalFilters("m-ets.ru", max_detail_requests=3),
            reconcile_missing=False, max_batches=2,
        ),
        SourceSyncSpec(
            "cdtrf.ru", EtpIncrementalFilters("cdtrf.ru", max_detail_requests=3),
            reconcile_missing=False, max_batches=2,
        ),
    )


def etp_five_page_source_specs() -> tuple[SourceSyncSpec, ...]:
    """Opt-in five-page trial, still not complete-source or archive-safe.

    Excludes these sources from the normal default polling scheduler. Each
    page validates at most two details; a caller must separately prove source
    coverage and never reconcile missing lots based on this finite window.
    """
    return (
        SourceSyncSpec(
            "m-ets.ru",
            EtpIncrementalFilters("m-ets.ru", max_detail_requests=2, max_pages=5),
            reconcile_missing=False, max_batches=5,
        ),
        SourceSyncSpec(
            "cdtrf.ru",
            EtpIncrementalFilters("cdtrf.ru", max_detail_requests=2, max_pages=5),
            reconcile_missing=False, max_batches=5,
        ),
    )
