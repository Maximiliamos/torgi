"""Opt-in read-only connector facades for direct-ETP pilot sources.

These are discoverable in the connector registry, but deliberately cannot be
used as an ingestion search: no complete catalogues or missing-lot reconciliation
have been verified. The preview returns *candidate evidence* only, not auction
records and never writes to PostgreSQL, journals, geocoding queues or S3.
"""
from __future__ import annotations

import asyncio

import requests

from bankrotai.connectors.base import (
    AuctionConnector, ConnectorCapabilityError, ConnectorHealth, ConnectorPage,
)
from bankrotai.connectors.registry.direct_etp_catalogue import (
    EtpCatalogError, PROFILE_BY_ID, fetch_catalogue, parse_catalogue,
)

PILOT_SOURCE_IDS = (
    "cdtrf.ru",
    "nistp.ru",
    "alfalot.ru",
    "rts-tender.ru",
    "fabrikant.ru",
    "arbbitlot.ru",
    "etpu.ru",
    "sistematorg.com",
    "centerr.ru",
    "aukcioncenter.ru",
    "vertrades.ru",
    "rus-on.ru",
    "zakazrf.ru",
    "utpl.ru",
)


class DirectEtpPilotConnector(AuctionConnector):
    capabilities = frozenset({"read_only_pilot", "catalogue_preview"})

    def __init__(self, source_id: str, session: requests.Session | None = None) -> None:
        if source_id not in PILOT_SOURCE_IDS:
            raise ValueError(f"Not an approved read-only pilot: {source_id}")
        self.source_id = source_id
        self.profile = PROFILE_BY_ID[source_id]
        self.session = session or requests.Session()

    async def search(self, filters=None, cursor: str | None = None) -> ConnectorPage:
        raise ConnectorCapabilityError(
            f"{self.source_id}: complete pagination/detail/status contracts unverified; "
            "production ingestion and archival are disabled"
        )

    def _preview(self, limit: int) -> dict:
        if not 1 <= limit <= 20:
            raise ValueError("Read-only preview limit must be 1..20")
        html = fetch_catalogue(self.profile, session=self.session)
        observations = parse_catalogue(self.profile, html, limit=limit)
        return {
            "source": self.source_id,
            "catalogue_url": self.profile.catalogue_url,
            "status": "candidates_unverified" if observations else "no_verified_candidates",
            "candidates": observations,
            "inspected": len(observations),
            "complete_source_inventory": False,
            "safe_for_archival": False,
            "ready_for_ingestion": False,
            "database_writes": 0,
        }

    async def preview(self, *, limit: int = 10) -> dict:
        return await asyncio.to_thread(self._preview, limit)

    async def healthcheck(self) -> ConnectorHealth:
        return ConnectorHealth(
            status="pilot",
            message="Read-only ETP candidate extraction; no production ingestion",
            metadata={
                "source_id": self.source_id,
                "catalogue_strategy": self.profile.strategy,
                "full_inventory_verified": False,
                "can_reconcile_missing": False,
            },
        )
