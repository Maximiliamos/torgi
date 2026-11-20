"""Bounded ETP sync specs which are never complete or archive-safe."""
from __future__ import annotations

from bankrotai.connectors.registry.etp_incremental import EtpIncrementalFilters
from bankrotai.connectors.registry.cdtrf_sync import CdtrfSyncFilters
from bankrotai.connectors.registry.mets_sync import MetsSyncFilters
from bankrotai.connectors.etp_sources.common import EtpSearchFilters
from bankrotai.services.ingestion import SourceSyncSpec

ADDITIONAL_ETP_SOURCE_IDS = (
    "alfalot.ru", "arbbitlot.ru", "aukcioncenter.ru", "centerr.ru", "etpu.ru",
    "fabrikant.ru", "nistp.ru", "rus-on.ru", "sberbank-ast.ru", "sistematorg.com",
    "utpl.ru", "vertrades.ru", "zakazrf.ru",
)
LARGE_ASPNET_SOURCE_IDS = frozenset({"arbbitlot.ru", "etpu.ru", "utpl.ru"})

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
    """Five-page bounded trial, not complete-source or archive-safe.

    Called by the approved schedule and available to manual operators. Each
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


def scheduled_etp_source_specs() -> tuple[SourceSyncSpec, ...]:
    """Full selected-catalogue 09:00/21:00 ETP walks.

    These runs prove and exhaust each selected paginator, but intentionally
    never claim complete provider inventory and never reconcile or archive
    missing lots. NISTP keeps its active-row/detail eligibility gates.
    Alfalot uses the browser-verified public GD sales servlet, with individual
    card validation. Every additional ETP remains non-reconciling.
    """
    return (
        SourceSyncSpec(
            "m-ets.ru", MetsSyncFilters(),
            reconcile_missing=False, max_batches=500, require_source_end=True,
        ),
        SourceSyncSpec(
            "cdtrf.ru", CdtrfSyncFilters(page_size=100),
            reconcile_missing=False, max_batches=500, require_source_end=True,
        ),
        *(SourceSyncSpec(
            source, EtpSearchFilters(page=1), reconcile_missing=False,
            # These providers expose more than 500 real catalogue pages. The
            # budget remains bounded; success still requires their actual end.
            max_batches=(6000 if source in LARGE_ASPNET_SOURCE_IDS else
                         3500 if source in {"rus-on.ru", "sistematorg.com"} else 500),
            require_source_end=True,
        ) for source in ADDITIONAL_ETP_SOURCE_IDS),
    )
