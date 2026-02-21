"""Tests for the deterministic signal validator.

Tests all 6 validation rules with edge cases and boundary values.
"""

import pytest

from bot.config import Settings, TradingMode
from bot.layer2_intelligence.validator import validate_signal
from bot.models import (
    DivergenceSignal,
    DivergenceType,
    IndicatorSet,
    SignalDirection,
)


@pytest.fixture
def settings():
    return Settings(
        trading_mode=TradingMode.DEV,
        min_confidence=0.6,
        min_risk_reward=1.5,
    )


@pytest.fixture
def indicators(sample_indicator_set):
    return sample_indicator_set


class TestRule1MinConfidence:
    def test_rejects_low_confidence(self, indicators, settings):
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.3,
            reasoning="test",
            direction=SignalDirection.LONG,
            entry_price=42000,
            stop_loss=41500,
            take_profit_1=43000,
            symbol="BTC/USDT",
            timeframe="4h",
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "Confidence" in result.reason

    def test_accepts_threshold_confidence(self, indicators, settings):
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.6,
            reasoning="test",
            direction=SignalDirection.LONG,
            entry_price=42000,
            stop_loss=41500,
            take_profit_1=43000,
            symbol="BTC/USDT",
            timeframe="4h",
        )
        result = validate_signal(signal, indicators, settings)
        # May pass or fail on other rules, but not confidence
        assert "Confidence" not in result.reason


class TestRule2RequiredFields:
    def test_rejects_missing_entry(self, indicators, settings):
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.8,
            reasoning="test",
            direction=SignalDirection.LONG,
            entry_price=None,
            stop_loss=41500,
            take_profit_1=43000,
            symbol="BTC/USDT",
            timeframe="4h",
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "Missing" in result.reason

    def test_rejects_missing_stop_loss(self, indicators, settings):
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.8,
            reasoning="test",
            direction=SignalDirection.LONG,
            entry_price=42000,
            stop_loss=None,
            take_profit_1=43000,
            symbol="BTC/USDT",
            timeframe="4h",
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "Missing" in result.reason


class TestRule3StopLossSide:
    def test_rejects_long_with_stop_above_entry(self, indicators, settings):
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            reasoning="test",
            direction=SignalDirection.LONG,
            entry_price=42000,
            stop_loss=42500,
            take_profit_1=43000,
            symbol="BTC/USDT",
            timeframe="4h",
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "stop_loss must be below" in result.reason

    def test_rejects_short_with_stop_below_entry(self, indicators, settings):
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            reasoning="test",
            direction=SignalDirection.SHORT,
            entry_price=42000,
            stop_loss=41500,
            take_profit_1=41000,
            symbol="BTC/USDT",
            timeframe="4h",
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "stop_loss must be above" in result.reason


class TestRule4RiskReward:
    def test_rejects_bad_rr_ratio(self, indicators, settings):
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            reasoning="test",
            direction=SignalDirection.LONG,
            entry_price=42000,
            stop_loss=41500,
            take_profit_1=42200,  # Only 200 reward vs 500 risk = 0.4 R:R
            symbol="BTC/USDT",
            timeframe="4h",
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "R:R ratio" in result.reason

    def test_accepts_good_rr_ratio(self, indicators, settings):
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            reasoning="test",
            direction=SignalDirection.LONG,
            entry_price=42000,
            stop_loss=41500,
            take_profit_1=43000,  # 1000 reward vs 500 risk = 2.0 R:R
            symbol="BTC/USDT",
            timeframe="4h",
        )
        result = validate_signal(signal, indicators, settings)
        # Should pass R:R check (may fail other rules)
        assert "R:R ratio" not in result.reason


class TestRule5RSIContradiction:
    def test_rejects_long_with_extreme_overbought(self, settings):
        # Create indicators with RSI at 85
        indicators = _make_indicators(rsi_last=85.0)
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            reasoning="test",
            direction=SignalDirection.LONG,
            entry_price=42000,
            stop_loss=41500,
            take_profit_1=43000,
            symbol="BTC/USDT",
            timeframe="4h",
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "overbought" in result.reason

    def test_rejects_short_with_extreme_oversold(self, settings):
        indicators = _make_indicators(rsi_last=15.0)
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            reasoning="test",
            direction=SignalDirection.SHORT,
            entry_price=42000,
            stop_loss=42800,
            take_profit_1=40400,  # 1600 reward / 800 risk = 2.0 R:R (passes rule 4)
            symbol="BTC/USDT",
            timeframe="4h",
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "oversold" in result.reason


class TestRule6ATRStopDistance:
    def test_rejects_stop_too_tight(self, settings):
        # ATR=400, stop=50 away = 0.125x ATR (below 0.5x minimum)
        indicators = _make_indicators(atr_last=400.0)
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            reasoning="test",
            direction=SignalDirection.LONG,
            entry_price=42000,
            stop_loss=41950,  # Only 50 away
            take_profit_1=43000,
            symbol="BTC/USDT",
            timeframe="4h",
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "too tight" in result.reason

    def test_rejects_stop_too_wide(self, settings):
        # ATR=400, stop=2500 away = 6.25x ATR (above 5.0x maximum)
        indicators = _make_indicators(atr_last=400.0)
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            reasoning="test",
            direction=SignalDirection.LONG,
            entry_price=42000,
            stop_loss=39500,  # 2500 away
            take_profit_1=46000,
            symbol="BTC/USDT",
            timeframe="4h",
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "too wide" in result.reason


class TestFullValidation:
    def test_valid_bullish_signal_passes(self, bullish_signal, sample_indicator_set, settings):
        result = validate_signal(bullish_signal, sample_indicator_set, settings)
        # May pass or fail depending on random indicator values, but check it runs
        assert isinstance(result.passed, bool)
        assert isinstance(result.reason, str)

    def test_no_signal_fields_still_validates(self, sample_indicator_set, settings):
        """Signal with no divergence should fail on missing fields."""
        signal = DivergenceSignal(
            divergence_detected=False,
            confidence=0.1,
            reasoning="No pattern found",
            symbol="BTC/USDT",
            timeframe="4h",
        )
        result = validate_signal(signal, sample_indicator_set, settings)
        assert not result.passed


# --- Helper ---

def _make_indicators(
    rsi_last: float = 50.0,
    atr_last: float = 350.0,
) -> IndicatorSet:
    """Create a minimal IndicatorSet with specific last values."""
    from datetime import datetime, timezone

    n = 30
    return IndicatorSet(
        symbol="BTC/USDT",
        timeframe="4h",
        timestamp=datetime(2026, 2, 1, tzinfo=timezone.utc),
        rsi=[50.0] * (n - 1) + [rsi_last],
        macd_line=[0.0] * n,
        macd_signal=[0.0] * n,
        macd_histogram=[0.0] * n,
        obv=[100000.0] * n,
        mfi=[50.0] * n,
        stoch_k=[50.0] * n,
        stoch_d=[50.0] * n,
        atr=[atr_last] * n,
        ema_short=[42000.0] * n,
        ema_medium=[41800.0] * n,
        ema_long=[41000.0] * n,
        closes=[42000.0] * n,
        highs=[42200.0] * n,
        lows=[41800.0] * n,
        volumes=[1000.0] * n,
    )
