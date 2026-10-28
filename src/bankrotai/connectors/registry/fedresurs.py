"""Official EFRSB publications adapter.

EFRSB trade messages are *events*, not auction lots. This adapter exposes
read-only event lookup while refusing unsafe auction-card persistence.
"""
from __future__ import annotations

import asyncio
from typing import Any

from bankrotai.connectors.base import (
    AuctionConnector,
    ConnectorCapabilityError,
    ConnectorHealth,
    ConnectorPage,
)
from bankrotai.connectors.registry.fedresurs_publications import FedresursPublicationsClient


class FedresursConnector(AuctionConnector):
    source_id = "fedresurs.ru"
    capabilities = frozenset({"publications", "fetch_events", "fetch_documents", "healthcheck"})

    def __init__(
        self,
        *,
        login: str | None = None,
        password: str | None = None,
        base_url: str | None = None,
        session: Any = None,
    ) -> None:
        self.publications = FedresursPublicationsClient(
            login=login, password=password, base_url=base_url, session=session
        )

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        raise ConnectorCapabilityError(
            "EFRSB publications are not individual sale lots. "
            "Use read-only collect_publications until verified XML lot extraction exists."
        )

    async def fetch_lot(self, external_id: str) -> Any:
        raise ConnectorCapabilityError(
            "EFRSB message GUID is not a lot identifier; no synthetic lots may be created"
        )

    async def collect_publications(
        self,
        *,
        kind: str,
        start_date: str,
        end_date: str,
        page_size: int = 100,
        max_pages: int = 2,
        message_types: str | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.publications.collect_bounded,
            kind=kind, start_date=start_date, end_date=end_date,
            page_size=page_size, max_pages=max_pages, message_types=message_types,
        )

    async def fetch_events(self, external_id: str) -> list[dict[str, Any]]:
        message = await asyncio.to_thread(
            self.publications.fetch_message, "trade-messages", external_id
        )
        return [message]

    async def fetch_documents(self, external_id: str) -> list[dict[str, Any]]:
        # File archives can be large: return the official authorized download
        # reference without fetching/attaching it to public auction cards.
        from bankrotai.connectors.registry.fedresurs_publications import _guid
        return [{
            "kind": "archive",
            "url": (
                f"{self.publications.base_url}/v1/trade-messages/"
                f"{_guid(external_id)}/files/archive"
            ),
            "requires_authorization": True,
        }]

    async def healthcheck(self) -> ConnectorHealth:
        try:
            await asyncio.to_thread(self.publications.authenticate)
            return ConnectorHealth(status="healthy", message="EFRSB official API authentication succeeded")
        except Exception as exc:
            # Never return raw HTTP response bodies, auth details or tokens.
            return ConnectorHealth(
                status="unavailable",
                message=f"EFRSB authentication failed ({type(exc).__name__})",
            )
