"""Deterministic divergence detector — Phase 1 (post-backtest fixes).

Changes from v1:
1. TREND FILTER: Hard reject counter-trend signals (price vs EMA-200)
2. 3 UNCORRELATED OSCILLATORS: RSI (momentum), MACD (trend-momentum), OBV (volume)
   — requires ALL 3 to confirm, not 3-of-7
3. VOLUME CONFIRMATION: Signal candle volume must exceed 20-period average
4. SWING ORDER 5: Already default, but now enforced as minimum

Outputs the same DivergenceSignal model the live bot uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from bot.models import (
    DivergenceSignal,
    DivergenceType,
    IndicatorSet,
    SignalDirection,
)


@dataclass
class DetectorParams:
    """Tunable parameters for the deterministic detector."""

    swing_order: int = 5
    min_confluence: int = 3          # Now means "all 3 must agree"
    min_confidence: float = 0.7
    min_risk_reward: float = 2.0
    atr_sl_multiplier: float = 1.5
    max_position_pct: float = 2.0
    require_trend_alignment: bool = True   # Phase 1: hard trend filter
    require_volume_confirmation: bool = True  # Phase 1: volume filter
    volume_sma_period: int = 20
    setup_expiry_hours: int = 24  # Phase 2: how long a 4h setup stays valid for 1h confirmation


# ---------------------------------------------------------------------------
# Confidence tiers based on oscillator confluence count (out of 3)
# ---------------------------------------------------------------------------

_CONFIDENCE_MAP = {
    1: 0.50,
    2: 0.65,
    3: 0.85,  # All 3 uncorrelated oscillators agree
}


def _confluence_to_confidence(count: int) -> float:
    """Map oscillator confluence count to confidence score."""
    if count <= 0:
        return 0.0
    return _CONFIDENCE_MAP.get(count, 0.85)


# ---------------------------------------------------------------------------
# Swing point detection
# ---------------------------------------------------------------------------


def find_swing_highs(highs: list[float], order: int = 5) -> list[int]:
    """Find local maxima with `order` bars on each side."""
    swings: list[int] = []
    for i in range(order, len(highs) - order):
        window = highs[i - order : i + order + 1]
        if highs[i] == max(window):
            swings.append(i)
    return swings


def find_swing_lows(lows: list[float], order: int = 5) -> list[int]:
    """Find local minima with `order` bars on each side."""
    swings: list[int] = []
    for i in range(order, len(lows) - order):
        window = lows[i - order : i + order + 1]
        if lows[i] == min(window):
            swings.append(i)
    return swings


# ---------------------------------------------------------------------------
# Per-oscillator divergence check
# ---------------------------------------------------------------------------


def _get_valid(arr: list[Optional[float]], idx: int) -> Optional[float]:
    """Safely get a non-None value from an indicator array at given index."""
    if 0 <= idx < len(arr):
        return arr[idx]
    return None


def check_bullish_regular(
    prices: list[float],
    indicator: list[Optional[float]],
    swing_indices: list[int],
) -> bool:
    """Bullish regular: price makes lower low, oscillator makes higher low."""
    if len(swing_indices) < 2:
        return False
    i1, i2 = swing_indices[-2], swing_indices[-1]
    p1, p2 = prices[i1], prices[i2]
    v1, v2 = _get_valid(indicator, i1), _get_valid(indicator, i2)
    if v1 is None or v2 is None:
        return False
    return p2 < p1 and v2 > v1


def check_bearish_regular(
    prices: list[float],
    indicator: list[Optional[float]],
    swing_indices: list[int],
) -> bool:
    """Bearish regular: price makes higher high, oscillator makes lower high."""
    if len(swing_indices) < 2:
        return False
    i1, i2 = swing_indices[-2], swing_indices[-1]
    p1, p2 = prices[i1], prices[i2]
    v1, v2 = _get_valid(indicator, i1), _get_valid(indicator, i2)
    if v1 is None or v2 is None:
        return False
    return p2 > p1 and v2 < v1


def check_bullish_hidden(
    prices: list[float],
    indicator: list[Optional[float]],
    swing_indices: list[int],
) -> bool:
    """Bullish hidden: price makes higher low, oscillator makes lower low."""
    if len(swing_indices) < 2:
        return False
    i1, i2 = swing_indices[-2], swing_indices[-1]
    p1, p2 = prices[i1], prices[i2]
    v1, v2 = _get_valid(indicator, i1), _get_valid(indicator, i2)
    if v1 is None or v2 is None:
        return False
    return p2 > p1 and v2 < v1


def check_bearish_hidden(
    prices: list[float],
    indicator: list[Optional[float]],
    swing_indices: list[int],
) -> bool:
    """Bearish hidden: price makes lower high, oscillator makes higher high."""
    if len(swing_indices) < 2:
        return False
    i1, i2 = swing_indices[-2], swing_indices[-1]
    p1, p2 = prices[i1], prices[i2]
    v1, v2 = _get_valid(indicator, i1), _get_valid(indicator, i2)
    if v1 is None or v2 is None:
        return False
    return p2 < p1 and v2 > v1


# ---------------------------------------------------------------------------
# 3 uncorrelated oscillators only (Phase 1 change #2)
# ---------------------------------------------------------------------------


def _get_oscillator_arrays(indicators: IndicatorSet) -> dict[str, list[Optional[float]]]:
    """Return only 3 uncorrelated oscillator arrays.

    RSI   — pure momentum (price-based)
    MACD  — trend-momentum hybrid (moving average crossover)
    OBV   — volume flow (completely independent of price oscillators)

    These measure genuinely different things. When all 3 show divergence,
    that's real confluence — not the same signal measured 3 ways.
    """
    return {
        "RSI": indicators.rsi,
        "MACD": indicators.macd_histogram,
        "OBV": indicators.obv,
    }


def _scan_divergences(
    indicators: IndicatorSet,
    swing_lows: list[int],
    swing_highs: list[int],
) -> tuple[int, int, list[str], list[str], DivergenceType | None, DivergenceType | None]:
    """Scan 3 uncorrelated oscillators for bullish and bearish divergences.

    Returns (bullish_count, bearish_count, bullish_names, bearish_names,
             bullish_type, bearish_type).
    """
    oscillators = _get_oscillator_arrays(indicators)

    bullish_count = 0
    bearish_count = 0
    bullish_names: list[str] = []
    bearish_names: list[str] = []
    bullish_type: DivergenceType | None = None
    bearish_type: DivergenceType | None = None

    for name, osc_arr in oscillators.items():
        # Check bullish divergences (use swing lows for price)
        if check_bullish_regular(indicators.lows, osc_arr, swing_lows):
            bullish_count += 1
            bullish_names.append(name)
            if bullish_type is None:
                bullish_type = DivergenceType.BULLISH_REGULAR
        elif check_bullish_hidden(indicators.lows, osc_arr, swing_lows):
            bullish_count += 1
            bullish_names.append(name)
            if bullish_type is None:
                bullish_type = DivergenceType.BULLISH_HIDDEN

        # Check bearish divergences (use swing highs for price)
        if check_bearish_regular(indicators.highs, osc_arr, swing_highs):
            bearish_count += 1
            bearish_names.append(name)
            if bearish_type is None:
                bearish_type = DivergenceType.BEARISH_REGULAR
        elif check_bearish_hidden(indicators.highs, osc_arr, swing_highs):
            bearish_count += 1
            bearish_names.append(name)
            if bearish_type is None:
                bearish_type = DivergenceType.BEARISH_HIDDEN

    return bullish_count, bearish_count, bullish_names, bearish_names, bullish_type, bearish_type


# ---------------------------------------------------------------------------
# Trend filter (Phase 1 change #1)
# ---------------------------------------------------------------------------


def _last_valid(arr: list[Optional[float]]) -> Optional[float]:
    """Get the last non-None value from an indicator array."""
    for v in reversed(arr):
        if v is not None:
            return v
    return None


def _check_trend_filter(
    indicators: IndicatorSet,
    direction: SignalDirection,
) -> tuple[bool, str]:
    """Hard trend filter: reject counter-trend signals.

    LONG: price must be ABOVE EMA-200
    SHORT: price must be BELOW EMA-200

    This is a hard gate, not a confidence adjustment. Counter-trend
    signals are rejected entirely — they were the biggest source of
    losses in the v1 backtest.

    Returns (passed, reason).
    """
    close = indicators.closes[-1]
    ema_long = _last_valid(indicators.ema_long)

    if ema_long is None:
        return True, ""  # Not enough data, allow

    if direction == SignalDirection.LONG:
        if close < ema_long:
            return False, f"Trend filter: LONG rejected — price {close:.2f} below EMA-200 {ema_long:.2f}"
    else:
        if close > ema_long:
            return False, f"Trend filter: SHORT rejected — price {close:.2f} above EMA-200 {ema_long:.2f}"

    return True, ""


# ---------------------------------------------------------------------------
# Volume confirmation (Phase 1 change #3)
# ---------------------------------------------------------------------------


def _check_volume_confirmation(
    indicators: IndicatorSet,
    params: DetectorParams,
) -> tuple[bool, str]:
    """Require signal candle volume to be above the 20-period average.

    A divergence forming on thin volume is noise. One forming on strong
    volume has conviction behind it.

    Returns (passed, reason).
    """
    volumes = indicators.volumes
    period = params.volume_sma_period

    if len(volumes) < period + 1:
        return True, ""  # Not enough data, allow

    current_volume = volumes[-1]
    avg_volume = sum(volumes[-period - 1 : -1]) / period

    if avg_volume <= 0:
        return True, ""

    if current_volume < avg_volume:
        ratio = current_volume / avg_volume
        return False, f"Volume filter: current {current_volume:.0f} below avg {avg_volume:.0f} ({ratio:.1%})"

    return True, ""


# ---------------------------------------------------------------------------
# Entry / SL / TP calculation
# ---------------------------------------------------------------------------


def _calculate_levels(
    indicators: IndicatorSet,
    direction: SignalDirection,
    swing_lows: list[int],
    swing_highs: list[int],
    params: DetectorParams,
) -> tuple[float, float, float, float | None, float | None]:
    """Calculate entry, stop loss, and take profit levels.

    Returns (entry, stop_loss, tp1, tp2, tp3).
    """
    entry = indicators.closes[-1]
    atr = _last_valid(indicators.atr)
    if atr is None or atr == 0:
        atr = entry * 0.02  # Fallback: 2% of price

    if direction == SignalDirection.LONG:
        # Stop below recent swing low minus ATR buffer
        if swing_lows:
            recent_low = indicators.lows[swing_lows[-1]]
            stop_loss = recent_low - (0.5 * atr)
        else:
            stop_loss = entry - (params.atr_sl_multiplier * atr)

        # Enforce minimum stop distance
        if stop_loss >= entry:
            stop_loss = entry - (params.atr_sl_multiplier * atr)

        risk = entry - stop_loss
        tp1 = entry + (risk * params.min_risk_reward)
        tp2 = entry + (risk * params.min_risk_reward * 1.5)
        tp3 = entry + (risk * params.min_risk_reward * 2.0)
    else:
        # Stop above recent swing high plus ATR buffer
        if swing_highs:
            recent_high = indicators.highs[swing_highs[-1]]
            stop_loss = recent_high + (0.5 * atr)
        else:
            stop_loss = entry + (params.atr_sl_multiplier * atr)

        # Enforce minimum stop distance
        if stop_loss <= entry:
            stop_loss = entry + (params.atr_sl_multiplier * atr)

        risk = stop_loss - entry
        tp1 = entry - (risk * params.min_risk_reward)
        tp2 = entry - (risk * params.min_risk_reward * 1.5)
        tp3 = entry - (risk * params.min_risk_reward * 2.0)

    return entry, stop_loss, tp1, tp2, tp3


# ---------------------------------------------------------------------------
# Main detection function
# ---------------------------------------------------------------------------


def detect(
    indicators: IndicatorSet,
    params: DetectorParams | None = None,
) -> DivergenceSignal:
    """Run the full deterministic divergence detection pipeline (Phase 1).

    1. Find swing points in price (order=5 minimum)
    2. Scan 3 uncorrelated oscillators (RSI, MACD, OBV)
    3. Require ALL 3 to confirm
    4. Hard trend filter (price vs EMA-200)
    5. Volume confirmation (above 20-period average)
    6. Calculate entry/SL/TP levels
    7. Return DivergenceSignal
    """
    if params is None:
        params = DetectorParams()

    no_signal = DivergenceSignal(
        divergence_detected=False,
        confidence=0.0,
        reasoning="No divergence detected",
        symbol=indicators.symbol,
        timeframe=indicators.timeframe,
    )

    # Need enough data for swing detection
    min_bars = params.swing_order * 2 + 5
    if len(indicators.closes) < min_bars:
        no_signal.reasoning = f"Insufficient data: {len(indicators.closes)} bars (need {min_bars})"
        return no_signal

    # Step 1: Find swing points
    swing_highs = find_swing_highs(indicators.highs, order=params.swing_order)
    swing_lows = find_swing_lows(indicators.lows, order=params.swing_order)

    if len(swing_highs) < 2 and len(swing_lows) < 2:
        no_signal.reasoning = "Not enough swing points found"
        return no_signal

    # Step 2: Scan 3 uncorrelated oscillators
    (
        bullish_count,
        bearish_count,
        bullish_names,
        bearish_names,
        bullish_type,
        bearish_type,
    ) = _scan_divergences(indicators, swing_lows, swing_highs)

    # Step 3: Require ALL oscillators to confirm (min_confluence=3 means all 3)
    if bullish_count >= bearish_count and bullish_count >= params.min_confluence:
        direction = SignalDirection.LONG
        confluence = bullish_count
        confirming = bullish_names
        div_type = bullish_type or DivergenceType.BULLISH_REGULAR
    elif bearish_count >= params.min_confluence:
        direction = SignalDirection.SHORT
        confluence = bearish_count
        confirming = bearish_names
        div_type = bearish_type or DivergenceType.BEARISH_REGULAR
    else:
        no_signal.reasoning = (
            f"Insufficient confluence: bullish={bullish_count}/3, "
            f"bearish={bearish_count}/3 (need {params.min_confluence})"
        )
        return no_signal

    # Step 4: Hard trend filter — reject counter-trend signals entirely
    if params.require_trend_alignment:
        trend_ok, trend_reason = _check_trend_filter(indicators, direction)
        if not trend_ok:
            no_signal.reasoning = trend_reason
            return no_signal

    # Step 5: Volume confirmation — reject low-volume divergences
    if params.require_volume_confirmation:
        vol_ok, vol_reason = _check_volume_confirmation(indicators, params)
        if not vol_ok:
            no_signal.reasoning = vol_reason
            return no_signal

    # Confidence from confluence (now out of 3, not 7)
    confidence = _confluence_to_confidence(confluence)

    # Check minimum confidence
    if confidence < params.min_confidence:
        no_signal.reasoning = (
            f"Confidence {confidence:.2f} below {params.min_confidence} threshold "
            f"(confluence={confluence}/3)"
        )
        return no_signal

    # Step 6: Calculate entry/SL/TP
    entry, stop_loss, tp1, tp2, tp3 = _calculate_levels(
        indicators,
        direction,
        swing_lows,
        swing_highs,
        params,
    )

    # Build reasoning string
    reasoning = (
        f"{div_type.value} divergence — {confluence}/3 oscillators confirming "
        f"({', '.join(confirming)}). Trend-aligned. Volume confirmed. "
        f"Confidence: {confidence:.2f}"
    )

    return DivergenceSignal(
        divergence_detected=True,
        divergence_type=div_type,
        indicator=", ".join(confirming),
        confidence=confidence,
        direction=direction,
        entry_price=entry,
        stop_loss=stop_loss,
        take_profit_1=tp1,
        take_profit_2=tp2,
        take_profit_3=tp3,
        reasoning=reasoning,
        symbol=indicators.symbol,
        timeframe=indicators.timeframe,
    )
