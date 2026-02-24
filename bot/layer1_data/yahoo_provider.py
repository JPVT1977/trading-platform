"""Yahoo Finance data provider — fetches OHLCV candles for stock symbols.

Used as an alternative data source for IG stock CFDs because IG blocks
historical price data for individual share CFDs via their API
(``unauthorised.access.to.equity.exception``).

yfinance is synchronous, so all calls are wrapped in asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pandas as pd
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from bot.models import Candle

_RETRY_DECORATOR = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=1, max=30),
    retry=retry_if_exception_type(Exception),
    before_sleep=lambda rs: logger.warning(
        f"Yahoo Finance request failed, retrying ({rs.attempt_number}/3)..."
    ),
)


class YahooProvider:
    """Async wrapper around yfinance for stock OHLCV data."""

    # Timeframes that map directly to yfinance intervals
    _YF_INTERVALS: ClassVar[dict[str, str]] = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1h",
        "1d": "1d",
        "1w": "1wk",
    }

    # Timeframes requiring aggregation: tf -> (fetch_interval, resample_rule)
    _AGGREGATION: ClassVar[dict[str, tuple[str, str]]] = {
        "4h": ("1h", "4h"),
    }

    @_RETRY_DECORATOR
    async def fetch_ohlcv(
        self, ticker: str, timeframe: str, limit: int = 200
    ) -> list[Candle]:
        """Fetch OHLCV candles from Yahoo Finance.

        For timeframes not natively supported (e.g. 4h), fetches at a
        finer granularity and resamples.
        """
        if timeframe in self._AGGREGATION:
            fetch_tf, resample_rule = self._AGGREGATION[timeframe]
            raw_limit = limit * 4 + 20
            df = await self._download(ticker, fetch_tf, raw_limit)
            if df.empty:
                return []
            df = self._resample(df, resample_rule)
        else:
            df = await self._download(ticker, timeframe, limit)

        if df.empty:
            logger.warning(f"Yahoo: no data for {ticker}/{timeframe}")
            return []

        candles = self._to_candles(df, limit)
        logger.debug(f"Yahoo: fetched {len(candles)} candles for {ticker}/{timeframe}")
        return candles

    async def _download(
        self, ticker: str, interval: str, limit: int
    ) -> pd.DataFrame:
        """Download price data via yfinance in a thread executor."""
        yf_interval = self._YF_INTERVALS.get(interval, interval)
        period = self._period_for(interval, limit)
        return await asyncio.to_thread(
            self._sync_download, ticker, yf_interval, period
        )

    @staticmethod
    def _sync_download(ticker: str, interval: str, period: str) -> pd.DataFrame:
        """Synchronous yfinance download (called via to_thread)."""
        import yfinance as yf

        t = yf.Ticker(ticker)
        df = t.history(period=period, interval=interval)
        if df.empty:
            return df

        # Ensure UTC timestamps
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        return df

    @staticmethod
    def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
        """Resample to a coarser timeframe (e.g. 1h -> 4h)."""
        return (
            df.resample(rule)
            .agg({
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            })
            .dropna(subset=["Open"])
        )

    @staticmethod
    def _to_candles(df: pd.DataFrame, limit: int) -> list[Candle]:
        """Convert a pandas DataFrame to a list of Candle models."""
        df = df.tail(limit)
        candles: list[Candle] = []
        for ts, row in df.iterrows():
            candles.append(
                Candle(
                    timestamp=ts.to_pydatetime(),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row["Volume"]),
                )
            )
        return candles

    @staticmethod
    def _period_for(interval: str, limit: int) -> str:
        """Estimate a yfinance period string that fetches enough candles."""
        if interval in ("1h", "60m"):
            # ~7 hourly candles per trading day
            if limit <= 300:
                return "60d"
            if limit <= 900:
                return "6mo"
            return "1y"
        if interval == "1d":
            return "1y" if limit <= 250 else "2y"
        if interval in ("1wk", "1w"):
            return "5y"
        return "1y"
