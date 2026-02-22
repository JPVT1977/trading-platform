"""OANDA v20 REST API client implementing BrokerInterface.

All oandapyV20 calls are synchronous — wrapped in run_in_executor() to avoid
blocking the event loop. Rate limit (HTTP 429) responses trigger exponential
backoff via tenacity.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import partial
from typing import TYPE_CHECKING

from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

import oandapyV20
import oandapyV20.endpoints.instruments as instruments_ep
import oandapyV20.endpoints.orders as orders_ep
import oandapyV20.endpoints.pricing as pricing_ep
import oandapyV20.endpoints.accounts as accounts_ep
from oandapyV20.exceptions import V20Error

from bot.layer1_data.broker_interface import BrokerInterface
from bot.models import Candle

if TYPE_CHECKING:
    from bot.config import Settings

# OANDA timeframe mapping
_TF_MAP = {
    "1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
    "1h": "H1", "4h": "H4", "1d": "D", "1w": "W",
}


class OandaClient(BrokerInterface):
    """OANDA v20 REST API broker implementation."""

    def __init__(self, settings: Settings) -> None:
        environment = "practice" if settings.oanda_sandbox else "live"
        self._api = oandapyV20.API(
            access_token=settings.oanda_api_token,
            environment=environment,
        )
        self._account_id = settings.oanda_account_id
        self._settings = settings

    @property
    def broker_id(self) -> str:
        return "oanda"

    def _run_sync(self, func, *args, **kwargs):
        """Run a synchronous oandapyV20 call in the thread pool."""
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(None, partial(func, *args, **kwargs))

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=1, max=30),
        retry=retry_if_exception_type((V20Error, ConnectionError, TimeoutError)),
        before_sleep=lambda rs: logger.warning(
            f"OANDA request failed, retrying ({rs.attempt_number}/3)..."
        ),
    )
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int | None = None
    ) -> list[Candle]:
        """Fetch OHLCV candles from OANDA. Uses midpoint prices."""
        granularity = _TF_MAP.get(timeframe, "H1")
        count = limit or self._settings.lookback_candles

        params = {
            "granularity": granularity,
            "count": min(count, 5000),  # OANDA max is 5000
            "price": "M",  # Midpoint
        }

        ep = instruments_ep.InstrumentsCandles(instrument=symbol, params=params)
        response = await self._run_sync(self._api.request, ep)

        candles = []
        for c in response.get("candles", []):
            if not c.get("complete", False) and limit != 1:
                # Skip incomplete candles unless fetching just 1 (for cache seeding)
                if limit and limit > 1:
                    continue
            mid = c["mid"]
            candles.append(Candle(
                timestamp=datetime.fromisoformat(
                    c["time"].replace("Z", "+00:00")
                ) if "Z" in c["time"] else datetime.fromisoformat(c["time"]),
                open=float(mid["o"]),
                high=float(mid["h"]),
                low=float(mid["l"]),
                close=float(mid["c"]),
                volume=float(c.get("volume", 0)),
            ))

        logger.debug(f"OANDA: fetched {len(candles)} candles for {symbol}/{timeframe}")
        return candles

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=1, max=30),
        retry=retry_if_exception_type((V20Error, ConnectionError, TimeoutError)),
    )
    async def fetch_ticker(self, symbol: str) -> dict:
        """Fetch current bid/ask/mid pricing from OANDA."""
        params = {"instruments": symbol}
        ep = pricing_ep.PricingInfo(accountID=self._account_id, params=params)
        response = await self._run_sync(self._api.request, ep)

        prices = response.get("prices", [])
        if not prices:
            raise V20Error(0, f"No pricing data for {symbol}")

        p = prices[0]
        bid = float(p["bids"][0]["price"])
        ask = float(p["asks"][0]["price"])
        mid = (bid + ask) / 2

        return {"last": mid, "bid": bid, "ask": ask}

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=1, max=30),
        retry=retry_if_exception_type((V20Error, ConnectionError, TimeoutError)),
    )
    async def fetch_balance(self) -> dict:
        """Fetch OANDA account summary."""
        ep = accounts_ep.AccountSummary(accountID=self._account_id)
        response = await self._run_sync(self._api.request, ep)
        account = response.get("account", {})
        return {
            "total": float(account.get("balance", 0)),
            "free": float(account.get("marginAvailable", 0)),
            "used": float(account.get("marginUsed", 0)),
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=1, max=30),
        retry=retry_if_exception_type((V20Error, ConnectionError, TimeoutError)),
    )
    async def create_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> dict:
        """Place a limit order on OANDA. Negative units = sell."""
        units = str(int(amount)) if side == "buy" else str(-int(amount))
        data = {
            "order": {
                "type": "LIMIT",
                "instrument": symbol,
                "units": units,
                "price": str(price),
                "timeInForce": "GTC",
            }
        }
        ep = orders_ep.OrderCreate(accountID=self._account_id, data=data)
        response = await self._run_sync(self._api.request, ep)
        order_id = (
            response.get("orderCreateTransaction", {}).get("id", "")
        )
        logger.info(f"OANDA limit order: {side} {amount} {symbol} @ {price} -> {order_id}")
        return {"id": order_id, "info": response}

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=1, max=30),
        retry=retry_if_exception_type((V20Error, ConnectionError, TimeoutError)),
    )
    async def create_stop_order(
        self, symbol: str, side: str, amount: float, stop_price: float
    ) -> dict:
        """Place a stop order on OANDA."""
        units = str(int(amount)) if side == "buy" else str(-int(amount))
        data = {
            "order": {
                "type": "STOP",
                "instrument": symbol,
                "units": units,
                "price": str(stop_price),
                "timeInForce": "GTC",
            }
        }
        ep = orders_ep.OrderCreate(accountID=self._account_id, data=data)
        response = await self._run_sync(self._api.request, ep)
        order_id = (
            response.get("orderCreateTransaction", {}).get("id", "")
        )
        logger.info(f"OANDA stop order: {side} {amount} {symbol} @ stop {stop_price} -> {order_id}")
        return {"id": order_id, "info": response}

    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        """Cancel an OANDA order."""
        ep = orders_ep.OrderCancel(accountID=self._account_id, orderID=order_id)
        response = await self._run_sync(self._api.request, ep)
        logger.info(f"OANDA order {order_id} cancelled")
        return {"id": order_id, "info": response}

    async def check_connectivity(self) -> None:
        """Verify OANDA API connectivity by fetching account summary."""
        ep = accounts_ep.AccountSummary(accountID=self._account_id)
        await self._run_sync(self._api.request, ep)

    async def close(self) -> None:
        """No persistent connection to close for oandapyV20."""
        pass
