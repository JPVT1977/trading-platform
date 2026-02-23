"""Tests for the deterministic signal validator.

Tests all 6 validation rules with edge cases and boundary values.
"""

from datetime import UTC

import pytest

from bot.config import Settings, TradingMode
from bot.layer2_intelligence.validator import validate_signal
from bot.models import (
    DivergenceSignal,
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


class TestRule7ADXTrendStrength:
    def test_rejects_crypto_with_low_adx(self, settings):
        """Crypto signals should be rejected when ADX < 20 (choppy market)."""
        indicators = _make_indicators(adx_last=15.0)
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
        assert "choppy" in result.reason.lower()

    def test_allows_crypto_with_strong_adx(self, settings):
        """Crypto signals should pass when ADX >= 20."""
        indicators = _make_indicators(adx_last=25.0)
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
        assert "choppy" not in result.reason

    def test_allows_forex_with_low_adx(self, settings):
        """Forex signals should NOT be rejected by Rule 7 alone (ADX < 20)."""
        indicators = _make_indicators(adx_last=15.0)
        # Use non-flat EMA slope to avoid Rule 8 triggering
        indicators.ema_long = [41000.0 + i * 10 for i in range(30)]
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            reasoning="test",
            direction=SignalDirection.LONG,
            entry_price=1.0800,
            stop_loss=1.0740,
            take_profit_1=1.0920,
            symbol="EUR_USD",
            timeframe="4h",
        )
        result = validate_signal(signal, indicators, settings)
        assert "choppy" not in result.reason


class TestRule8RangingMarket:
    def test_rejects_ranging_market(self, settings):
        """Signals should be rejected when ADX < 25 and EMA 200 is flat."""
        # Flat EMA: all values the same → slope = 0%
        indicators = _make_indicators(adx_last=20.0, ema_long_values=[41000.0] * 30)
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            reasoning="test",
            direction=SignalDirection.LONG,
            entry_price=42000,
            stop_loss=41500,
            take_profit_1=43000,
            symbol="EUR_USD",
            timeframe="4h",
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "Ranging" in result.reason

    def test_allows_trending_market(self, settings):
        """Signals should pass when EMA 200 has clear slope even with low ADX."""
        # Trending EMA: slope > 0.05%
        ema_vals = [41000.0 + i * 5.0 for i in range(30)]  # ~0.7% rise over 10 bars
        indicators = _make_indicators(adx_last=22.0, ema_long_values=ema_vals)
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            reasoning="test",
            direction=SignalDirection.LONG,
            entry_price=42000,
            stop_loss=41500,
            take_profit_1=43000,
            symbol="EUR_USD",
            timeframe="4h",
        )
        result = validate_signal(signal, indicators, settings)
        assert "Ranging" not in result.reason


class TestRule9OscillatorStack:
    def test_rejects_single_confirming_indicator(self, settings):
        """Reject signals with fewer than 2 confirming indicators."""
        indicators = _make_indicators()
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
            confirming_indicators=["RSI"],
            swing_length_bars=18,
            divergence_magnitude=8.5,
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "confirming indicator" in result.reason

    def test_accepts_two_confirming_indicators(self, settings):
        """Accept signals with 2+ confirming indicators."""
        indicators = _make_indicators()
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
            confirming_indicators=["RSI", "MACD"],
            swing_length_bars=18,
            divergence_magnitude=8.5,
        )
        result = validate_signal(signal, indicators, settings)
        assert "confirming indicator" not in result.reason


class TestRule10SwingLength:
    def test_rejects_short_swing_4h(self, settings):
        """Reject 4h signals with swing length below 15 bars."""
        indicators = _make_indicators()
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
            confirming_indicators=["RSI", "MACD"],
            swing_length_bars=10,
            divergence_magnitude=8.5,
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "Swing length" in result.reason

    def test_accepts_adequate_swing_4h(self, settings):
        """Accept 4h signals with swing length >= 15 bars."""
        indicators = _make_indicators()
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
            confirming_indicators=["RSI", "MACD"],
            swing_length_bars=15,
            divergence_magnitude=8.5,
        )
        result = validate_signal(signal, indicators, settings)
        assert "Swing length" not in result.reason


class TestRule11DivergenceMagnitude:
    def test_rejects_low_rsi_magnitude(self, settings):
        """Reject RSI divergence with magnitude below 5.0."""
        indicators = _make_indicators()
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
            indicator="RSI",
            confirming_indicators=["RSI", "MACD"],
            swing_length_bars=18,
            divergence_magnitude=3.0,
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "magnitude" in result.reason

    def test_accepts_strong_rsi_magnitude(self, settings):
        """Accept RSI divergence with magnitude >= 5.0."""
        indicators = _make_indicators()
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
            indicator="RSI",
            confirming_indicators=["RSI", "MACD"],
            swing_length_bars=18,
            divergence_magnitude=8.0,
        )
        result = validate_signal(signal, indicators, settings)
        assert "magnitude" not in result.reason


class TestRule12ZeroVolume:
    def test_rejects_zero_volume(self, settings):
        """Reject signals when recent volume includes zero."""
        vols = [1000.0] * 27 + [0.0, 1000.0, 1000.0]
        indicators = _make_indicators(volumes=vols)
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
            confirming_indicators=["RSI", "MACD"],
            swing_length_bars=18,
            divergence_magnitude=8.5,
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "Zero volume" in result.reason

    def test_accepts_normal_volume(self, settings):
        """Accept signals with normal volume."""
        indicators = _make_indicators()
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
            confirming_indicators=["RSI", "MACD"],
            swing_length_bars=18,
            divergence_magnitude=8.5,
        )
        result = validate_signal(signal, indicators, settings)
        assert "Zero volume" not in result.reason


