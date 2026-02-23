from __future__ import annotations

from loguru import logger

from bot.config import Settings
from bot.instruments import AssetClass, get_asset_class
from bot.models import DivergenceSignal, IndicatorSet, SignalDirection, ValidationResult


def _last_valid(arr: list[float | None]) -> float | None:
    """Get the last non-None value from an indicator array."""
    for v in reversed(arr):
        if v is not None:
            return v
    return None


def validate_signal(
    signal: DivergenceSignal,
    indicators: IndicatorSet,
    settings: Settings,
) -> ValidationResult:
    """Deterministic signal validation. 6 hard-coded rules. <1ms execution.

    This replaces the blueprint's two-pass Claude validation approach.
    Zero API calls. Pure Python logic. Fully testable.
    """

    # Rule 0: Must have a direction
    if signal.direction is None:
        return ValidationResult(
            passed=False,
            reason="Signal has no direction (long/short)",
        )

    # Rule 1: Minimum confidence threshold
    # NOTE: Per-broker confidence thresholds are applied in main.py after validation.
    # This global check acts as an absolute floor across all brokers.
    if signal.confidence < settings.min_confidence:
        return ValidationResult(
            passed=False,
            reason=f"Confidence {signal.confidence:.2f} below {settings.min_confidence} threshold",
        )

    # Rule 2: Must have entry, stop loss, and at least one take profit
    if any(v is None for v in [signal.entry_price, signal.stop_loss, signal.take_profit_1]):
        return ValidationResult(
            passed=False,
            reason="Missing entry_price, stop_loss, or take_profit_1",
        )

    # Rule 3: Stop loss must be on the correct side of entry
    if signal.direction is not None and signal.entry_price and signal.stop_loss:
        if signal.direction.value == "long":
            if signal.stop_loss >= signal.entry_price:
                return ValidationResult(
                    passed=False,
                    reason="Long signal: stop_loss must be below entry_price",
                )
            if signal.take_profit_1 and signal.take_profit_1 <= signal.entry_price:
                return ValidationResult(
                    passed=False,
                    reason="Long signal: take_profit_1 must be above entry_price",
                )
        elif signal.direction.value == "short":
            if signal.stop_loss <= signal.entry_price:
                return ValidationResult(
                    passed=False,
                    reason="Short signal: stop_loss must be above entry_price",
                )
            if signal.take_profit_1 and signal.take_profit_1 >= signal.entry_price:
                return ValidationResult(
                    passed=False,
                    reason="Short signal: take_profit_1 must be below entry_price",
                )

    # Rule 4: Minimum risk/reward ratio
    if signal.entry_price and signal.stop_loss and signal.take_profit_1:
        risk = abs(signal.entry_price - signal.stop_loss)
        reward = abs(signal.take_profit_1 - signal.entry_price)
        if risk == 0:
            return ValidationResult(passed=False, reason="Zero risk distance (entry == stop_loss)")
        rr_ratio = reward / risk
        if rr_ratio < settings.min_risk_reward - 0.01:
            return ValidationResult(
                passed=False,
                reason=f"R:R ratio {rr_ratio:.2f} below {settings.min_risk_reward} minimum",
            )

    # Rule 5: RSI must not contradict the signal direction
    latest_rsi = _last_valid(indicators.rsi)
    if latest_rsi is not None and signal.direction is not None:
        if signal.direction.value == "long" and latest_rsi > 80:
            return ValidationResult(
                passed=False,
                reason=f"Long signal but RSI={latest_rsi:.1f} is extremely overbought (>80)",
            )
        if signal.direction.value == "short" and latest_rsi < 20:
            return ValidationResult(
                passed=False,
                reason=f"Short signal but RSI={latest_rsi:.1f} is extremely oversold (<20)",
            )

    # Rule 6: Stop loss distance must be within reasonable ATR range (0.5-5x)
    latest_atr = _last_valid(indicators.atr)
    if latest_atr and latest_atr > 0 and signal.entry_price and signal.stop_loss:
        stop_distance = abs(signal.entry_price - signal.stop_loss)
        atr_multiple = stop_distance / latest_atr
        if atr_multiple < 0.5:
            return ValidationResult(
                passed=False,
                reason=f"Stop too tight: {atr_multiple:.1f}x ATR (minimum 0.5x)",
            )
        if atr_multiple > 5.0:
            return ValidationResult(
                passed=False,
                reason=f"Stop too wide: {atr_multiple:.1f}x ATR (maximum 5.0x)",
            )

    # Rule 7: ADX trend strength — reject crypto signals in choppy markets
    latest_adx = _last_valid(indicators.adx)
    if latest_adx is not None:
        asset_class = get_asset_class(signal.symbol)
        if asset_class == AssetClass.CRYPTO and latest_adx < 20:
            return ValidationResult(
                passed=False,
                reason=f"Crypto market too choppy: ADX={latest_adx:.1f} (minimum 20)",
            )

    # Rule 8: Counter-trend in ranging market — ADX < 25 + flat 200 EMA
    if latest_adx is not None and latest_adx < 25 and signal.direction is not None:
        ema_long_vals = [v for v in indicators.ema_long if v is not None]
        if len(ema_long_vals) >= 10:
            ema_now = ema_long_vals[-1]
            ema_10_ago = ema_long_vals[-10]
            if ema_10_ago != 0:
                ema_slope_pct = abs(ema_now - ema_10_ago) / abs(ema_10_ago) * 100
                if ema_slope_pct < 0.05:
                    return ValidationResult(
                        passed=False,
                        reason=(
                            f"Ranging market: ADX={latest_adx:.1f}, "
                            f"EMA200 slope={ema_slope_pct:.3f}% "
                            f"— divergence unreliable"
                        ),
                    )

    # Rule 9: Oscillator stack — require minimum confirming indicators
    if signal.divergence_detected and signal.confirming_indicators is not None:
        if len(signal.confirming_indicators) < settings.min_confirming_indicators:
            return ValidationResult(
                passed=False,
                reason=(
                    f"Only {len(signal.confirming_indicators)} confirming indicator(s) "
                    f"(minimum {settings.min_confirming_indicators})"
                ),
            )

    # Rule 10: Swing length — reject too-short divergences
    if signal.swing_length_bars is not None:
        min_bars = (
            settings.min_swing_bars_4h if "4h" in signal.timeframe
            else settings.min_swing_bars_1h
        )
        if signal.swing_length_bars < min_bars:
            return ValidationResult(
                passed=False,
                reason=(
                    f"Swing length {signal.swing_length_bars} bars below "
                    f"minimum {min_bars} for {signal.timeframe}"
                ),
            )

    # Rule 11: Divergence magnitude — RSI must show meaningful change
    if (
        signal.divergence_magnitude is not None
        and signal.indicator == "RSI"
        and signal.divergence_magnitude < settings.min_divergence_magnitude_rsi
    ):
        return ValidationResult(
            passed=False,
            reason=(
                f"RSI divergence magnitude {signal.divergence_magnitude:.1f} "
                f"below minimum {settings.min_divergence_magnitude_rsi}"
            ),
        )

    # Rule 12: Zero volume guard — reject if recent volume is zero/near-zero
    if indicators.volumes and len(indicators.volumes) >= 3:
        recent_vols = indicators.volumes[-3:]
        if any(v == 0 for v in recent_vols):
            return ValidationResult(
                passed=False,
                reason="Zero volume detected in last 3 bars",
            )
        vol_sma_last = _last_valid(indicators.volume_sma) if indicators.volume_sma else None
        if vol_sma_last and vol_sma_last > 0:
            max_recent = max(recent_vols)
            if max_recent < vol_sma_last * 0.01:
                return ValidationResult(
                    passed=False,
                    reason=(
                        f"Near-zero volume: max recent {max_recent:.2f} "
                        f"< 1% of volume SMA {vol_sma_last:.2f}"
                    ),
                )

    # Rule 13: Low volume — reject if current volume < 50% of SMA
    if indicators.volume_sma and indicators.volumes:
        vol_sma_last = _last_valid(indicators.volume_sma)
        if vol_sma_last and vol_sma_last > 0:
            current_vol = indicators.volumes[-1]
            if current_vol < vol_sma_last * settings.volume_low_threshold:
                return ValidationResult(
                    passed=False,
                    reason=(
                        f"Low volume: {current_vol:.2f} < "
                        f"{settings.volume_low_threshold * 100:.0f}% of "
                        f"SMA({settings.volume_sma_period}) {vol_sma_last:.2f}"
                    ),
                )

    # Rule 14: Candle gate — require reversal candlestick pattern
    if indicators.candle_patterns and signal.direction is not None:
        lookback = settings.candle_gate_lookback
        bullish_patterns = ["hammer", "inverted_hammer", "piercing", "morning_star"]
        bearish_patterns = ["shooting_star", "hanging_man", "dark_cloud", "evening_star"]

        found_pattern = False
        if signal.direction == SignalDirection.LONG:
            for name in bullish_patterns:
                if name in indicators.candle_patterns:
                    vals = indicators.candle_patterns[name][-lookback:]
                    if any(v > 0 for v in vals):
                        found_pattern = True
                        break
            # Also check bullish engulfing (+100)
            if not found_pattern and "engulfing" in indicators.candle_patterns:
                vals = indicators.candle_patterns["engulfing"][-lookback:]
                if any(v > 0 for v in vals):
                    found_pattern = True
        elif signal.direction == SignalDirection.SHORT:
            # TA-Lib single-direction bearish patterns return +100 when detected
            for name in bearish_patterns:
                if name in indicators.candle_patterns:
                    vals = indicators.candle_patterns[name][-lookback:]
                    if any(v != 0 for v in vals):
                        found_pattern = True
                        break
            # Engulfing is bidirectional: -100 = bearish, +100 = bullish
            if not found_pattern and "engulfing" in indicators.candle_patterns:
                vals = indicators.candle_patterns["engulfing"][-lookback:]
                if any(v < 0 for v in vals):
                    found_pattern = True

        if not found_pattern:
            direction_label = "bullish" if signal.direction == SignalDirection.LONG else "bearish"
            return ValidationResult(
                passed=False,
                reason=f"No {direction_label} reversal candlestick in last {lookback} bars",
            )

    logger.debug(
        f"Signal validated: {signal.symbol}/{signal.timeframe} "
        f"confidence={signal.confidence:.2f}"
    )
    return ValidationResult(passed=True, reason="All validation rules passed")
