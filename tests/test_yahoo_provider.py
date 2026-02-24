"""Tests for YahooProvider — Yahoo Finance OHLCV data fetcher."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

from bot.layer1_data.yahoo_provider import YahooProvider
from bot.models import Candle


def _make_df(rows: int = 50) -> pd.DataFrame:
    """Create a synthetic yfinance-like DataFrame starting at a 4h boundary."""
    base = datetime(2026, 2, 2, 0, 0, tzinfo=UTC)
    index = pd.DatetimeIndex(
        [base + timedelta(hours=i) for i in range(rows)],
        tz="UTC",
    )
    return pd.DataFrame(
        {
            "Open": [100.0 + i * 0.1 for i in range(rows)],
            "High": [101.0 + i * 0.1 for i in range(rows)],
            "Low": [99.0 + i * 0.1 for i in range(rows)],
            "Close": [100.5 + i * 0.1 for i in range(rows)],
            "Volume": [1000 + i for i in range(rows)],
        },
        index=index,
    )


@pytest.fixture
def provider() -> YahooProvider:
    return YahooProvider()


@pytest.mark.asyncio
async def test_fetch_ohlcv_1h(provider: YahooProvider) -> None:
    """1h candles are fetched directly from yfinance."""
    df = _make_df(rows=50)

    with patch.object(provider, "_download", return_value=df) as mock_dl:
        candles = await provider.fetch_ohlcv("NVDA", "1h", limit=30)

    mock_dl.assert_awaited_once_with("NVDA", "1h", 30)
    assert len(candles) == 30
    assert all(isinstance(c, Candle) for c in candles)
    # Should return the LAST 30 candles (tail)
    assert candles[-1].close == pytest.approx(100.5 + 49 * 0.1)


@pytest.mark.asyncio
async def test_fetch_ohlcv_4h_aggregation(provider: YahooProvider) -> None:
    """4h candles are aggregated from 1h data."""
    df = _make_df(rows=100)

    with patch.object(provider, "_download", return_value=df) as mock_dl:
        candles = await provider.fetch_ohlcv("NVDA", "4h", limit=10)

    # Should request 1h data with extra buffer
    mock_dl.assert_awaited_once_with("NVDA", "1h", 10 * 4 + 20)
    assert len(candles) <= 10
    assert all(isinstance(c, Candle) for c in candles)
    # Each aggregated candle should have positive volume
    for c in candles:
        assert c.volume > 0


@pytest.mark.asyncio
async def test_fetch_ohlcv_empty_data(provider: YahooProvider) -> None:
    """Empty DataFrame (market closed) returns empty list."""
    empty_df = pd.DataFrame()

    with patch.object(provider, "_download", return_value=empty_df):
        candles = await provider.fetch_ohlcv("NVDA", "1h", limit=30)

    assert candles == []


@pytest.mark.asyncio
async def test_fetch_ohlcv_4h_empty_data(provider: YahooProvider) -> None:
    """4h aggregation with empty source data returns empty list."""
    empty_df = pd.DataFrame()

    with patch.object(provider, "_download", return_value=empty_df):
        candles = await provider.fetch_ohlcv("NVDA", "4h", limit=10)

    assert candles == []


@pytest.mark.asyncio
async def test_download_calls_yfinance(provider: YahooProvider) -> None:
    """_download wraps yfinance in asyncio.to_thread."""
    df = _make_df(rows=10)

    with patch.object(YahooProvider, "_sync_download", return_value=df) as mock_sync:
        result = await provider._download("AAPL", "1h", 200)

    mock_sync.assert_called_once_with("AAPL", "1h", "60d")
    assert len(result) == 10


def test_resample_4h() -> None:
    """Resample aggregates 1h bars into 4h bars correctly."""
    df = _make_df(rows=8)
    result = YahooProvider._resample(df, "4h")

    assert len(result) == 2

    # First 4h bar (rows 0-3): open from first, high from max, close from last
    assert result.iloc[0]["Open"] == pytest.approx(100.0)
    assert result.iloc[0]["High"] == pytest.approx(101.0 + 3 * 0.1)  # 101.3
    assert result.iloc[0]["Low"] == pytest.approx(99.0)
    assert result.iloc[0]["Close"] == pytest.approx(100.5 + 3 * 0.1)  # 100.8
    assert result.iloc[0]["Volume"] == pytest.approx(1000 + 1001 + 1002 + 1003)

    # Second 4h bar (rows 4-7)
    assert result.iloc[1]["Open"] == pytest.approx(100.0 + 4 * 0.1)  # 100.4
    assert result.iloc[1]["High"] == pytest.approx(101.0 + 7 * 0.1)  # 101.7
    assert result.iloc[1]["Low"] == pytest.approx(99.0 + 4 * 0.1)  # 99.4
    assert result.iloc[1]["Close"] == pytest.approx(100.5 + 7 * 0.1)  # 101.2


def test_to_candles_respects_limit() -> None:
    """_to_candles returns at most ``limit`` candles from the tail."""
    df = _make_df(rows=50)
    candles = YahooProvider._to_candles(df, limit=10)

    assert len(candles) == 10
    # Should be the last 10 rows
    assert candles[0].open == pytest.approx(100.0 + 40 * 0.1)


def test_to_candles_preserves_utc() -> None:
    """Candle timestamps should be timezone-aware (UTC)."""
    df = _make_df(rows=5)
    candles = YahooProvider._to_candles(df, limit=5)

    for c in candles:
        assert c.timestamp.tzinfo is not None


def test_period_for_1h() -> None:
    """Period calculation for 1h interval."""
    assert YahooProvider._period_for("1h", 50) == "60d"
    assert YahooProvider._period_for("1h", 200) == "60d"
    assert YahooProvider._period_for("1h", 500) == "6mo"
    assert YahooProvider._period_for("1h", 1000) == "1y"


def test_period_for_1d() -> None:
    """Period calculation for daily interval."""
    assert YahooProvider._period_for("1d", 200) == "1y"
    assert YahooProvider._period_for("1d", 300) == "2y"
