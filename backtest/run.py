"""CLI entry point for the backtesting pipeline.

Usage:
    python -m backtest.run                              # BTC/USDT 4h default
    python -m backtest.run --symbol ETH/USDT --timeframe 1h
    python -m backtest.run --optimize                   # Walk-forward optimization
    python -m backtest.run --all-symbols                # All 10 symbols
    python -m backtest.run --start 2024-01-01 --end 2025-12-31
    python -m backtest.run --multi-tf                   # Multi-TF: 4h setup + 1h trigger
    python -m backtest.run --multi-tf --optimize        # Multi-TF walk-forward optimization
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from loguru import logger

from bot.config import Settings
from backtest.data_loader import fetch_historical
from backtest.detector import DetectorParams
from backtest.multi_tf_simulator import MultiTFSimulatorResult, run_multi_tf_simulation
from backtest.optimizer import (
    print_multi_tf_optimization_report,
    print_optimization_report,
    run_multi_tf_optimization,
    run_optimization,
)
from backtest.report import (
    compute_metrics,
    export_equity_csv,
    export_trades_csv,
    generate_html_report,
    print_console_report,
)
from backtest.simulator import run_simulation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest the divergence trading strategy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--symbol", type=str, default="BTC/USDT",
        help="Trading pair (default: BTC/USDT)",
    )
    parser.add_argument(
        "--timeframe", type=str, default="4h",
        help="Candle interval (default: 4h)",
    )
    parser.add_argument(
        "--start", type=str, default="2024-01-01",
        help="Start date YYYY-MM-DD (default: 2024-01-01)",
    )
    parser.add_argument(
        "--end", type=str, default="2025-12-31",
        help="End date YYYY-MM-DD (default: 2025-12-31)",
    )
    parser.add_argument(
        "--optimize", action="store_true",
        help="Run walk-forward optimization instead of single backtest",
    )
    parser.add_argument(
        "--all-symbols", action="store_true",
        help="Backtest all 10 configured symbols",
    )
    parser.add_argument(
        "--exchange", type=str, default="binance",
        help="Exchange for data fetching (default: binance)",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Skip CSV cache and re-fetch from exchange",
    )
    parser.add_argument(
        "--multi-tf", action="store_true",
        help="Use multi-timeframe confirmation (4h setup + 1h trigger)",
    )
    return parser.parse_args()


async def backtest_single(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    settings: Settings,
    exchange_id: str = "bybit",
    optimize: bool = False,
) -> None:
    """Run a single backtest or optimization for one symbol/timeframe."""
    logger.info(f"{'Optimizing' if optimize else 'Backtesting'} {symbol} {timeframe} "
                f"({start:%Y-%m-%d} -> {end:%Y-%m-%d})")

    # Fetch data
    candles = await fetch_historical(
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        exchange_id=exchange_id,
    )

    if len(candles) < 300:
        logger.error(f"Insufficient data for {symbol} {timeframe}: {len(candles)} candles")
        return

    logger.info(f"Loaded {len(candles)} candles for {symbol} {timeframe}")

    if optimize:
        # Walk-forward optimization
        opt_result = run_optimization(
            candles=candles,
            symbol=symbol,
            timeframe=timeframe,
            settings=settings,
            progress_callback=lambda c, t: logger.info(f"  Progress: {c}/{t} simulations"),
        )
        print_optimization_report(opt_result)

        # Run final backtest with best params
        if opt_result.best_params:
            logger.info("Running final backtest with optimized parameters...")
            result = run_simulation(
                candles=candles,
                symbol=symbol,
                timeframe=timeframe,
                settings=settings,
                detector_params=opt_result.best_params,
            )
            _output_results(result)

    else:
        # Single backtest with current settings
        params = DetectorParams(
            min_confidence=settings.min_confidence,
            min_risk_reward=settings.min_risk_reward,
            max_position_pct=settings.max_position_pct,
        )
        result = run_simulation(
            candles=candles,
            symbol=symbol,
            timeframe=timeframe,
            settings=settings,
            detector_params=params,
        )
        _output_results(result)


async def backtest_multi_tf(
    symbol: str,
    start: datetime,
    end: datetime,
    settings: Settings,
    exchange_id: str = "binance",
    optimize: bool = False,
) -> None:
    """Run a multi-TF backtest or optimization (4h setup + 1h trigger)."""
    logger.info(
        f"{'Optimizing' if optimize else 'Backtesting'} {symbol} multi-TF (4h+1h) "
        f"({start:%Y-%m-%d} -> {end:%Y-%m-%d})"
    )

    # Fetch both timeframes
    candles_4h = await fetch_historical(
        symbol=symbol, timeframe="4h",
        start=start, end=end, exchange_id=exchange_id,
    )
    candles_1h = await fetch_historical(
        symbol=symbol, timeframe="1h",
        start=start, end=end, exchange_id=exchange_id,
    )

    if len(candles_4h) < 300:
        logger.error(f"Insufficient 4h data for {symbol}: {len(candles_4h)} candles")
        return
    if len(candles_1h) < 500:
        logger.error(f"Insufficient 1h data for {symbol}: {len(candles_1h)} candles")
        return

    logger.info(
        f"Loaded {len(candles_4h)} 4h candles + {len(candles_1h)} 1h candles for {symbol}"
    )

    if optimize:
        opt_result = run_multi_tf_optimization(
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            symbol=symbol,
            settings=settings,
            progress_callback=lambda c, t: logger.info(f"  Progress: {c}/{t} simulations"),
        )
        print_multi_tf_optimization_report(opt_result)

        # Run final multi-TF backtest with best params
        if opt_result.best_params:
            logger.info("Running final multi-TF backtest with optimized parameters...")
            result = run_multi_tf_simulation(
                candles_4h=candles_4h,
                candles_1h=candles_1h,
                symbol=symbol,
                settings=settings,
                detector_params=opt_result.best_params,
            )
            _output_multi_tf_results(result)

    else:
        params = DetectorParams(
            min_confidence=settings.min_confidence,
            min_risk_reward=settings.min_risk_reward,
            max_position_pct=settings.max_position_pct,
        )
        result = run_multi_tf_simulation(
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            symbol=symbol,
            settings=settings,
            detector_params=params,
        )
        _output_multi_tf_results(result)


def _output_results(result) -> None:
    """Print console report and generate output files."""
    metrics = compute_metrics(result)
    print_console_report(result, metrics)

    # Export files
    trades_path = export_trades_csv(result)
    equity_path = export_equity_csv(result)
    html_path = generate_html_report(result, metrics)

    logger.info(f"Trade log:    {trades_path}")
    logger.info(f"Equity curve: {equity_path}")
    logger.info(f"HTML report:  {html_path}")


def _output_multi_tf_results(result: MultiTFSimulatorResult) -> None:
    """Print console report with multi-TF stats and generate output files."""
    metrics = compute_metrics(result)
    print_console_report(result, metrics)

    # Print multi-TF specific stats
    print(f"\n  Multi-TF Stats:")
    print(f"    4h setups created:   {result.setups_created}")
    print(f"    Setups confirmed:    {result.setups_confirmed}")
    print(f"    Setups expired:      {result.setups_expired}")
    if result.setups_created > 0:
        confirm_rate = result.setups_confirmed / result.setups_created * 100
        print(f"    Confirmation rate:   {confirm_rate:.1f}%")
    print()

    # Export files
    trades_path = export_trades_csv(result)
    equity_path = export_equity_csv(result)
    html_path = generate_html_report(result, metrics)

    logger.info(f"Trade log:    {trades_path}")
    logger.info(f"Equity curve: {equity_path}")
    logger.info(f"HTML report:  {html_path}")


async def main() -> None:
    args = parse_args()

    # Configure logging
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")

    # Load settings (no .env needed for backtesting — defaults are fine)
    settings = Settings(
        trading_mode="dev",
        anthropic_api_key="not-needed-for-backtest",
        database_url="not-needed-for-backtest",
    )

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    if args.multi_tf:
        # Multi-TF mode: 4h setup + 1h trigger
        if args.all_symbols:
            for symbol in settings.symbols:
                await backtest_multi_tf(
                    symbol=symbol,
                    start=start,
                    end=end,
                    settings=settings,
                    exchange_id=args.exchange,
                    optimize=args.optimize,
                )
        else:
            await backtest_multi_tf(
                symbol=args.symbol,
                start=start,
                end=end,
                settings=settings,
                exchange_id=args.exchange,
                optimize=args.optimize,
            )
    elif args.all_symbols:
        # Only run 4h — 1h divergences are pure noise (Phase 1 finding)
        timeframes = [args.timeframe] if args.timeframe != "4h" else ["4h"]
        for symbol in settings.symbols:
            for timeframe in timeframes:
                await backtest_single(
                    symbol=symbol,
                    timeframe=timeframe,
                    start=start,
                    end=end,
                    settings=settings,
                    exchange_id=args.exchange,
                    optimize=args.optimize,
                )
    else:
        await backtest_single(
            symbol=args.symbol,
            timeframe=args.timeframe,
            start=start,
            end=end,
            settings=settings,
            exchange_id=args.exchange,
            optimize=args.optimize,
        )


if __name__ == "__main__":
    asyncio.run(main())
