"""Core nationwide source specifications, separate from ETP scheduling gates."""
from __future__ import annotations

from bankrotai.scraper_contracts import (
    BidExpertSearchFilters,
    LotOnlineSearchFilters,
    TBankrotSearchFilters,
    TorgiGovSearchFilters,
    TorgiRussiaSearchFilters,
)
from bankrotai.scrapers import LotOnlineClient, TBankrotClient, TorgiGovClient
from bankrotai.services.ingestion import SourceSyncSpec


def core_source_specs() -> tuple[SourceSyncSpec, ...]:
    core_specs = (
        SourceSyncSpec(
            "torgi.gov.ru",
            TorgiGovSearchFilters(
                type_transaction="SALE",
                category_code=TorgiGovClient.REAL_ESTATE_ROOT_CATEGORY_CODES,
                lot_status=TorgiGovClient.DEFAULT_LOT_STATUS,
                page=1,
                page_size=100,
            ),
        ),
        SourceSyncSpec(
            "tbankrot.ru",
            TBankrotSearchFilters(
                category_codes=TBankrotClient.REAL_ESTATE_CATEGORY_CODES,
                page=1,
                page_size=100,
            ),
        ),
        SourceSyncSpec(
            "lot-online.ru",
            LotOnlineSearchFilters(
                category_id=LotOnlineClient.DEFAULT_CATEGORY_ID,
                archive_mode="false",
                page=1,
                page_size=96,
            ),
        ),
        SourceSyncSpec(
            "torgi-russia.ru", TorgiRussiaSearchFilters(),
            reconcile_missing=False, require_source_end=True,
        ),
        SourceSyncSpec(
            "bidexpert.ru",
            BidExpertSearchFilters(category="realty", page=1),
        ),
    )
    return core_specs
