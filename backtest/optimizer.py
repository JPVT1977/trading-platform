"""Walk-forward optimization engine.

Prevents overfitting by splitting data into in-sample/out-of-sample windows:
- 9-month IS / 3-month OOS
- 3-month stride, ~5 windows over 2 years
- Optimize parameters on IS, validate on OOS
- Only parameters that work across ALL windows survive
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from loguru import logger

from bot.config import Settings
from bot.models import Candle
from bot.layer1_data.indicators import compute_indicators

from backtest.detector import DetectorParams
from backtest.multi_tf_simulator import MultiTFSimulatorResult, run_multi_tf_simulation
from backtest.report import compute_metrics
from backtest.simulator import SimulatorResult, run_simulation

# Parameter grid (Phase 1: 3 uncorrelated oscillators, trend + volume filters)
PARAM_GRID = {
    "swing_order": [3, 5, 7],
    "min_confluence": [2, 3],           # 2-of-3 or all-3 (max is 3 now)
    "min_confidence": [0.6, 0.7, 0.8],
    "min_risk_reward": [1.5, 2.0, 2.5],
    "max_position_pct": [1.0, 2.0, 3.0],
    "atr_sl_multiplier": [1.0, 1.5, 2.0],
}

# Total: 3 x 2 x 3 x 3 x 3 x 3 = 486 combinations


@dataclass
class WindowResult:
    """Result for one parameter combo on one IS/OOS window."""

    window_idx: int
    is_start: datetime
    is_end: datetime
    oos_start: datetime
    oos_end: datetime
    params: DetectorParams
    is_sharpe: float
    is_return_pct: float
    is_trades: int
    oos_sharpe: float
    oos_return_pct: float
    oos_trades: int


@dataclass
class OptimizationResult:
    """Full output from walk-forward optimization."""

    symbol: str
    timeframe: str
    total_combos: int
    windows: int
    best_params: DetectorParams | None = None
    best_oos_sharpe: float = 0.0
    window_results: list[WindowResult] = field(default_factory=list)
    robust_combos: list[tuple[DetectorParams, float]] = field(default_factory=list)


def _generate_param_combos() -> list[DetectorParams]:
    """Generate all parameter combinations from the grid."""
    keys = list(PARAM_GRID.keys())
    values = list(PARAM_GRID.values())
    combos: list[DetectorParams] = []

    for combo in itertools.product(*values):
        params_dict = dict(zip(keys, combo))
        combos.append(DetectorParams(**params_dict))

    return combos


def _split_candles(
    candles: list[Candle],
    start: datetime,
    end: datetime,
) -> list[Candle]:
    """Filter candles to a date range."""
    start_utc = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
    end_utc = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end
    return [
        c for c in candles
        if start_utc <= c.timestamp.replace(tzinfo=timezone.utc) <= end_utc
    ]


def _generate_windows(
    data_start: datetime,
    data_end: datetime,
    is_months: int = 9,
    oos_months: int = 3,
    stride_months: int = 3,
) -> list[tuple[datetime, datetime, datetime, datetime]]:
    """Generate IS/OOS window boundaries.

    Returns list of (is_start, is_end, oos_start, oos_end).
    """
    windows: list[tuple[datetime, datetime, datetime, datetime]] = []
    current = data_start

    while True:
        is_start = current
        is_end = is_start + timedelta(days=is_months * 30)
        oos_start = is_end
        oos_end = oos_start + timedelta(days=oos_months * 30)

        if oos_end > data_end:
            break

        windows.append((is_start, is_end, oos_start, oos_end))
        current += timedelta(days=stride_months * 30)

    return windows


def run_optimization(
    candles: list[Candle],
    symbol: str,
    timeframe: str,
    settings: Settings,
    top_n: int = 10,
    progress_callback: callable | None = None,
) -> OptimizationResult:
    """Run walk-forward optimization across all parameter combos.

    Args:
        candles: Full historical data (2+ years)
        symbol: Trading pair
        timeframe: Candle interval
        settings: Bot configuration
        top_n: Number of top combos per window to consider
        progress_callback: Optional callback(current, total) for progress

    Returns:
        OptimizationResult with robust parameter set
    """
    combos = _generate_param_combos()
    total_combos = len(combos)

    data_start = candles[0].timestamp.replace(tzinfo=timezone.utc)
    data_end = candles[-1].timestamp.replace(tzinfo=timezone.utc)
    windows = _generate_windows(data_start, data_end)

    logger.info(
        f"Walk-forward optimization: {total_combos} combos x {len(windows)} windows "
        f"= {total_combos * len(windows)} simulations"
    )

    result = OptimizationResult(
        symbol=symbol,
        timeframe=timeframe,
        total_combos=total_combos,
        windows=len(windows),
    )

    if not windows:
        logger.warning("Not enough data for walk-forward windows")
        return result

    # Track top combos per window
    # Key: combo index, Value: list of (window_idx, oos_sharpe)
    combo_scores: dict[int, list[tuple[int, float]]] = {i: [] for i in range(total_combos)}

    total_sims = total_combos * len(windows)
    completed = 0

    for w_idx, (is_start, is_end, oos_start, oos_end) in enumerate(windows):
        logger.info(
            f"Window {w_idx + 1}/{len(windows)}: "
            f"IS [{is_start:%Y-%m-%d} -> {is_end:%Y-%m-%d}] "
            f"OOS [{oos_start:%Y-%m-%d} -> {oos_end:%Y-%m-%d}]"
        )

        is_candles = _split_candles(candles, is_start, is_end)
        oos_candles = _split_candles(candles, oos_start, oos_end)

        if len(is_candles) < 250 or len(oos_candles) < 50:
            logger.warning(
                f"  Skipping window {w_idx + 1}: insufficient data "
                f"(IS={len(is_candles)}, OOS={len(oos_candles)})"
            )
            continue

        # Precompute indicators ONCE per window (biggest speedup)
        is_warmup = min(200, len(is_candles) // 3)
        oos_warmup = min(200, len(oos_candles) // 3)
        is_indicators = compute_indicators(is_candles, symbol, timeframe, settings)
        oos_indicators = compute_indicators(oos_candles, symbol, timeframe, settings)

        # Score each combo on IS data
        is_scores: list[tuple[int, float]] = []

        for c_idx, params in enumerate(combos):
            is_result = run_simulation(
                is_candles, symbol, timeframe, settings,
                detector_params=params, warmup=is_warmup,
                precomputed_indicators=is_indicators,
            )
            is_metrics = compute_metrics(is_result)
            is_sharpe = is_metrics["sharpe_ratio"]

            # Only consider combos with at least 5 trades on IS
            if is_metrics["total_trades"] >= 5:
                is_scores.append((c_idx, is_sharpe))

            completed += 1
            if progress_callback and completed % 50 == 0:
                progress_callback(completed, total_sims)

        # Rank by IS Sharpe, take top N
        is_scores.sort(key=lambda x: x[1], reverse=True)
        top_combos = is_scores[:top_n]

        # Validate top combos on OOS data
        for c_idx, is_sharpe in top_combos:
            params = combos[c_idx]
            oos_result = run_simulation(
                oos_candles, symbol, timeframe, settings,
                detector_params=params, warmup=oos_warmup,
                precomputed_indicators=oos_indicators,
            )
            oos_metrics = compute_metrics(oos_result)

            wr = WindowResult(
                window_idx=w_idx,
                is_start=is_start,
                is_end=is_end,
                oos_start=oos_start,
                oos_end=oos_end,
                params=params,
                is_sharpe=is_sharpe,
                is_return_pct=0.0,  # Computed during IS run
                is_trades=0,
                oos_sharpe=oos_metrics["sharpe_ratio"],
                oos_return_pct=oos_metrics["total_return_pct"],
                oos_trades=oos_metrics["total_trades"],
            )
            result.window_results.append(wr)
            combo_scores[c_idx].append((w_idx, oos_metrics["sharpe_ratio"]))

    # Find robust combos: appeared in top N across ALL windows
    num_windows = len(windows)
    for c_idx, scores in combo_scores.items():
        if len(scores) >= num_windows and num_windows > 0:
            avg_oos_sharpe = sum(s for _, s in scores) / len(scores)
            result.robust_combos.append((combos[c_idx], avg_oos_sharpe))

    # Sort robust combos by avg OOS Sharpe
    result.robust_combos.sort(key=lambda x: x[1], reverse=True)

    if result.robust_combos:
        best_params, best_sharpe = result.robust_combos[0]
        result.best_params = best_params
        result.best_oos_sharpe = best_sharpe
        logger.info(
            f"Best robust params: swing_order={best_params.swing_order}, "
            f"min_confluence={best_params.min_confluence}, "
            f"min_confidence={best_params.min_confidence}, "
            f"min_risk_reward={best_params.min_risk_reward}, "
            f"max_position_pct={best_params.max_position_pct}, "
            f"atr_sl_multiplier={best_params.atr_sl_multiplier} "
            f"(avg OOS Sharpe: {best_sharpe:.2f})"
        )
    else:
        logger.warning(
            "No robust parameter set found across all windows. "
            "Using median of top combos."
        )
        # Fallback: find the combo with best average OOS Sharpe across available windows
        best_avg = -999.0
        best_idx = 0
        for c_idx, scores in combo_scores.items():
            if scores:
                avg = sum(s for _, s in scores) / len(scores)
                if avg > best_avg:
                    best_avg = avg
                    best_idx = c_idx
        result.best_params = combos[best_idx]
        result.best_oos_sharpe = best_avg

    return result


def print_optimization_report(opt_result: OptimizationResult) -> None:
    """Print walk-forward optimization results to console."""
    print()
    print("=" * 65)
    print(f"  WALK-FORWARD OPTIMIZATION: {opt_result.symbol} {opt_result.timeframe}")
    print(f"  Combos tested: {opt_result.total_combos} | Windows: {opt_result.windows}")
    print("=" * 65)

    if opt_result.best_params:
        p = opt_result.best_params
        print(f"  Best Parameters:")
        print(f"    swing_order:      {p.swing_order}")
        print(f"    min_confluence:   {p.min_confluence}")
        print(f"    min_confidence:   {p.min_confidence}")
        print(f"    min_risk_reward:  {p.min_risk_reward}")
        print(f"    max_position_pct: {p.max_position_pct}%")
        print(f"    atr_sl_multiplier:{p.atr_sl_multiplier}")
        print(f"  Avg OOS Sharpe:     {opt_result.best_oos_sharpe:.2f}")
    else:
        print("  No robust parameters found.")

    print(f"\n  Robust combos found: {len(opt_result.robust_combos)}")
    for i, (params, sharpe) in enumerate(opt_result.robust_combos[:5], 1):
        print(
            f"    #{i}: confluence={params.min_confluence}, "
            f"confidence={params.min_confidence}, "
            f"rr={params.min_risk_reward}, "
            f"Sharpe={sharpe:.2f}"
        )

    # Window comparison
    if opt_result.window_results:
        print(f"\n  Window Results (top entries):")
        print(f"  {'Window':<10} {'IS Sharpe':>10} {'OOS Sharpe':>11} {'OOS Return':>11} {'OOS Trades':>11}")
        print(f"  {'-'*53}")

        # Show best per window
        by_window: dict[int, list[WindowResult]] = {}
        for wr in opt_result.window_results:
            by_window.setdefault(wr.window_idx, []).append(wr)

        for w_idx in sorted(by_window.keys()):
            wrs = sorted(by_window[w_idx], key=lambda x: x.oos_sharpe, reverse=True)
            best = wrs[0]
            print(
                f"  W{best.window_idx + 1:<9} {best.is_sharpe:>10.2f} "
                f"{best.oos_sharpe:>11.2f} {best.oos_return_pct:>10.1f}% "
                f"{best.oos_trades:>11}"
            )

    print("=" * 65)
    print()


# ---------------------------------------------------------------------------
# Phase 2: Multi-timeframe optimization
# ---------------------------------------------------------------------------

MULTI_TF_PARAM_GRID = {
    "swing_order": [3, 5, 7],
    "min_confluence": [2, 3],
    "min_confidence": [0.6, 0.7, 0.8],
    "min_risk_reward": [1.5, 2.0, 2.5],
    "max_position_pct": [1.0, 2.0, 3.0],
    "atr_sl_multiplier": [1.0, 1.5, 2.0],
    "setup_expiry_hours": [12, 24, 48],
}

# Total: 3 x 2 x 3 x 3 x 3 x 3 x 3 = 1,458 combinations


def _generate_multi_tf_param_combos() -> list[DetectorParams]:
    """Generate all parameter combinations from the multi-TF grid."""
    keys = list(MULTI_TF_PARAM_GRID.keys())
    values = list(MULTI_TF_PARAM_GRID.values())
    combos: list[DetectorParams] = []

    for combo in itertools.product(*values):
        params_dict = dict(zip(keys, combo))
        combos.append(DetectorParams(**params_dict))

    return combos


def run_multi_tf_optimization(
    candles_4h: list[Candle],
    candles_1h: list[Candle],
    symbol: str,
    settings: Settings,
    top_n: int = 10,
    progress_callback: callable | None = None,
) -> OptimizationResult:
    """Run walk-forward optimization for multi-TF (4h setup + 1h trigger).

    Same walk-forward window logic as single-TF, but:
    - Precomputes both 4h AND 1h indicators per window
    - Runs run_multi_tf_simulation() for each parameter combo
    - Includes setup_expiry_hours in the param grid

    Args:
        candles_4h: Full 4h historical data (2+ years)
        candles_1h: Full 1h historical data (2+ years)
        symbol: Trading pair
        settings: Bot configuration
        top_n: Number of top combos per window to consider
        progress_callback: Optional callback(current, total) for progress

    Returns:
        OptimizationResult with robust parameter set
    """
    combos = _generate_multi_tf_param_combos()
    total_combos = len(combos)

    # Use 4h timestamps for window generation (coarser granularity)
    data_start = candles_4h[0].timestamp.replace(tzinfo=timezone.utc)
    data_end = candles_4h[-1].timestamp.replace(tzinfo=timezone.utc)
    windows = _generate_windows(data_start, data_end)

    logger.info(
        f"Multi-TF walk-forward optimization: {total_combos} combos x {len(windows)} windows "
        f"= {total_combos * len(windows)} simulations"
    )

    result = OptimizationResult(
        symbol=symbol,
        timeframe="4h+1h",
        total_combos=total_combos,
        windows=len(windows),
    )

    if not windows:
        logger.warning("Not enough data for walk-forward windows")
        return result

    combo_scores: dict[int, list[tuple[int, float]]] = {i: [] for i in range(total_combos)}

    total_sims = total_combos * len(windows)
    completed = 0

    for w_idx, (is_start, is_end, oos_start, oos_end) in enumerate(windows):
        logger.info(
            f"Window {w_idx + 1}/{len(windows)}: "
            f"IS [{is_start:%Y-%m-%d} -> {is_end:%Y-%m-%d}] "
            f"OOS [{oos_start:%Y-%m-%d} -> {oos_end:%Y-%m-%d}]"
        )

        is_candles_4h = _split_candles(candles_4h, is_start, is_end)
        is_candles_1h = _split_candles(candles_1h, is_start, is_end)
        oos_candles_4h = _split_candles(candles_4h, oos_start, oos_end)
        oos_candles_1h = _split_candles(candles_1h, oos_start, oos_end)

        if (len(is_candles_4h) < 250 or len(is_candles_1h) < 500
                or len(oos_candles_4h) < 50 or len(oos_candles_1h) < 100):
            logger.warning(
                f"  Skipping window {w_idx + 1}: insufficient data "
                f"(IS 4h={len(is_candles_4h)}, 1h={len(is_candles_1h)}, "
                f"OOS 4h={len(oos_candles_4h)}, 1h={len(oos_candles_1h)})"
            )
            continue

        # Precompute indicators ONCE per window for both timeframes
        is_warmup_4h = min(200, len(is_candles_4h) // 3)
        is_warmup_1h = min(200, len(is_candles_1h) // 3)
        oos_warmup_4h = min(200, len(oos_candles_4h) // 3)
        oos_warmup_1h = min(200, len(oos_candles_1h) // 3)

        is_indicators_4h = compute_indicators(is_candles_4h, symbol, "4h", settings)
        is_indicators_1h = compute_indicators(is_candles_1h, symbol, "1h", settings)
        oos_indicators_4h = compute_indicators(oos_candles_4h, symbol, "4h", settings)
        oos_indicators_1h = compute_indicators(oos_candles_1h, symbol, "1h", settings)

        # Score each combo on IS data
        is_scores: list[tuple[int, float]] = []

        for c_idx, params in enumerate(combos):
            is_result = run_multi_tf_simulation(
                candles_4h=is_candles_4h,
                candles_1h=is_candles_1h,
                symbol=symbol,
                settings=settings,
                detector_params=params,
                warmup_4h=is_warmup_4h,
                warmup_1h=is_warmup_1h,
                precomputed_4h=is_indicators_4h,
                precomputed_1h=is_indicators_1h,
            )
            is_metrics = compute_metrics(is_result)
            is_sharpe = is_metrics["sharpe_ratio"]

            if is_metrics["total_trades"] >= 3:
                is_scores.append((c_idx, is_sharpe))

            completed += 1
            if progress_callback and completed % 50 == 0:
                progress_callback(completed, total_sims)

        # Rank by IS Sharpe, take top N
        is_scores.sort(key=lambda x: x[1], reverse=True)
        top_combos = is_scores[:top_n]

        # Validate top combos on OOS data
        for c_idx, is_sharpe in top_combos:
            params = combos[c_idx]
            oos_result = run_multi_tf_simulation(
                candles_4h=oos_candles_4h,
                candles_1h=oos_candles_1h,
                symbol=symbol,
                settings=settings,
                detector_params=params,
                warmup_4h=oos_warmup_4h,
                warmup_1h=oos_warmup_1h,
                precomputed_4h=oos_indicators_4h,
                precomputed_1h=oos_indicators_1h,
            )
            oos_metrics = compute_metrics(oos_result)

            wr = WindowResult(
                window_idx=w_idx,
                is_start=is_start,
                is_end=is_end,
                oos_start=oos_start,
                oos_end=oos_end,
                params=params,
                is_sharpe=is_sharpe,
                is_return_pct=0.0,
                is_trades=0,
                oos_sharpe=oos_metrics["sharpe_ratio"],
                oos_return_pct=oos_metrics["total_return_pct"],
                oos_trades=oos_metrics["total_trades"],
            )
            result.window_results.append(wr)
            combo_scores[c_idx].append((w_idx, oos_metrics["sharpe_ratio"]))

    # Find robust combos: appeared in top N across ALL windows
    num_windows = len(windows)
    for c_idx, scores in combo_scores.items():
        if len(scores) >= num_windows and num_windows > 0:
            avg_oos_sharpe = sum(s for _, s in scores) / len(scores)
            result.robust_combos.append((combos[c_idx], avg_oos_sharpe))

    result.robust_combos.sort(key=lambda x: x[1], reverse=True)

    if result.robust_combos:
        best_params, best_sharpe = result.robust_combos[0]
        result.best_params = best_params
        result.best_oos_sharpe = best_sharpe
        logger.info(
            f"Best multi-TF robust params: swing_order={best_params.swing_order}, "
            f"min_confluence={best_params.min_confluence}, "
            f"min_confidence={best_params.min_confidence}, "
            f"min_risk_reward={best_params.min_risk_reward}, "
            f"max_position_pct={best_params.max_position_pct}, "
            f"atr_sl_multiplier={best_params.atr_sl_multiplier}, "
            f"setup_expiry_hours={best_params.setup_expiry_hours} "
            f"(avg OOS Sharpe: {best_sharpe:.2f})"
        )
    else:
        logger.warning(
            "No robust multi-TF parameter set found across all windows. "
            "Using best average across available windows."
        )
        best_avg = -999.0
        best_idx = 0
        for c_idx, scores in combo_scores.items():
            if scores:
                avg = sum(s for _, s in scores) / len(scores)
                if avg > best_avg:
                    best_avg = avg
                    best_idx = c_idx
        result.best_params = combos[best_idx]
        result.best_oos_sharpe = best_avg

    return result


def print_multi_tf_optimization_report(opt_result: OptimizationResult) -> None:
    """Print multi-TF walk-forward optimization results to console."""
    print()
    print("=" * 70)
    print(f"  MULTI-TF WALK-FORWARD OPTIMIZATION: {opt_result.symbol}")
    print(f"  Combos tested: {opt_result.total_combos} | Windows: {opt_result.windows}")
    print("=" * 70)

    if opt_result.best_params:
        p = opt_result.best_params
        print(f"  Best Parameters:")
        print(f"    swing_order:        {p.swing_order}")
        print(f"    min_confluence:     {p.min_confluence}")
        print(f"    min_confidence:     {p.min_confidence}")
        print(f"    min_risk_reward:    {p.min_risk_reward}")
        print(f"    max_position_pct:   {p.max_position_pct}%")
        print(f"    atr_sl_multiplier:  {p.atr_sl_multiplier}")
        print(f"    setup_expiry_hours: {p.setup_expiry_hours}h")
        print(f"  Avg OOS Sharpe:       {opt_result.best_oos_sharpe:.2f}")
    else:
        print("  No robust parameters found.")

    print(f"\n  Robust combos found: {len(opt_result.robust_combos)}")
    for i, (params, sharpe) in enumerate(opt_result.robust_combos[:5], 1):
        print(
            f"    #{i}: confluence={params.min_confluence}, "
            f"confidence={params.min_confidence}, "
            f"rr={params.min_risk_reward}, "
            f"expiry={params.setup_expiry_hours}h, "
            f"Sharpe={sharpe:.2f}"
        )

    if opt_result.window_results:
        print(f"\n  Window Results (top entries):")
        print(f"  {'Window':<10} {'IS Sharpe':>10} {'OOS Sharpe':>11} {'OOS Return':>11} {'OOS Trades':>11}")
        print(f"  {'-'*53}")

        by_window: dict[int, list[WindowResult]] = {}
        for wr in opt_result.window_results:
            by_window.setdefault(wr.window_idx, []).append(wr)

        for w_idx in sorted(by_window.keys()):
            wrs = sorted(by_window[w_idx], key=lambda x: x.oos_sharpe, reverse=True)
            best = wrs[0]
            print(
                f"  W{best.window_idx + 1:<9} {best.is_sharpe:>10.2f} "
                f"{best.oos_sharpe:>11.2f} {best.oos_return_pct:>10.1f}% "
                f"{best.oos_trades:>11}"
            )

    print("=" * 70)
    print()
