from __future__ import annotations

import asyncio
import hashlib
import os
import time
from typing import Any

import requests

from bankrotai.connectors.base import AuctionConnector, ConnectorHealth, ConnectorPage
from bankrotai.domain import NormalizedLot
from bankrotai.logic import classify_category


class FedresursConnector(AuctionConnector):
    """Official EFRSB Publications API connector.

    Production credentials are operator-issued and are never bundled with the
    application. CAPTCHA/browser automation is deliberately not used.
    """

    source_id = "fedresurs.ru"
    capabilities = frozenset({"search", "fetch_lot", "fetch_documents", "fetch_events"})

    def __init__(
        self,
        *,
        login: str | None = None,
        password: str | None = None,
        base_url: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.login = login or os.getenv("FEDRESURS_LOGIN")
        self.password = password or os.getenv("FEDRESURS_PASSWORD")
        self.base_url = (base_url or os.getenv("FEDRESURS_BASE_URL") or "https://bank-publications-prod.fedresurs.ru").rstrip("/")
        self.session = session or requests.Session()
        self.timeout = (5, 30)
        self._token: str | None = None
        self._token_expires_at = 0.0

    def _authenticate(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        if not self.login or not self.password:
            raise RuntimeError("EFRSB credentials are required: set FEDRESURS_LOGIN and FEDRESURS_PASSWORD")
        password_hash = hashlib.sha512(self.password.encode("utf-8")).hexdigest().upper()
        response = self.session.post(
            f"{self.base_url}/v1/auth",
            json={"login": self.login, "passwordHash": password_hash},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("jwt") or payload.get("JWT") or payload.get("token") or payload.get("accessToken")
        if not token:
            raise RuntimeError("EFRSB authentication response did not contain a token")
        self._token = str(token)
        self._token_expires_at = time.time() + 11 * 60 * 60
        return self._token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._authenticate()}", "Accept": "application/json"}

    @staticmethod
    def _pick(payload: dict[str, Any], *keys: str) -> Any:
        lowered = {str(key).casefold(): value for key, value in payload.items()}
        for key in keys:
            value = lowered.get(key.casefold())
            if value not in (None, "", [], {}):
                return value
        return None

    def _normalize(self, payload: dict[str, Any]) -> NormalizedLot:
        guid = self._pick(payload, "guid", "id", "messageGuid", "tradeMessageGuid")
        if not guid:
            raise ValueError("EFRSB trade message has no stable GUID")
        title = str(self._pick(payload, "title", "messageType", "typeName") or f"EFRSB trade message {guid}")
        description = str(self._pick(payload, "description", "content", "text") or "")
        price = self._pick(payload, "currentPrice", "startPrice", "price")
        try:
            numeric_price = float(price) if price is not None else None
        except (TypeError, ValueError):
            numeric_price = None
        return NormalizedLot(
            external_id=str(guid),
            source="fedresurs",
            source_system=self.source_id,
            title=title[:500],
            description=description[:5000],
            category=classify_category(title, description),
            region_slug=None,
            region_name=self._pick(payload, "regionName", "region"),
            address=self._pick(payload, "address", "location"),
            cadastral_number=self._pick(payload, "cadastralNumber"),
            vin=self._pick(payload, "vin"),
            area=None,
            start_price=numeric_price,
            current_price=numeric_price,
            auction_status="unknown",
            lot_url=f"https://bankrot.fedresurs.ru/TradeCard.aspx?ID={guid}",
            source_url=f"{self.base_url}/v1/trade-messages/{guid}",
            detail_level="official_api",
            raw_data=payload,
            efresb_message_number=str(self._pick(payload, "number", "messageNumber") or guid),
            debtor_name=self._pick(payload, "debtorName", "bankruptName"),
            organizer_name=self._pick(payload, "tradeOrganizerName", "organizerName"),
            auction_manager_name=self._pick(payload, "arbitrManagerName", "arbitrationManagerName"),
            bankruptcy_case_number=self._pick(payload, "caseNumber"),
            platform_name=self._pick(payload, "tradePlaceName"),
        )

    def _search_sync(self, filters: Any, cursor: str | None) -> ConnectorPage:
        params = dict(filters or {})
        if cursor:
            params["page"] = cursor
        response = self.session.get(
            f"{self.base_url}/v1/trade-messages",
            params=params,
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            rows, metadata = payload, {}
        else:
            rows = payload.get("items") or payload.get("data") or payload.get("results") or []
            metadata = {key: value for key, value in payload.items() if key not in {"items", "data", "results"}}
        items = [self._normalize(row) for row in rows if isinstance(row, dict)]
        next_cursor = metadata.get("nextCursor") or metadata.get("nextPage")
        return ConnectorPage(items=items, next_cursor=str(next_cursor) if next_cursor else None, metadata=metadata)

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        return await asyncio.to_thread(self._search_sync, filters, cursor)

    def _fetch_lot_sync(self, external_id: str) -> NormalizedLot:
        response = self.session.get(
            f"{self.base_url}/v1/trade-messages/{external_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return self._normalize(response.json())

    async def fetch_lot(self, external_id: str) -> NormalizedLot:
        return await asyncio.to_thread(self._fetch_lot_sync, external_id)

    async def fetch_documents(self, external_id: str) -> list[dict[str, Any]]:
        return [{
            "kind": "archive",
            "url": f"{self.base_url}/v1/trade-messages/{external_id}/files/archive",
            "requires_authorization": True,
        }]

    async def fetch_events(self, external_id: str) -> list[dict[str, Any]]:
        lot = await self.fetch_lot(external_id)
        return [{"external_id": external_id, "raw_data": lot.raw_data}]

    async def healthcheck(self) -> ConnectorHealth:
        try:
            await asyncio.to_thread(self._authenticate)
            return ConnectorHealth(status="healthy", message="EFRSB authentication succeeded")
        except Exception as exc:
            return ConnectorHealth(status="unavailable", message=str(exc))
