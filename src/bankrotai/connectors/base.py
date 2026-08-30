from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from bankrotai.domain import NormalizedLot


def json_safe_value(value: Any) -> Any:
    """Convert connector evidence to values accepted by JSON database columns."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_value(item) for item in value]
    return value


class ConnectorCapabilityError(NotImplementedError):
    pass


@dataclass(slots=True)
class ConnectorPage:
    items: list[NormalizedLot]
    next_cursor: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ConnectorHealth:
    status: str
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class AuctionConnector(ABC):
    source_id: str
    detail_enrichment_version: int = 1
    capabilities: frozenset[str] = frozenset({"search"})

    @abstractmethod
    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        raise NotImplementedError

    async def fetch_lot(self, external_id: str) -> NormalizedLot:
        raise ConnectorCapabilityError(f"{self.source_id} does not implement fetch_lot")

    async def enrich_lot(self, lot: NormalizedLot) -> NormalizedLot:
        """Optionally upgrade a shallow listing before persistence."""
        return lot

    async def fetch_documents(self, external_id: str) -> list[dict[str, Any]]:
        raise ConnectorCapabilityError(f"{self.source_id} does not implement fetch_documents")

    async def fetch_events(self, external_id: str) -> list[dict[str, Any]]:
        raise ConnectorCapabilityError(f"{self.source_id} does not implement fetch_events")

    async def healthcheck(self) -> ConnectorHealth:
        return ConnectorHealth(status="unknown", message="Connector has no active healthcheck")
