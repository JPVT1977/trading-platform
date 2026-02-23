"""Tests for the divergence scoring engine.

Tests each scoring dimension independently, session weighting,
score clamping, and realistic full signal scores.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from bot.config import Settings, TradingMode
from bot.layer2_intelligence.scoring import (
    _score_adx,
    _score_ema_alignment,
    _score_indicator_confluence,
    _score_session,
    _score_swing_length,
    _score_volume,
    compute_score,
)
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
        min_divergence_score=5.0,
    )


def _make_signal(
    direction: SignalDirection = SignalDirection.LONG,
    confirming: list[str] | None = None,
    swing_bars: int | None = 18,
    magnitude: float | None = 8.5,
    symbol: str = "BTC/USDT",
    timeframe: str = "4h",
) -> DivergenceSignal:
    is_long = direction == SignalDirection.LONG
    return DivergenceSignal(
        divergence_detected=True,
        divergence_type=(
            DivergenceType.BULLISH_REGULAR if is_long
            else DivergenceType.BEARISH_REGULAR
        ),
        indicator="RSI",
        confidence=0.85,
        direction=direction,
        entry_price=42000.0,
        stop_loss=41500.0 if is_long else 42500.0,
        take_profit_1=43000.0 if is_long else 41000.0,
        reasoning="test signal",
        symbol=symbol,
        timeframe=timeframe,
        confirming_indicators=confirming if confirming is not None else ["RSI", "MACD"],
        swing_length_bars=swing_bars,
        divergence_magnitude=magnitude,
    )


def _make_indicators(
    volumes: list[float] | None = None,
    volume_sma: list[float | None] | None = None,
    ema_short: list[float] | None = None,
    ema_medium: list[float] | None = None,
    ema_long: list[float] | None = None,
    adx: list[float] | None = None,
) -> IndicatorSet:
    n = 30
    return IndicatorSet(
        symbol="BTC/USDT",
        timeframe="4h",
        timestamp=datetime(2026, 2, 1, tzinfo=UTC),
        rsi=[50.0] * n,
        macd_line=[0.0] * n,
        macd_signal=[0.0] * n,
        macd_histogram=[0.0] * n,
        obv=[100000.0] * n,
        mfi=[50.0] * n,
        stoch_k=[50.0] * n,
        stoch_d=[50.0] * n,
        cci=[0.0] * n,
        williams_r=[-50.0] * n,
        atr=[350.0] * n,
        adx=adx or [30.0] * n,
        ema_short=ema_short or [42000.0] * n,
        ema_medium=ema_medium or [41800.0] * n,
        ema_long=ema_long or [41000.0] * n,
        closes=[42000.0] * n,
        highs=[42200.0] * n,
        lows=[41800.0] * n,
        volumes=volumes if volumes is not None else [1000.0] * n,
        volume_sma=volume_sma if volume_sma is not None else [1000.0] * n,
        candle_patterns={},
    )


class TestIndicatorConfluence:
    def test_zero_indicators(self):
        signal = _make_signal(confirming=[])
        assert _score_indicator_confluence(signal) == 0.0

    def test_one_indicator(self):
        signal = _make_signal(confirming=["RSI"])
        assert _score_indicator_confluence(signal) == 0.0

    def test_two_indicators(self):
        signal = _make_signal(confirming=["RSI", "MACD"])
        assert _score_indicator_confluence(signal) == 1.0

    def test_three_indicators(self):
        signal = _make_signal(confirming=["RSI", "MACD", "OBV"])
        assert _score_indicator_confluence(signal) == 1.5

    def test_four_indicators(self):
        signal = _make_signal(confirming=["RSI", "MACD", "OBV", "MFI"])
        assert _score_indicator_confluence(signal) == 2.0

    def test_five_plus_indicators(self):
        signal = _make_signal(confirming=["RSI", "MACD", "OBV", "MFI", "CCI"])
        assert _score_indicator_confluence(signal) == 3.0


class TestSwingLength:
    def test_ideal_range_4h(self):
        signal = _make_signal(swing_bars=20, timeframe="4h")
        assert _score_swing_length(signal) == 2.0

    def test_ideal_range_1h(self):
        signal = _make_signal(swing_bars=15, timeframe="1h")
        assert _score_swing_length(signal) == 2.0

    def test_none_swing_bars(self):
        signal = _make_signal(swing_bars=None)
        assert _score_swing_length(signal) == 0.0

    def test_too_short_4h(self):
        signal = _make_signal(swing_bars=8, timeframe="4h")
        score = _score_swing_length(signal)
        assert 0.0 < score < 2.0

    def test_too_long_4h(self):
        signal = _make_signal(swing_bars=50, timeframe="4h")
        score = _score_swing_length(signal)
        assert 0.0 < score < 2.0


class TestVolumeConfirmation:
    def test_high_volume(self):
        indicators = _make_indicators(
            volumes=[1000.0] * 29 + [1600.0],
            volume_sma=[1000.0] * 30,
        )
        assert _score_volume(indicators) == 2.0

    def test_moderate_volume(self):
        indicators = _make_indicators(
            volumes=[1000.0] * 29 + [1300.0],
            volume_sma=[1000.0] * 30,
        )
        assert _score_volume(indicators) == 1.5

    def test_normal_volume(self):
        indicators = _make_indicators(
            volumes=[1000.0] * 29 + [900.0],
            volume_sma=[1000.0] * 30,
        )
        assert _score_volume(indicators) == 1.0

    def test_low_volume(self):
        indicators = _make_indicators(
            volumes=[1000.0] * 29 + [500.0],
            volume_sma=[1000.0] * 30,
        )
        assert _score_volume(indicators) == 0.5

    def test_empty_volume_sma(self):
        indicators = _make_indicators(volume_sma=[])
        assert _score_volume(indicators) == 0.5


class TestEMAAlignment:
    def test_long_against_downtrend(self):
        """Bullish divergence in downtrend = ideal reversal = 2.0."""
        signal = _make_signal(direction=SignalDirection.LONG)
        indicators = _make_indicators(
            ema_short=[40000.0] * 30,
            ema_medium=[41000.0] * 30,
            ema_long=[42000.0] * 30,
        )
        assert _score_ema_alignment(signal, indicators) == 2.0

    def test_long_with_uptrend(self):
        """Bullish divergence in uptrend = with trend = 0.5."""
        signal = _make_signal(direction=SignalDirection.LONG)
        indicators = _make_indicators(
            ema_short=[42000.0] * 30,
            ema_medium=[41000.0] * 30,
            ema_long=[40000.0] * 30,
        )
        assert _score_ema_alignment(signal, indicators) == 0.5

    def test_short_against_uptrend(self):
        """Bearish divergence in uptrend = ideal reversal = 2.0."""
        signal = _make_signal(direction=SignalDirection.SHORT)
        indicators = _make_indicators(
            ema_short=[42000.0] * 30,
            ema_medium=[41000.0] * 30,
            ema_long=[40000.0] * 30,
        )
        assert _score_ema_alignment(signal, indicators) == 2.0

    def test_mixed_ema(self):
        """Mixed EMAs = ranging = 1.0."""
        signal = _make_signal(direction=SignalDirection.LONG)
        indicators = _make_indicators(
            ema_short=[41000.0] * 30,
            ema_medium=[42000.0] * 30,
            ema_long=[41500.0] * 30,
        )
        assert _score_ema_alignment(signal, indicators) == 1.0


class TestADXStrength:
    def test_strong_adx(self):
        indicators = _make_indicators(adx=[35.0] * 30)
        assert _score_adx(indicators) == 1.0

    def test_moderate_adx(self):
        indicators = _make_indicators(adx=[27.0] * 30)
        assert _score_adx(indicators) == 0.75

    def test_weak_adx(self):
        indicators = _make_indicators(adx=[22.0] * 30)
        assert _score_adx(indicators) == 0.5

    def test_very_weak_adx(self):
        indicators = _make_indicators(adx=[15.0] * 30)
        assert _score_adx(indicators) == 0.25


class TestSessionWeighting:
    def test_crypto_always_zero(self):
        signal = _make_signal(symbol="BTC/USDT")
        assert _score_session(signal) == 0.0

    def test_forex_london_ny_overlap(self):
        signal = _make_signal(symbol="EUR_USD")
        with patch("bot.layer2_intelligence.scoring.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 1, 14, 0, tzinfo=UTC)
            score = _score_session(signal)
        assert score == 0.5

    def test_forex_off_hours(self):
        signal = _make_signal(symbol="EUR_USD")
        with patch("bot.layer2_intelligence.scoring.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 1, 23, 0, tzinfo=UTC)
            score = _score_session(signal)
        assert score == -0.5

    def test_index_primary_session(self):
        signal = _make_signal(symbol="IX.D.SPTRD.IFE.IP")
        with patch("bot.layer2_intelligence.scoring.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 1, 15, 0, tzinfo=UTC)
            score = _score_session(signal)
        assert score == 0.5


class TestScoreClamping:
    def test_minimum_clamp(self, settings):
        """Score should never go below 1.0."""
        signal = _make_signal(confirming=[], swing_bars=None)
        indicators = _make_indicators(
            adx=[10.0] * 30,
            volume_sma=[],
        )
        scored = compute_score(signal, indicators, settings)
        assert scored.score >= 1.0

    def test_maximum_clamp(self, settings):
        """Score should never exceed 10.0."""
        signal = _make_signal(
            confirming=["RSI", "MACD", "OBV", "MFI", "CCI"],
            swing_bars=20,
        )
        indicators = _make_indicators(
            volumes=[1000.0] * 29 + [2000.0],
            volume_sma=[1000.0] * 30,
            ema_short=[40000.0] * 30,
            ema_medium=[41000.0] * 30,
            ema_long=[42000.0] * 30,
            adx=[35.0] * 30,
        )
        scored = compute_score(signal, indicators, settings)
        assert scored.score <= 10.0


class TestRealisticScores:
    def test_strong_signal_above_threshold(self, settings):
        """A well-confirmed signal should score above min_divergence_score."""
        signal = _make_signal(
            confirming=["RSI", "MACD", "OBV"],
            swing_bars=18,
        )
        indicators = _make_indicators(
            volumes=[1000.0] * 29 + [1300.0],
            volume_sma=[1000.0] * 30,
            adx=[28.0] * 30,
        )
        scored = compute_score(signal, indicators, settings)
        assert scored.score >= settings.min_divergence_score
        assert "indicator_confluence" in scored.breakdown
        assert "swing_length" in scored.breakdown
        assert "volume_confirmation" in scored.breakdown

    def test_weak_signal_below_threshold(self, settings):
        """A poorly confirmed signal should score below threshold."""
        signal = _make_signal(
            confirming=["RSI"],
            swing_bars=5,
            magnitude=2.0,
        )
        indicators = _make_indicators(
            volumes=[1000.0] * 29 + [400.0],
            volume_sma=[1000.0] * 30,
            adx=[15.0] * 30,
        )
        scored = compute_score(signal, indicators, settings)
        assert scored.score < settings.min_divergence_score

    def test_breakdown_has_all_dimensions(self, settings):
        """Score breakdown should contain all 6 dimensions."""
        signal = _make_signal()
        indicators = _make_indicators()
        scored = compute_score(signal, indicators, settings)
        expected_keys = {
            "indicator_confluence",
            "swing_length",
            "volume_confirmation",
            "ema_alignment",
            "adx_strength",
            "session_weighting",
        }
        assert set(scored.breakdown.keys()) == expected_keys
