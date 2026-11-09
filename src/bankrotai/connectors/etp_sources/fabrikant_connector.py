from __future__ import annotations

import asyncio
from typing import Any

import requests

from bankrotai.connectors.base import ConnectorPage
from bankrotai.connectors.etp_sources.common import (
    EtpSearchFilters,
    HtmlEtpConnector,
    SourceSchemaError,
    _safe_provider_url,
)
from bankrotai.connectors.etp_sources.fabrikant import (
    FabrikantContractError,
    parse_detail_lot,
    resolve_catalogue_page_lots,
)
from bankrotai.connectors.etp_sources.pagination import has_next_page


class FabrikantConnector(HtmlEtpConnector):
    """Fabrikant procedure catalogue -> canonical individual lot connector."""

    def _read_get(self, url: str) -> requests.Response:
        return self._read_request(
            "get",
            _safe_provider_url(self.profile, url),
            timeout=self.timeout,
            allow_redirects=False,
        )

    def _resolve_page(
        self, response: requests.Response, *, page: int
    ):
        try:
            return resolve_catalogue_page_lots(
                self.profile, response, page=page, read_get=self._read_get
            )
        except FabrikantContractError as exc:
            raise SourceSchemaError(f"fabrikant.ru: {exc}") from exc

    async def search(
        self, filters: Any, cursor: str | None = None
    ) -> ConnectorPage:
        normalized = (
            filters
            if isinstance(filters, EtpSearchFilters)
            else EtpSearchFilters(**(filters or {}))
        )
        page = max(1, int(cursor or normalized.page))
        response = await asyncio.to_thread(self._request, page)
        lots = await asyncio.to_thread(self._resolve_page, response, page=page)
        next_cursor = (
            str(page + 1)
            if lots and has_next_page(self.profile, response.content, page)
            else None
        )
        return ConnectorPage(
            items=lots,
            next_cursor=next_cursor,
            metadata={
                "source": self.source_id,
                "page": page,
                "items_on_page": len(lots),
                "total_items": None,
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
                "maturity": self.profile.status,
                "response_url": response.url,
            },
        )

    async def fetch_lot(self, external_id: str):
        prefix = "fabrikant.ru:"
        if not external_id.startswith(prefix):
            raise SourceSchemaError("fabrikant.ru: external_id has wrong source")
        identity = external_id[len(prefix):]
        parts = identity.split(":", 1)
        if len(parts) != 2 or not all(parts):
            raise SourceSchemaError("fabrikant.ru: invalid procedure/lot identity")
        detail_url = (
            "https://www.fabrikant.ru/v2/trades/procedure/lot/view/"
            f"{parts[0]}/{parts[1]}"
        )
        response = await asyncio.to_thread(self._read_get, detail_url)
        try:
            return parse_detail_lot(
                self.profile,
                response.content,
                detail_url=detail_url,
                external_id=external_id,
            )
        except FabrikantContractError as exc:
            raise SourceSchemaError(f"fabrikant.ru: {exc}") from exc
