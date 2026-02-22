"""Broker router — routes API calls to the correct broker by symbol."""

from __future__ import annotations

from bot.instruments import route_symbol
from bot.layer1_data.broker_interface import BrokerInterface


class BrokerRouter:
    """Registry that maps symbols to their broker implementation."""

    def __init__(self) -> None:
        self._brokers: dict[str, BrokerInterface] = {}

    def register(self, broker: BrokerInterface) -> None:
        """Register a broker implementation."""
        self._brokers[broker.broker_id] = broker

    def get_broker(self, symbol: str) -> BrokerInterface:
        """Look up the correct broker for a symbol."""
        broker_type = route_symbol(symbol)
        broker = self._brokers.get(broker_type.value)
        if broker is None:
            raise KeyError(f"No broker registered for {broker_type.value} (symbol: {symbol})")
        return broker

    def get_broker_by_id(self, broker_id: str) -> BrokerInterface:
        """Direct lookup by broker ID."""
        broker = self._brokers.get(broker_id)
        if broker is None:
            raise KeyError(f"No broker registered with id '{broker_id}'")
        return broker

    @property
    def all_brokers(self) -> list[BrokerInterface]:
        """All registered broker instances."""
        return list(self._brokers.values())

    async def close_all(self) -> None:
        """Close all broker connections."""
        for broker in self._brokers.values():
            await broker.close()
