"""Shared test fixtures for all test modules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from bot.config import Settings, TradingMode
from bot.models import (
    Candle,
    DivergenceSignal,
    DivergenceType,
    IndicatorSet,
    OrderState,
    PortfolioState,
    SignalDirection,
    TradeOrder,
)


@pytest.fixture
def settings() -> Settings:
    """Test settings with dev trading mode (no exchange calls)."""
    return Settings(
        trading_mode=TradingMode.DEV,
        anthropic_api_key="test-key-not-real",
        database_url="postgresql://test:test@localhost:5432/test_trading",
        exchange_api_key="test-key",
        exchange_api_secret="test-secret",
        exchange_sandbox=True,
        symbols=["BTC/USDT"],
        timeframes=["4h"],
        log_level="DEBUG",
        min_confidence=0.6,
        min_risk_reward=1.5,
        max_position_pct=2.0,
        max_daily_loss_pct=5.0,
        max_open_positions=3,
        max_correlation_exposure=2,
        binance_max_open_positions=3,
        binance_max_correlation_exposure=2,
    )


@pytest.fixture
def sample_candles() -> list[Candle]:
    """200 candles of synthetic BTC/USDT data with realistic price action."""
    rng = np.random.default_rng(seed=42)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candles = []
    price = 42000.0

    for i in range(200):
        change = rng.normal(0, 100)
        price = max(price + change, 1000)  # Prevent negative prices
        high_offset = abs(rng.normal(0, 50))
        low_offset = abs(rng.normal(0, 50))
        close_offset = rng.normal(0, 30)

        candles.append(Candle(
            timestamp=base + timedelta(hours=i * 4),
            open=round(price, 2),
            high=round(price + high_offset, 2),
            low=round(price - low_offset, 2),
            close=round(price + close_offset, 2),
            volume=round(rng.uniform(100, 2000), 2),
        ))

    return candles


@pytest.fixture
def sample_indicator_set() -> IndicatorSet:
    """Pre-computed indicator set for testing."""
    rng = np.random.default_rng(seed=42)
    n = 200

    return IndicatorSet(
        symbol="BTC/USDT",
        timeframe="4h",
        timestamp=datetime(2026, 2, 1, tzinfo=UTC),
        rsi=[float(rng.uniform(20, 80)) for _ in range(n)],
        macd_line=[float(rng.normal(0, 50)) for _ in range(n)],
        macd_signal=[float(rng.normal(0, 40)) for _ in range(n)],
        macd_histogram=[float(rng.normal(0, 20)) for _ in range(n)],
        obv=[float(rng.uniform(100000, 200000)) for _ in range(n)],
        mfi=[float(rng.uniform(20, 80)) for _ in range(n)],
        stoch_k=[float(rng.uniform(10, 90)) for _ in range(n)],
        stoch_d=[float(rng.uniform(10, 90)) for _ in range(n)],
        cci=[float(rng.uniform(-200, 200)) for _ in range(n)],
        williams_r=[float(rng.uniform(-100, 0)) for _ in range(n)],
        atr=[float(rng.uniform(200, 500)) for _ in range(n)],
        adx=[float(rng.uniform(20, 60)) for _ in range(n)],
        ema_short=[float(42000 + rng.normal(0, 200)) for _ in range(n)],
        ema_medium=[float(41800 + rng.normal(0, 300)) for _ in range(n)],
        ema_long=[float(41000 + rng.normal(0, 500)) for _ in range(n)],
        closes=[float(42000 + rng.normal(0, 200)) for _ in range(n)],
        highs=[float(42200 + rng.normal(0, 200)) for _ in range(n)],
        lows=[float(41800 + rng.normal(0, 200)) for _ in range(n)],
        volumes=[float(rng.uniform(100, 2000)) for _ in range(n)],
        volume_sma=[float(rng.uniform(500, 1500)) for _ in range(n)],
        candle_patterns={},
    )


@pytest.fixture
def bullish_signal() -> DivergenceSignal:
    """A valid bullish divergence signal for testing."""
    return DivergenceSignal(
        divergence_detected=True,
        divergence_type=DivergenceType.BULLISH_REGULAR,
        indicator="RSI",
        confidence=0.85,
        direction=SignalDirection.LONG,
        entry_price=42000.0,
        stop_loss=41500.0,
        take_profit_1=43000.0,
        take_profit_2=44000.0,
        take_profit_3=45000.0,
        reasoning="RSI showing higher lows while price makes lower lows on 4h timeframe",
        symbol="BTC/USDT",
        timeframe="4h",
        confirming_indicators=["RSI", "MACD"],
        swing_length_bars=18,
        divergence_magnitude=8.5,
    )


@pytest.fixture
def bearish_signal() -> DivergenceSignal:
    """A valid bearish divergence signal for testing."""
    return DivergenceSignal(
        divergence_detected=True,
        divergence_type=DivergenceType.BEARISH_REGULAR,
        indicator="MACD",
        confidence=0.75,
        direction=SignalDirection.SHORT,
        entry_price=42000.0,
        stop_loss=42800.0,
        take_profit_1=41000.0,
        take_profit_2=40200.0,
        take_profit_3=39500.0,
        reasoning="MACD histogram showing lower highs while price makes higher highs",
        symbol="BTC/USDT",
        timeframe="4h",
        confirming_indicators=["RSI", "MACD"],
        swing_length_bars=18,
        divergence_magnitude=8.5,
    )


@pytest.fixture
def empty_portfolio() -> PortfolioState:
    """Portfolio with no open positions."""
    return PortfolioState(
        total_equity=10000.0,
        available_balance=10000.0,
        open_positions=[],
        daily_pnl=0.0,
        daily_trades=0,
    )


@pytest.fixture
def full_portfolio() -> PortfolioState:
    """Portfolio at max open positions."""
    orders = [
        TradeOrder(
            symbol=f"PAIR{i}/USDT",
            direction=SignalDirection.LONG,
            state=OrderState.FILLED,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit_1=110.0,
            quantity=1.0,
        )
        for i in range(3)
    ]
    return PortfolioState(
        total_equity=10000.0,
        available_balance=7000.0,
        open_positions=orders,
        daily_pnl=-100.0,
        daily_trades=3,
    )
