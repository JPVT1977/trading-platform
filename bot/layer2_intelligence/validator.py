from __future__ import annotations

from loguru import logger

from bot.config import Settings
from bot.instruments import AssetClass, get_asset_class
from bot.models import DivergenceSignal, IndicatorSet, ValidationResult


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

    logger.debug(
        f"Signal validated: {signal.symbol}/{signal.timeframe} "
        f"confidence={signal.confidence:.2f}"
    )
    return ValidationResult(passed=True, reason="All validation rules passed")