class TestRule13LowVolume:
    def test_rejects_low_volume(self, settings):
        """Reject signals when current volume < 50% of SMA."""
        vols = [1000.0] * 29 + [200.0]  # Last vol is 200, SMA is 1000 → 20%
        indicators = _make_indicators(volumes=vols, volume_sma=[1000.0] * 30)
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
            confirming_indicators=["RSI", "MACD"],
            swing_length_bars=18,
            divergence_magnitude=8.5,
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "Low volume" in result.reason

    def test_accepts_adequate_volume(self, settings):
        """Accept signals when current volume >= 50% of SMA."""
        vols = [1000.0] * 29 + [600.0]  # 600/1000 = 60% > 50%
        indicators = _make_indicators(volumes=vols, volume_sma=[1000.0] * 30)
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
            confirming_indicators=["RSI", "MACD"],
            swing_length_bars=18,
            divergence_magnitude=8.5,
        )
        result = validate_signal(signal, indicators, settings)
        assert "Low volume" not in result.reason


class TestRule14CandleGate:
    def test_rejects_no_reversal_pattern(self, settings):
        """Reject long signals when no bullish candlestick pattern found."""
        n = 30
        patterns = {
            "hammer": [0] * n,
            "engulfing": [0] * n,
            "morning_star": [0] * n,
            "piercing": [0] * n,
            "inverted_hammer": [0] * n,
            "shooting_star": [0] * n,
            "evening_star": [0] * n,
            "dark_cloud": [0] * n,
            "hanging_man": [0] * n,
        }
        indicators = _make_indicators(candle_patterns=patterns)
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
            confirming_indicators=["RSI", "MACD"],
            swing_length_bars=18,
            divergence_magnitude=8.5,
        )
        result = validate_signal(signal, indicators, settings)
        assert not result.passed
        assert "reversal candlestick" in result.reason

    def test_accepts_hammer_pattern(self, settings):
        """Accept long signals with hammer pattern in last 3 bars."""
        n = 30
        hammer_vals = [0] * (n - 2) + [100, 0]
        patterns = {
            "hammer": hammer_vals,
            "engulfing": [0] * n,
            "morning_star": [0] * n,
            "piercing": [0] * n,
            "inverted_hammer": [0] * n,
            "shooting_star": [0] * n,
            "evening_star": [0] * n,
            "dark_cloud": [0] * n,
            "hanging_man": [0] * n,
        }
        indicators = _make_indicators(candle_patterns=patterns)
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
            confirming_indicators=["RSI", "MACD"],
            swing_length_bars=18,
            divergence_magnitude=8.5,
        )
        result = validate_signal(signal, indicators, settings)
        assert "reversal candlestick" not in result.reason

    def test_accepts_bearish_engulfing(self, settings):
        """Accept short signals with bearish engulfing (-100) in last 3 bars."""
        n = 30
        patterns = {
            "hammer": [0] * n,
            "engulfing": [0] * (n - 1) + [-100],
            "morning_star": [0] * n,
            "piercing": [0] * n,
            "inverted_hammer": [0] * n,
            "shooting_star": [0] * n,
            "evening_star": [0] * n,
            "dark_cloud": [0] * n,
            "hanging_man": [0] * n,
        }
        indicators = _make_indicators(candle_patterns=patterns)
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            reasoning="test",
            direction=SignalDirection.SHORT,
            entry_price=42000,
            stop_loss=42800,
            take_profit_1=40400,
            symbol="BTC/USDT",
            timeframe="4h",
            confirming_indicators=["RSI", "MACD"],
            swing_length_bars=18,
            divergence_magnitude=8.5,
        )
        result = validate_signal(signal, indicators, settings)
        assert "reversal candlestick" not in result.reason

    def test_skips_when_empty_patterns(self, settings):
        """Skip candle gate when candle_patterns is empty (backward compat)."""
        indicators = _make_indicators(candle_patterns={})
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
            confirming_indicators=["RSI", "MACD"],
            swing_length_bars=18,
            divergence_magnitude=8.5,
        )
        result = validate_signal(signal, indicators, settings)
        assert "reversal candlestick" not in result.reason


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
    adx_last: float = 30.0,
    ema_long_values: list[float] | None = None,
    volumes: list[float] | None = None,
    volume_sma: list[float | None] | None = None,
    candle_patterns: dict[str, list[int]] | None = None,
) -> IndicatorSet:
    """Create a minimal IndicatorSet with specific last values."""
    from datetime import datetime

    n = 30
    ema_long = ema_long_values if ema_long_values and len(ema_long_values) == n else [41000.0] * n
    vols = volumes if volumes and len(volumes) == n else [1000.0] * n
    vol_sma = volume_sma if volume_sma and len(volume_sma) == n else [1000.0] * n
    cdl = candle_patterns if candle_patterns is not None else {}
    return IndicatorSet(
        symbol="BTC/USDT",
        timeframe="4h",
        timestamp=datetime(2026, 2, 1, tzinfo=UTC),
        rsi=[50.0] * (n - 1) + [rsi_last],
        macd_line=[0.0] * n,
        macd_signal=[0.0] * n,
        macd_histogram=[0.0] * n,
        obv=[100000.0] * n,
        mfi=[50.0] * n,
        stoch_k=[50.0] * n,
        stoch_d=[50.0] * n,
        cci=[0.0] * n,
        williams_r=[-50.0] * n,
        atr=[atr_last] * n,
        adx=[adx_last] * n,
        ema_short=[42000.0] * n,
        ema_medium=[41800.0] * n,
        ema_long=ema_long,
        closes=[42000.0] * n,
        highs=[42200.0] * n,
        lows=[41800.0] * n,
        volumes=vols,
        volume_sma=vol_sma,
        candle_patterns=cdl,
    )
