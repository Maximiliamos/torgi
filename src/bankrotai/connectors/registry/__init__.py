from __future__ import annotations

from collections.abc import Callable

from bankrotai.connectors.base import AuctionConnector


class ConnectorRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], AuctionConnector]] = {}

    def register(self, source_id: str, factory: Callable[[], AuctionConnector]) -> None:
        key = source_id.strip().lower()
        if not key:
            raise ValueError("source_id is required")
        if key in self._factories:
            raise ValueError(f"Connector already registered: {key}")
        self._factories[key] = factory

    def create(self, source_id: str) -> AuctionConnector:
        key = source_id.strip().lower()
        try:
            return self._factories[key]()
        except KeyError as exc:
            raise KeyError(f"Unknown connector: {source_id}") from exc

    def source_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


connector_registry = ConnectorRegistry()


def _register_builtins() -> None:
    from bankrotai.connectors.registry.fedresurs import FedresursConnector
    from bankrotai.connectors.registry.lot_online import LotOnlineConnector
    from bankrotai.connectors.registry.tbankrot import TBankrotConnector
    from bankrotai.connectors.registry.torgi_gov import TorgiGovConnector
    from bankrotai.connectors.registry.torgi_russia import TorgiRussiaConnector

    connector_registry.register(TorgiGovConnector.source_id, TorgiGovConnector)
    connector_registry.register(TBankrotConnector.source_id, TBankrotConnector)
    connector_registry.register(FedresursConnector.source_id, FedresursConnector)
    connector_registry.register(LotOnlineConnector.source_id, LotOnlineConnector)
    connector_registry.register(TorgiRussiaConnector.source_id, TorgiRussiaConnector)


_register_builtins()

__all__ = ["ConnectorRegistry", "connector_registry"]
