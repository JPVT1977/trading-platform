"""IG Markets broker client implementing BrokerInterface.

Fully async — uses aiohttp via IGSession (no executor wrapping needed).
All price data uses bid/ask midpoint for consistency with the indicator pipeline.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiohttp
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from bot.instruments import get_instrument
from bot.layer1_data.broker_interface import BrokerInterface
from bot.layer1_data.ig_rate_limiter import IGRateLimiter
from bot.layer1_data.ig_session import IGSession
from bot.models import Candle

if TYPE_CHECKING:
    from bot.config import Settings

# IG resolution strings
_TF_MAP = {
    "1m": "MINUTE",
    "5m": "MINUTE_5",
    "15m": "MINUTE_15",
    "30m": "MINUTE_30",
    "1h": "HOUR",
    "4h": "HOUR_4",
    "1d": "DAY",
    "1w": "WEEK",
}

_RETRY_DECORATOR = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=1, max=30),
    retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError, RuntimeError)),
    before_sleep=lambda rs: logger.warning(
        f"IG request failed, retrying ({rs.attempt_number}/3)..."
    ),
)


class IGClient(BrokerInterface):
    """IG Markets REST API broker implementation."""

    def __init__(self, settings: Settings) -> None:
        self._session = IGSession(settings)
        self._limiter = IGRateLimiter()
        self._settings = settings

    @property
    def broker_id(self) -> str:
        return "ig"

    @_RETRY_DECORATOR
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int | None = None
    ) -> list[Candle]:
        """Fetch OHLCV candles from IG. Converts bid/ask to midpoint."""
        resolution = _TF_MAP.get(timeframe, "HOUR")
        count = limit or self._settings.lookback_candles

        await self._limiter.acquire("historical")
        data = await self._session.request(
            "GET",
            f"/prices/{symbol}",
            version="3",
            params={"resolution": resolution, "max": str(min(count, 1000)), "pageSize": "0"},
        )

        candles: list[Candle] = []
        for p in data.get("prices", []):
            ts_str = p.get("snapshotTime", "") or p.get("snapshotTimeUTC", "")
            if not ts_str:
                continue

            # Parse IG timestamp formats
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("/", "-"))
            except ValueError:
                # IG sometimes uses "2024/01/15 14:00:00" format
                try:
                    ts = datetime.strptime(ts_str, "%Y/%m/%d %H:%M:%S").replace(
                        tzinfo=UTC
                    )
                except ValueError:
                    continue

            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)

            bid = p.get("closeBid") or p.get("closePrice", {})
            ask = p.get("closeAsk") or p.get("closePrice", {})

            # Handle nested bid/ask OHLC objects
            if isinstance(bid, dict) and isinstance(ask, dict):
                o = (float(bid.get("open", 0)) + float(ask.get("open", 0))) / 2
                h = (float(bid.get("high", 0)) + float(ask.get("high", 0))) / 2
                l_ = (float(bid.get("low", 0)) + float(ask.get("low", 0))) / 2
                c = (float(bid.get("close", 0)) + float(ask.get("close", 0))) / 2
            else:
                # Flat structure — use openPrice/highPrice/lowPrice/closePrice
                op = p.get("openPrice", {})
                hp = p.get("highPrice", {})
                lp = p.get("lowPrice", {})
                cp = p.get("closePrice", {})

                def _mid(price_obj: dict) -> float:
                    b = float(price_obj.get("bid", 0))
                    a = float(price_obj.get("ask", 0))
                    return (b + a) / 2 if (b and a) else b or a

                o = _mid(op) if isinstance(op, dict) else float(op or 0)
                h = _mid(hp) if isinstance(hp, dict) else float(hp or 0)
                l_ = _mid(lp) if isinstance(lp, dict) else float(lp or 0)
                c = _mid(cp) if isinstance(cp, dict) else float(cp or 0)

            vol = float(p.get("lastTradedVolume", 0))

            candles.append(Candle(
                timestamp=ts,
                open=o,
                high=h,
                low=l_,
                close=c,
                volume=vol,
            ))

        logger.debug(f"IG: fetched {len(candles)} candles for {symbol}/{timeframe}")
        return candles

    @_RETRY_DECORATOR
    async def fetch_ticker(self, symbol: str) -> dict:
        """Fetch current bid/ask/mid from IG market details."""
        await self._limiter.acquire("data")
        data = await self._session.request("GET", f"/markets/{symbol}", version="3")

        snapshot = data.get("snapshot", {})
        bid = float(snapshot.get("bid", 0))
        ask = float(snapshot.get("offer", 0))
        mid = (bid + ask) / 2 if (bid and ask) else bid or ask

        return {"last": mid, "bid": bid, "ask": ask}

    @_RETRY_DECORATOR
    async def fetch_balance(self) -> dict:
        """Fetch IG account balance."""
        await self._limiter.acquire("data")
        data = await self._session.request("GET", "/accounts", version="1")

        # Find the matching account or use the first one
        for acc in data.get("accounts", []):
            acc_id = self._settings.ig_account_id
            if acc.get("accountId") == acc_id or not acc_id:
                balance = acc.get("balance", {})
                return {
                    "total": float(balance.get("balance", 0)),
                    "free": float(balance.get("available", 0)),
                    "used": float(balance.get("balance", 0)) - float(balance.get("available", 0)),
                }

        return {"total": 0, "free": 0, "used": 0}

    @_RETRY_DECORATOR
    async def create_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> dict:
        """Place a limit working order on IG."""
        await self._limiter.acquire("trading")
        direction = "BUY" if side == "buy" else "SELL"
        currency = get_instrument(symbol).quote_currency

        payload = {
            "epic": symbol,
            "direction": direction,
            "size": str(amount),
            "level": str(price),
            "type": "LIMIT",
            "timeInForce": "GOOD_TILL_CANCELLED",
            "guaranteedStop": False,
            "forceOpen": True,
            "currencyCode": currency,
        }

        data = await self._session.request(
            "POST", "/workingorders/otc", version="2", json=payload
        )
        deal_ref = data.get("dealReference", "")

        # Confirm the deal
        result = await self._confirm_deal(deal_ref)
        deal_id = result.get("dealId", deal_ref)

        logger.info(f"IG limit order: {side} {amount} {symbol} @ {price} -> {deal_id}")
        return {"id": deal_id, "info": result}

    @_RETRY_DECORATOR
    async def create_stop_order(
        self, symbol: str, side: str, amount: float, stop_price: float
    ) -> dict:
        """Place a stop working order on IG."""
        await self._limiter.acquire("trading")
        direction = "BUY" if side == "buy" else "SELL"
        currency = get_instrument(symbol).quote_currency

        payload = {
            "epic": symbol,
            "direction": direction,
            "size": str(amount),
            "level": str(stop_price),
            "type": "STOP",
            "timeInForce": "GOOD_TILL_CANCELLED",
            "guaranteedStop": False,
            "forceOpen": True,
            "currencyCode": currency,
        }

        data = await self._session.request(
            "POST", "/workingorders/otc", version="2", json=payload
        )
        deal_ref = data.get("dealReference", "")

        result = await self._confirm_deal(deal_ref)
        deal_id = result.get("dealId", deal_ref)

        logger.info(f"IG stop order: {side} {amount} {symbol} @ stop {stop_price} -> {deal_id}")
        return {"id": deal_id, "info": result}

    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        """Cancel an IG working order."""
        await self._limiter.acquire("trading")
        data = await self._session.request(
            "DELETE", f"/workingorders/otc/{order_id}", version="2"
        )
        logger.info(f"IG order {order_id} cancelled")
        return {"id": order_id, "info": data}

    async def check_connectivity(self) -> None:
        """Verify IG API connectivity by fetching session info."""
        await self._limiter.acquire("data")
        await self._session.request("GET", "/session", version="1")

    async def close(self) -> None:
        """Close the IG session."""
        await self._session.close()

    async def _confirm_deal(self, deal_reference: str) -> dict:
        """Poll deal confirmation up to 5 times at 500ms intervals."""
        for attempt in range(5):
            await asyncio.sleep(0.5)
            try:
                await self._limiter.acquire("trading")
                result = await self._session.request(
                    "GET", f"/confirms/{deal_reference}", version="1"
                )
                status = result.get("dealStatus", "")
                if status in ("ACCEPTED", "REJECTED"):
                    if status == "REJECTED":
                        reason = result.get("reason", "unknown")
                        logger.warning(f"IG deal {deal_reference} rejected: {reason}")
                    return result
            except Exception as e:
                if attempt == 4:
                    logger.error(f"IG deal confirmation failed after 5 attempts: {e}")
                    raise

        return {"dealReference": deal_reference, "dealStatus": "UNKNOWN"}
