"""Abstract broker interface — all exchange clients implement this."""

from __future__ import annotations

from abc import ABC, abstractmethod

from bot.models import Candle


class BrokerInterface(ABC):
    """Unified interface for all broker implementations."""

    @property
    @abstractmethod
    def broker_id(self) -> str:
        """Unique identifier for this broker (e.g. 'binance', 'oanda')."""

    @abstractmethod
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int | None = None
    ) -> list[Candle]: ...

    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> dict: ...

    @abstractmethod
    async def fetch_balance(self) -> dict: ...

    @abstractmethod
    async def create_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> dict: ...

    @abstractmethod
    async def create_stop_order(
        self, symbol: str, side: str, amount: float, stop_price: float
    ) -> dict: ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> dict: ...

    @abstractmethod
    async def check_connectivity(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...
