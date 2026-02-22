from __future__ import annotations

from datetime import datetime, timezone

import ccxt.async_support as ccxt
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from bot.config import Settings
from bot.models import Candle


class MarketDataClient:
    """Async CCXT exchange client with retry logic."""

    def __init__(self, settings: Settings) -> None:
        exchange_class = getattr(ccxt, settings.exchange_id)
        self._exchange: ccxt.Exchange = exchange_class({
            "apiKey": settings.exchange_api_key or None,
            "secret": settings.exchange_api_secret or None,
            "sandbox": settings.exchange_sandbox,
            "enableRateLimit": True,
            "timeout": 30000,
        })
        self._settings = settings

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((
            ccxt.NetworkError,
            ccxt.ExchangeNotAvailable,
            ccxt.RequestTimeout,
        )),
        before_sleep=lambda retry_state: logger.warning(
            f"Exchange request failed, retrying ({retry_state.attempt_number}/3)..."
        ),
    )
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int | None = None
    ) -> list[Candle]:
        """Fetch OHLCV candles from the exchange."""
        fetch_limit = limit or self._settings.lookback_candles
        raw = await self._exchange.fetch_ohlcv(symbol, timeframe, limit=fetch_limit)

        candles = [
            Candle(
                timestamp=datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc),
                open=float(r[1]),
                high=float(r[2]),
                low=float(r[3]),
                close=float(r[4]),
                volume=float(r[5]),
            )
            for r in raw
        ]

        logger.debug(f"Fetched {len(candles)} candles for {symbol}/{timeframe}")
        return candles

    async def check_connectivity(self) -> None:
        """Quick connectivity check — fetch server time or a single ticker."""
        await self._exchange.fetch_time()

    async def fetch_balance(self) -> dict:
        """Fetch account balance from the exchange."""
        return await self._exchange.fetch_balance()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((
            ccxt.NetworkError,
            ccxt.ExchangeNotAvailable,
            ccxt.RequestTimeout,
        )),
    )
    async def create_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> dict:
        """Place a limit order on the exchange."""
        logger.info(f"Placing {side} limit order: {symbol} {amount} @ {price}")
        return await self._exchange.create_limit_order(symbol, side, amount, price)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((
            ccxt.NetworkError,
            ccxt.ExchangeNotAvailable,
            ccxt.RequestTimeout,
        )),
    )
    async def create_stop_order(
        self, symbol: str, side: str, amount: float, stop_price: float
    ) -> dict:
        """Place a stop-market order on the exchange."""
        logger.info(f"Placing {side} stop order: {symbol} {amount} @ stop {stop_price}")
        return await self._exchange.create_order(
            symbol, "stop", side, amount,
            params={"stopPrice": stop_price, "triggerPrice": stop_price},
        )

    async def fetch_ticker(self, symbol: str) -> dict:
        """Fetch current ticker (last price, bid, ask) for a symbol."""
        return await self._exchange.fetch_ticker(symbol)

    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        """Cancel an open order."""
        logger.info(f"Cancelling order {order_id} for {symbol}")
        return await self._exchange.cancel_order(order_id, symbol)

    async def close(self) -> None:
        """Close the exchange connection."""
        await self._exchange.close()
