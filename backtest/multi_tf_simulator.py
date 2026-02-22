"""Multi-timeframe simulator: 4h setup + 1h trigger confirmation.

A divergence detected on the 4h chart becomes an "active setup". It only
becomes a trade when a 1h divergence in the same direction confirms it
within setup_expiry_hours (default 24h). This naturally filters out weak
signals that get immediately run over.

Trade levels use 1h entry (better timing) with 4h structural stop loss
(more meaningful level), which improves risk-reward.

Performance: The main loop iterates 1h candles (~17,500 bars / 2 years).
- SL/TP checks: every 1h bar (trivial price comparisons)
- 4h detector: only at 4h boundaries (~4,380 calls)
- 1h detector: only when an active setup exists (rare, ~100-200 calls)
- Indicators precomputed once per timeframe
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from loguru import logger

from bot.config import Settings
from bot.layer1_data.indicators import compute_indicators
from bot.layer2_intelligence.validator import validate_signal
from bot.models import (
    Candle,
    DivergenceSignal,
    IndicatorSet,
    SignalDirection,
)
from backtest.detector import DetectorParams, detect
from backtest.simulator import (
    BacktestTrade,
    FEE_RATE,
    OpenPosition,
    SLIPPAGE_RATE,
    STARTING_EQUITY,
    SimulatorResult,
    _apply_fees,
    _apply_slippage,
    _calculate_position_size,
    _check_entry_backtest,
    _slice_indicators,
)


@dataclass
class ActiveSetup:
    """A 4h divergence signal waiting for 1h confirmation."""

    signal: DivergenceSignal
    detected_at: datetime
    expires_at: datetime
    direction: SignalDirection


@dataclass
class MultiTFSimulatorResult(SimulatorResult):
    """Extends SimulatorResult with multi-TF stats."""

    setups_created: int = 0
    setups_confirmed: int = 0
    setups_expired: int = 0


def _build_4h_boundary_map(candles_4h: list[Candle]) -> list[datetime]:
    """Return sorted list of 4h candle timestamps for boundary detection."""
    return [c.timestamp.replace(tzinfo=timezone.utc) if c.timestamp.tzinfo is None
            else c.timestamp for c in candles_4h]


def _find_4h_index(ts: datetime, timestamps_4h: list[datetime]) -> int:
    """Find the index of the latest completed 4h candle at or before `ts`.

    Uses binary search for efficiency. Returns -1 if no 4h candle has
    completed yet.
    """
    # Ensure tz-aware comparison
    ts_utc = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts

    lo, hi = 0, len(timestamps_4h) - 1
    result = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if timestamps_4h[mid] <= ts_utc:
            result = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return result


def _build_confirmed_signal(
    setup: ActiveSetup,
    signal_1h: DivergenceSignal,
    candle_1h: Candle,
    detector_params: DetectorParams,
) -> DivergenceSignal:
    """Build a final trade signal using 1h entry + 4h stop loss.

    - Entry: 1h close (better timing, closer to reversal)
    - Stop loss: from the 4h signal (structural swing point on higher TF)
    - Take profit: recalculated from 1h entry + 4h SL distance x R:R ratio
    """
    entry = candle_1h.close
    stop_loss = setup.signal.stop_loss

    # Use 4h structural stop, but if it's on the wrong side of 1h entry
    # (possible if price moved significantly), fall back to 1h stop
    if setup.direction == SignalDirection.LONG:
        if stop_loss is not None and stop_loss >= entry:
            stop_loss = signal_1h.stop_loss
        if stop_loss is None or stop_loss >= entry:
            return None  # Invalid levels
        risk = entry - stop_loss
        tp1 = entry + (risk * detector_params.min_risk_reward)
        tp2 = entry + (risk * detector_params.min_risk_reward * 1.5)
        tp3 = entry + (risk * detector_params.min_risk_reward * 2.0)
    else:
        if stop_loss is not None and stop_loss <= entry:
            stop_loss = signal_1h.stop_loss
        if stop_loss is None or stop_loss <= entry:
            return None  # Invalid levels
        risk = stop_loss - entry
        tp1 = entry - (risk * detector_params.min_risk_reward)
        tp2 = entry - (risk * detector_params.min_risk_reward * 1.5)
        tp3 = entry - (risk * detector_params.min_risk_reward * 2.0)

    return DivergenceSignal(
        divergence_detected=True,
        divergence_type=setup.signal.divergence_type,
        indicator=f"4h:{setup.signal.indicator}, 1h:{signal_1h.indicator}",
        confidence=max(setup.signal.confidence, signal_1h.confidence),
        direction=setup.direction,
        entry_price=entry,
        stop_loss=stop_loss,
        take_profit_1=tp1,
        take_profit_2=tp2,
        take_profit_3=tp3,
        reasoning=(
            f"Multi-TF confirmed: 4h {setup.signal.divergence_type.value} "
            f"({setup.signal.indicator}) + 1h {signal_1h.divergence_type.value} "
            f"({signal_1h.indicator}). Entry={entry:.2f}, SL={stop_loss:.2f} (4h structural)"
        ),
        symbol=setup.signal.symbol,
        timeframe="4h+1h",
    )


def run_multi_tf_simulation(
    candles_4h: list[Candle],
    candles_1h: list[Candle],
    symbol: str,
    settings: Settings,
    detector_params: DetectorParams | None = None,
    warmup_4h: int = 200,
    warmup_1h: int = 200,
    precomputed_4h: IndicatorSet | None = None,
    precomputed_1h: IndicatorSet | None = None,
) -> MultiTFSimulatorResult:
    """Run a multi-timeframe backtest: 4h setup + 1h trigger.

    Args:
        candles_4h: 4h OHLCV data sorted by timestamp
        candles_1h: 1h OHLCV data sorted by timestamp
        symbol: Trading pair
        settings: Bot configuration
        detector_params: Tunable detector parameters
        warmup_4h: Bars before 4h detector starts (indicator warmup)
        warmup_1h: Bars before 1h detector starts (indicator warmup)
        precomputed_4h: Pre-computed 4h indicators (optimizer speedup)
        precomputed_1h: Pre-computed 1h indicators (optimizer speedup)

    Returns:
        MultiTFSimulatorResult with trades, equity curve, and multi-TF stats
    """
    if detector_params is None:
        detector_params = DetectorParams(
            min_confidence=settings.min_confidence,
            min_risk_reward=settings.min_risk_reward,
            max_position_pct=settings.max_position_pct,
        )

    setup_expiry = timedelta(hours=detector_params.setup_expiry_hours)

    equity = STARTING_EQUITY
    peak_equity = equity
    open_positions: list[OpenPosition] = []
    trades: list[BacktestTrade] = []
    equity_curve: list[tuple[datetime, float]] = []
    daily_pnl: dict[str, float] = {}
    current_day_pnl = 0.0
    current_day = ""
    pending_signal: DivergenceSignal | None = None

    active_setups: list[ActiveSetup] = []
    setups_created = 0
    setups_confirmed = 0
    setups_expired = 0

    result = MultiTFSimulatorResult(
        symbol=symbol,
        timeframe="4h+1h",
        start_date=(candles_1h[warmup_1h].timestamp
                    if len(candles_1h) > warmup_1h
                    else candles_1h[0].timestamp),
        end_date=candles_1h[-1].timestamp,
        starting_equity=STARTING_EQUITY,
        final_equity=STARTING_EQUITY,
    )

    if len(candles_1h) <= warmup_1h + 1:
        logger.warning(
            f"Not enough 1h candles: {len(candles_1h)} (warmup={warmup_1h})"
        )
        return result

    if len(candles_4h) <= warmup_4h + 1:
        logger.warning(
            f"Not enough 4h candles: {len(candles_4h)} (warmup={warmup_4h})"
        )
        return result

    # Precompute indicators once on full candle sets
    full_4h = precomputed_4h or compute_indicators(candles_4h, symbol, "4h", settings)
    full_1h = precomputed_1h or compute_indicators(candles_1h, symbol, "1h", settings)

    # Build 4h timestamp list for boundary detection
    timestamps_4h = _build_4h_boundary_map(candles_4h)

    # Track which 4h candle we last processed to avoid re-running the detector
    last_processed_4h_idx = -1

    for i_1h in range(warmup_1h, len(candles_1h)):
        candle = candles_1h[i_1h]
        candle_ts = (candle.timestamp.replace(tzinfo=timezone.utc)
                     if candle.timestamp.tzinfo is None
                     else candle.timestamp)

        # Track daily P&L reset
        day_str = candle_ts.strftime("%Y-%m-%d")
        if day_str != current_day:
            if current_day:
                daily_pnl[current_day] = current_day_pnl
            current_day = day_str
            current_day_pnl = 0.0

        # -----------------------------------------------------------------
        # 1. Fill pending signal at this candle's open (next-bar fill)
        # -----------------------------------------------------------------
        if pending_signal is not None:
            risk_check = _check_entry_backtest(
                pending_signal,
                open_positions,
                current_day_pnl,
                equity,
                settings,
            )

            if risk_check.approved:
                if risk_check.reason == "REVERSAL":
                    open_positions = [
                        p for p in open_positions if p.symbol != pending_signal.symbol
                    ]

                direction = pending_signal.direction
                fill_price = _apply_slippage(candle.open, direction, is_entry=True)
                size = _calculate_position_size(
                    pending_signal, equity, detector_params.max_position_pct
                )

                if size > 0:
                    entry_fees = _apply_fees(fill_price * size)
                    equity -= entry_fees

                    open_positions.append(
                        OpenPosition(
                            symbol=pending_signal.symbol,
                            direction=direction,
                            entry_time=candle_ts,
                            entry_price=fill_price,
                            stop_loss=pending_signal.stop_loss,
                            take_profit_1=pending_signal.take_profit_1,
                            take_profit_2=pending_signal.take_profit_2,
                            take_profit_3=pending_signal.take_profit_3,
                            quantity=size,
                            risk_per_unit=abs(fill_price - pending_signal.stop_loss),
                        )
                    )

            pending_signal = None

        # -----------------------------------------------------------------
        # 2. Check open positions for SL/TP hits
        # -----------------------------------------------------------------
        closed_indices: list[int] = []
        for idx, pos in enumerate(open_positions):
            exit_price: float | None = None
            exit_reason = ""

            if pos.direction == SignalDirection.LONG:
                if candle.low <= pos.stop_loss:
                    exit_price = pos.stop_loss
                    exit_reason = "stop_loss"
                elif candle.high >= pos.take_profit_1:
                    exit_price = pos.take_profit_1
                    exit_reason = "take_profit"
            else:
                if candle.high >= pos.stop_loss:
                    exit_price = pos.stop_loss
                    exit_reason = "stop_loss"
                elif candle.low <= pos.take_profit_1:
                    exit_price = pos.take_profit_1
                    exit_reason = "take_profit"

            if exit_price is not None:
                fill = _apply_slippage(exit_price, pos.direction, is_entry=False)
                exit_fees = _apply_fees(fill * pos.quantity)

                if pos.direction == SignalDirection.LONG:
                    pnl = (fill - pos.entry_price) * pos.quantity - exit_fees
                else:
                    pnl = (pos.entry_price - fill) * pos.quantity - exit_fees

                equity += pnl
                current_day_pnl += pnl

                r_multiple = (
                    pnl / (pos.risk_per_unit * pos.quantity)
                    if pos.risk_per_unit > 0
                    else 0.0
                )

                trades.append(
                    BacktestTrade(
                        symbol=pos.symbol,
                        timeframe="4h+1h",
                        direction=pos.direction.value,
                        entry_time=pos.entry_time,
                        exit_time=candle_ts,
                        entry_price=pos.entry_price,
                        exit_price=fill,
                        quantity=pos.quantity,
                        pnl=pnl,
                        pnl_pct=(pnl / STARTING_EQUITY) * 100,
                        r_multiple=r_multiple,
                        exit_reason=exit_reason,
                    )
                )
                closed_indices.append(idx)

        for idx in sorted(closed_indices, reverse=True):
            open_positions.pop(idx)

        # Track peak equity for drawdown
        peak_equity = max(peak_equity, equity)

        # Max drawdown circuit breaker
        if peak_equity > 0:
            drawdown_pct = (peak_equity - equity) / peak_equity * 100
            if drawdown_pct >= settings.max_drawdown_pct:
                logger.warning(
                    f"Max drawdown {drawdown_pct:.1f}% at {candle_ts} — "
                    f"closing all positions"
                )
                for pos in open_positions:
                    fill = candle.close
                    if pos.direction == SignalDirection.LONG:
                        pnl = (fill - pos.entry_price) * pos.quantity
                    else:
                        pnl = (pos.entry_price - fill) * pos.quantity
                    equity += pnl
                    trades.append(
                        BacktestTrade(
                            symbol=pos.symbol,
                            timeframe="4h+1h",
                            direction=pos.direction.value,
                            entry_time=pos.entry_time,
                            exit_time=candle_ts,
                            entry_price=pos.entry_price,
                            exit_price=fill,
                            quantity=pos.quantity,
                            pnl=pnl,
                            pnl_pct=(pnl / STARTING_EQUITY) * 100,
                            r_multiple=(
                                pnl / (pos.risk_per_unit * pos.quantity)
                                if pos.risk_per_unit > 0 else 0.0
                            ),
                            exit_reason="drawdown_breaker",
                        )
                    )
                open_positions.clear()
                break

        # Record equity
        equity_curve.append((candle_ts, equity))

        # -----------------------------------------------------------------
        # 3. At 4h boundaries: run 4h detector
        # -----------------------------------------------------------------
        i_4h = _find_4h_index(candle_ts, timestamps_4h)

        if i_4h >= warmup_4h and i_4h != last_processed_4h_idx:
            last_processed_4h_idx = i_4h

            sliced_4h = _slice_indicators(full_4h, i_4h + 1)
            signal_4h = detect(sliced_4h, params=detector_params)

            if signal_4h.divergence_detected:
                validation = validate_signal(signal_4h, sliced_4h, settings)
                if validation.passed and signal_4h.direction is not None:
                    setup_ts = timestamps_4h[i_4h]
                    active_setups.append(
                        ActiveSetup(
                            signal=signal_4h,
                            detected_at=setup_ts,
                            expires_at=setup_ts + setup_expiry,
                            direction=signal_4h.direction,
                        )
                    )
                    setups_created += 1

        # -----------------------------------------------------------------
        # 4. Expire old setups
        # -----------------------------------------------------------------
        before_count = len(active_setups)
        active_setups = [s for s in active_setups if candle_ts < s.expires_at]
        setups_expired += before_count - len(active_setups)

        # -----------------------------------------------------------------
        # 5. If active setups exist, run 1h detector for confirmation
        # -----------------------------------------------------------------
        if active_setups and pending_signal is None:
            sliced_1h = _slice_indicators(full_1h, i_1h + 1)
            signal_1h = detect(sliced_1h, params=detector_params)

            if signal_1h.divergence_detected and signal_1h.direction is not None:
                for setup in active_setups:
                    if setup.direction == signal_1h.direction:
                        confirmed = _build_confirmed_signal(
                            setup, signal_1h, candle, detector_params
                        )
                        if confirmed is not None:
                            # Validate the combined signal
                            val = validate_signal(confirmed, sliced_1h, settings)
                            if val.passed:
                                pending_signal = confirmed
                                active_setups.remove(setup)
                                setups_confirmed += 1
                                break

    # End of data: close remaining open positions
    if open_positions:
        last_candle = candles_1h[-1]
        last_ts = (last_candle.timestamp.replace(tzinfo=timezone.utc)
                   if last_candle.timestamp.tzinfo is None
                   else last_candle.timestamp)
        for pos in open_positions:
            fill = last_candle.close
            if pos.direction == SignalDirection.LONG:
                pnl = (fill - pos.entry_price) * pos.quantity
            else:
                pnl = (pos.entry_price - fill) * pos.quantity
            equity += pnl
            trades.append(
                BacktestTrade(
                    symbol=pos.symbol,
                    timeframe="4h+1h",
                    direction=pos.direction.value,
                    entry_time=pos.entry_time,
                    exit_time=last_ts,
                    entry_price=pos.entry_price,
                    exit_price=fill,
                    quantity=pos.quantity,
                    pnl=pnl,
                    pnl_pct=(pnl / STARTING_EQUITY) * 100,
                    r_multiple=(
                        pnl / (pos.risk_per_unit * pos.quantity)
                        if pos.risk_per_unit > 0 else 0.0
                    ),
                    exit_reason="end_of_data",
                )
            )

    # Record final daily P&L
    if current_day:
        daily_pnl[current_day] = current_day_pnl

    # Count remaining active setups as expired
    setups_expired += len(active_setups)

    result.final_equity = equity
    result.trades = trades
    result.equity_curve = equity_curve
    result.daily_pnl = daily_pnl
    result.setups_created = setups_created
    result.setups_confirmed = setups_confirmed
    result.setups_expired = setups_expired

    logger.info(
        f"Multi-TF simulation: {setups_created} setups created, "
        f"{setups_confirmed} confirmed, {setups_expired} expired, "
        f"{len(trades)} trades"
    )

    return result
