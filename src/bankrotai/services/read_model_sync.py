from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from bankrotai.domain import NormalizedLot
from bankrotai.logic import persist_lot, reconcile_cross_source_duplicates
from bankrotai.scrapers import (
    LotOnlineClient,
    LotOnlineSearchFilters,
    TBankrotClient,
    TBankrotSearchFilters,
    TorgiGovClient,
    TorgiGovSearchFilters,
)
from bankrotai.services.quality import update_source_health
from bankrotai.services.quality import record_diagnostic


def sync_read_model(
    session_factory: Callable[[], Any],
    *,
    max_items_per_source: int = 5000,
) -> dict[str, int]:
    sources = (
        (
            "ГИС Торги",
            TorgiGovClient(diagnostics=True),
            TorgiGovSearchFilters(
                type_transaction="SALE",
                category_code=TorgiGovClient.REAL_ESTATE_CATEGORY_CODES,
                lot_status=TorgiGovClient.DEFAULT_LOT_STATUS,
                page=1,
                page_size=100,
            ),
        ),
        (
            "TBankrot",
            TBankrotClient(diagnostics=True),
            TBankrotSearchFilters(
                category_codes=TBankrotClient.REAL_ESTATE_CATEGORY_CODES,
                page=1,
                page_size=100,
            ),
        ),
        (
            "РАД / ЛОТ-ОНЛАЙН",
            LotOnlineClient(diagnostics=True),
            LotOnlineSearchFilters(
                category_id=LotOnlineClient.DEFAULT_CATEGORY_ID,
                archive_mode="false",
                page=1,
                page_size=96,
            ),
        ),
    )
    counts: dict[str, int] = {}
    failures: dict[str, str] = {}
    for name, client, filters in sources:
        with session_factory() as session:
            update_source_health(session, name, status="running")
        try:
            def persist_page(lots: list[NormalizedLot], _metadata: dict) -> None:
                with session_factory() as page_session:
                    for lot in lots:
                        persist_lot(page_session, lot)

            kwargs = {"max_items": max_items_per_source, "page_cb": persist_page}
            if isinstance(client, (TorgiGovClient, LotOnlineClient)):
                kwargs["max_pages"] = 500
            lots, _metadata = client.search_all_lots(filters, **kwargs)
            counts[name] = len(lots)
            with session_factory() as session:
                update_source_health(session, name, status="healthy", items_seen=len(lots))
        except Exception as exc:
            failures[name] = str(exc)
            with session_factory() as session:
                update_source_health(session, name, status="failed", error=str(exc))
    with session_factory() as session:
        reconcile_cross_source_duplicates(session)
    if failures:
        detail = "; ".join(f"{name}: {message}" for name, message in failures.items())
        with session_factory() as session:
            record_diagnostic(
                session,
                severity="error",
                component="scheduled_sync",
                message="Read-model sync incomplete",
                context={"failures": failures, "successful_sources": counts},
            )
        raise RuntimeError(f"Read-model sync incomplete: {detail}")
    return counts
