"""Composite broker for IG stock CFDs — Yahoo Finance data + IG order execution.

IG Markets blocks historical OHLCV data for individual share CFDs
(``unauthorised.access.to.equity.exception``).  This broker transparently
routes data requests to Yahoo Finance while keeping all order execution
on IG.  Non-stock IG symbols (indices, commodities) still use IG for data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from bot.instruments import IG_EPIC_TO_TICKER
from bot.layer1_data.broker_interface import BrokerInterface
from bot.models import Candle

if TYPE_CHECKING:
    from bot.config import Settings
    from bot.layer1_data.ig_client import IGClient
    from bot.layer1_data.yahoo_provider import YahooProvider


class IGStockBroker(BrokerInterface):
    """Wraps IGClient with Yahoo Finance for stock OHLCV data.

    For IG epic codes that map to a Yahoo ticker (stocks), ``fetch_ohlcv``
    delegates to :class:`YahooProvider`.  Everything else passes through to
    :class:`IGClient`.
    """

    def __init__(
        self, ig_client: IGClient, yahoo: YahooProvider, settings: Settings
    ) -> None:
        self._ig = ig_client
        self._yahoo = yahoo
        self._settings = settings
        self._epic_to_ticker: dict[str, str] = dict(IG_EPIC_TO_TICKER)

    @property
    def broker_id(self) -> str:
        return "ig"

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int | None = None
    ) -> list[Candle]:
        ticker = self._epic_to_ticker.get(symbol)
        if ticker:
            count = limit or self._settings.lookback_candles
            candles = await self._yahoo.fetch_ohlcv(ticker, timeframe, count)
            logger.debug(
                f"IGStockBroker: {len(candles)} candles for {symbol} "
                f"(via Yahoo/{ticker}/{timeframe})"
            )
            return candles
        # Non-stock IG instrument — use IG directly
        return await self._ig.fetch_ohlcv(symbol, timeframe, limit)

    async def fetch_ticker(self, symbol: str) -> dict:
        """Fetch current price — IG first, Yahoo fallback for stocks.

        IG demo accounts return null bid/offer for stock CFDs, so we
        fall back to Yahoo Finance for any stock epic in the ticker map.
        """
        result = await self._ig.fetch_ticker(symbol)
        if result.get("last"):
            return result

        # IG returned no price — try Yahoo for stock CFDs
        yahoo_ticker = self._epic_to_ticker.get(symbol)
        if yahoo_ticker:
            try:
                yahoo_result = await self._yahoo.fetch_ticker(yahoo_ticker)
                if yahoo_result.get("last"):
                    logger.debug(
                        f"IGStockBroker: ticker for {symbol} via Yahoo/{yahoo_ticker} "
                        f"(IG returned null)"
                    )
                    return yahoo_result
            except Exception as e:
                logger.warning(f"Yahoo ticker fallback failed for {symbol}: {e}")

        return result

    async def fetch_balance(self) -> dict:
        return await self._ig.fetch_balance()

    async def create_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> dict:
        return await self._ig.create_limit_order(symbol, side, amount, price)

    async def create_stop_order(
        self, symbol: str, side: str, amount: float, stop_price: float
    ) -> dict:
        return await self._ig.create_stop_order(symbol, side, amount, stop_price)

    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        return await self._ig.cancel_order(order_id, symbol)

    async def check_connectivity(self) -> None:
        await self._ig.check_connectivity()

    async def close(self) -> None:
        await self._ig.close()
