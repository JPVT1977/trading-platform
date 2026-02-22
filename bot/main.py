"""Divergence Trading Bot — Main Entry Point.

Wires all five layers together:
  Layer 1: Data Ingestion (CCXT + TA-Lib)
  Layer 2: Intelligence (Claude API with tool_use)
  Layer 3: Execution Engine (deterministic, FSM-based)
  Layer 4: Risk Management (hard-coded rules, circuit breakers)
  Layer 5: Monitoring (Loguru, Telegram, health checks)
"""

from __future__ import annotations

import asyncio
import json
import signal as signal_module
import sys
import time
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from bot.config import Settings, TradingMode
from bot.database.connection import Database
from bot.layer1_data.indicators import compute_indicators
from bot.layer1_data.market_data import MarketDataClient
from bot.layer1_data.payload_builder import build_analysis_payload
from bot.layer2_intelligence.claude_client import ClaudeClient
from bot.layer2_intelligence.validator import validate_signal
from bot.layer3_execution.engine import ExecutionEngine
from bot.layer4_risk.manager import RiskManager
from bot.layer5_monitoring.health import HealthServer
from bot.layer5_monitoring.logger import setup_logger
from bot.layer5_monitoring.outcome_tracker import track_signal_outcomes
from bot.layer5_monitoring.sms import SMSClient
from bot.layer5_monitoring.telegram import TelegramClient
from bot.models import AnalysisCycleResult

# Track last candle timestamp per symbol/timeframe to determine forming vs closed
_last_candle_times: dict[str, str] = {}

# Track which candle already produced a signal (prevents duplicate signals per candle)
_signaled_candles: dict[str, str] = {}


async def _seed_candle_cache(market: MarketDataClient, settings: Settings) -> None:
    """Populate _last_candle_times from exchange so first cycle knows candle status.

    Without this, every restart treats the first candle as "closed" (new timestamp)
    which could trigger false confidence boosts or reversal trades.

    Fetches 1 candle per symbol/timeframe directly from the exchange — the same
    source the analysis cycle uses — so timestamps match exactly.
    """
    for symbol in settings.symbols:
        for timeframe in settings.timeframes:
            try:
                candles = await market.fetch_ohlcv(symbol, timeframe, limit=1)
                if candles:
                    key = f"{symbol}/{timeframe}"
                    _last_candle_times[key] = candles[-1].timestamp.isoformat()
                    logger.info(f"Candle cache seeded: {key} = {candles[-1].timestamp.isoformat()}")
            except Exception as e:
                logger.warning(f"Failed to seed candle cache for {symbol}/{timeframe}: {e}")

    logger.info(f"Candle cache seeded with {len(_last_candle_times)} entries")


async def position_monitor(engine: ExecutionEngine) -> None:
    """Standalone position monitor — checks SL/TP every 2 minutes."""
    try:
        closed = await engine.monitor_open_positions()
        if closed > 0:
            logger.info(f"Position monitor: {closed} position(s) closed")
    except Exception as e:
        logger.error(f"Position monitor error: {e}")


async def _persist_signal(
    db: Database,
    signal,
    validated: bool,
    validation_reason: str,
) -> str | None:
    """Save a detected signal to the database immediately. Returns signal ID."""
    try:
        from bot.database import queries as q
        row = await db.pool.fetchrow(
            q.INSERT_SIGNAL,
            signal.symbol,
            signal.timeframe,
            signal.divergence_type.value if signal.divergence_type else "none",
            signal.indicator,
            signal.confidence,
            signal.direction.value if signal.direction else None,
            signal.entry_price,
            signal.stop_loss,
            signal.take_profit_1,
            signal.take_profit_2,
            signal.take_profit_3,
            signal.reasoning,
            json.dumps(signal.model_dump(mode="json")),
            validated,
            validation_reason,
        )
        return str(row["id"]) if row else None
    except Exception as e:
        logger.error(f"Failed to persist signal: {e}")
        return None


async def analysis_cycle(
    settings: Settings,
    market: MarketDataClient,
    claude: ClaudeClient,
    engine: ExecutionEngine,
    risk: RiskManager,
    db: Database,
    telegram: TelegramClient,
) -> AnalysisCycleResult:
    """Single analysis cycle: fetch → compute → analyse → validate → execute."""

    result = AnalysisCycleResult(
        started_at=datetime.now(timezone.utc),
        symbols_analyzed=[],
    )
    cycle_start = time.monotonic()

    # Get current portfolio state for risk checks
    portfolio = await risk.get_portfolio_state()

    # --- Record portfolio snapshot for equity curve ---
    try:
        from bot.database import queries as q
        open_count = await db.pool.fetchval(q.COUNT_OPEN_ORDERS)
        pnl_row = await db.pool.fetchrow(q.SELECT_DAILY_PNL)
        daily_pnl = float(pnl_row["daily_pnl"]) if pnl_row else 0.0
        daily_trades = int(pnl_row["daily_trades"]) if pnl_row else 0
        await db.pool.execute(
            q.INSERT_PORTFOLIO_SNAPSHOT,
            portfolio.total_equity,
            portfolio.available_balance,
            open_count,
            daily_pnl,
            daily_trades,
        )
    except Exception as e:
        logger.error(f"Failed to record portfolio snapshot: {e}")

    # Track symbols traded this cycle to prevent duplicates
    traded_symbols: set[str] = set()

    for symbol in settings.symbols:
        for timeframe in settings.timeframes:
            candle_key = f"{symbol}/{timeframe}"
            try:
                # --- Layer 1: Data Ingestion ---
                candles = await market.fetch_ohlcv(symbol, timeframe)
                if len(candles) < settings.lookback_candles // 2:
                    logger.warning(
                        f"Insufficient candles for {symbol}/{timeframe}: "
                        f"{len(candles)} (need {settings.lookback_candles // 2}+)"
                    )
                    result.symbols_analyzed.append(candle_key)
                    result.symbol_details[candle_key] = f"insufficient_data ({len(candles)} candles)"
                    continue

                indicators = compute_indicators(candles, symbol, timeframe, settings)
                result.symbols_analyzed.append(candle_key)

                # Determine candle status: "closed" (new candle appeared) or "forming" (mid-candle)
                latest_ts = candles[-1].timestamp.isoformat()
                prev_ts = _last_candle_times.get(candle_key)
                if prev_ts != latest_ts:
                    # New candle timestamp — previous candle just closed
                    candle_status = "closed"
                    _last_candle_times[candle_key] = latest_ts
                    # Clear signal dedup for this pair (new candle = fresh slate)
                    _signaled_candles.pop(candle_key, None)
                    logger.info(f"New candle detected: {candle_key} ({latest_ts})")
                else:
                    candle_status = "forming"

                # Signal-level dedup: skip if we already found a divergence on this candle
                if _signaled_candles.get(candle_key) == latest_ts:
                    logger.debug(f"Skipping {candle_key}: already signaled on this candle")
                    result.symbol_details[candle_key] = "already_signaled"
                    continue

                payload = build_analysis_payload(indicators, settings, candle_status=candle_status)

                # --- Layer 2: Intelligence ---
                signal = await claude.analyze_divergence(payload, symbol, timeframe)

                if not signal.divergence_detected:
                    logger.debug(f"No divergence: {symbol}/{timeframe} ({candle_status})")
                    result.symbol_details[candle_key] = f"no_divergence ({candle_status})"
                    continue

                result.signals_found += 1
                # Mark this candle as signaled so we don't re-analyze next minute
                _signaled_candles[candle_key] = latest_ts
                logger.info(
                    f"Signal detected: {symbol}/{timeframe} — "
                    f"{signal.divergence_type} ({signal.confidence:.0%}) [{candle_status}]"
                )

                # --- Layer 2: Validation (deterministic, <1ms) ---
                validation = validate_signal(signal, indicators, settings)

                # Persist EVERY detected signal immediately (validated or not)
                signal_id = await _persist_signal(
                    db, signal, validation.passed, validation.reason or "All validation rules passed",
                )

                if not validation.passed:
                    logger.info(f"Signal rejected: {validation.reason}")
                    result.symbol_details[candle_key] = f"signal_rejected ({validation.reason})"
                    continue

                result.signals_validated += 1

                # Skip if we already traded this symbol this cycle
                if symbol in traded_symbols:
                    logger.info(f"Skipping {symbol}/{timeframe}: already traded {symbol} this cycle")
                    result.symbol_details[candle_key] = "signal_validated (already traded this cycle)"
                    continue

                # --- Layer 3 + 4: Execution (includes risk checks) ---
                order = await engine.execute_signal(signal, portfolio, signal_id=signal_id)
                if order:
                    result.orders_placed += 1

                    # Track this symbol as traded + update in-memory portfolio
                    traded_symbols.add(symbol)
                    portfolio.open_positions.append(order)

                    # Send Telegram alert for the signal
                    await telegram.send_signal_alert(signal)
                    result.symbol_details[candle_key] = "order_placed"
                else:
                    result.symbol_details[candle_key] = "signal_validated (execution declined)"

            except Exception as e:
                error_msg = f"Error analysing {symbol}/{timeframe}: {e}"
                logger.error(error_msg)
                result.errors.append(error_msg)
                result.symbol_details[candle_key] = f"error ({e})"

    # Finalise cycle result
    result.completed_at = datetime.now(timezone.utc)
    result.duration_ms = int((time.monotonic() - cycle_start) * 1000)

    logger.info(
        f"Cycle complete: {len(result.symbols_analyzed)} analysed, "
        f"{result.signals_found} signals, "
        f"{result.signals_validated} validated, "
        f"{result.orders_placed} orders, "
        f"{result.duration_ms}ms"
    )

    # Persist cycle result
    try:
        from bot.database import queries
        cycle_details = {}
        if result.errors:
            cycle_details["errors"] = result.errors
        if result.symbol_details:
            cycle_details["symbols"] = result.symbol_details
        await db.pool.execute(
            queries.INSERT_ANALYSIS_CYCLE,
            result.started_at,
            result.completed_at,
            result.symbols_analyzed,
            result.signals_found,
            result.signals_validated,
            result.orders_placed,
            json.dumps(cycle_details) if cycle_details else None,
            result.duration_ms,
        )
    except Exception as e:
        logger.error(f"Failed to persist analysis cycle: {e}")

    return result


async def main() -> None:
    """Application entry point."""

    # Load configuration
    settings = Settings()

    # Validate configuration
    errors = settings.validate_for_startup()
    if errors:
        for err in errors:
            print(f"CONFIG ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    # Initialise logging
    setup_logger(settings)
    logger.info(f"Starting Divergence Trading Bot (mode={settings.trading_mode.value})")
    logger.info(f"Symbols: {settings.symbols}")
    logger.info(f"Timeframes: {settings.timeframes}")
    logger.info(f"Analysis interval: {settings.analysis_interval_minutes} minutes")

    # Initialise components
    db = Database(settings)
    await db.connect()

    market = MarketDataClient(settings)
    claude = ClaudeClient(settings)
    risk = RiskManager(settings, db)
    telegram = TelegramClient(settings)
    sms = SMSClient(settings)
    engine = ExecutionEngine(settings, db, market, risk, telegram, sms=sms)
    health = HealthServer(settings, db, market, risk_manager=risk)

    # Start health check server (Fly.io needs this)
    await health.start()

    # Send startup notifications
    startup_msg = (
        f"Bot Started | Mode: {settings.trading_mode.value} | "
        f"Symbols: {', '.join(settings.symbols)} | "
        f"Interval: {settings.analysis_interval_minutes}min"
    )
    await telegram.send(
        f"<b>Bot Started</b>\n"
        f"Mode: {settings.trading_mode.value}\n"
        f"Symbols: {', '.join(settings.symbols)}\n"
        f"Interval: {settings.analysis_interval_minutes}min"
    )
    await sms.send(startup_msg)

    # Set up scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        analysis_cycle,
        "interval",
        minutes=settings.analysis_interval_minutes,
        args=[settings, market, claude, engine, risk, db, telegram],
        id="analysis_cycle",
        name="Divergence Analysis",
        max_instances=1,
        misfire_grace_time=60,
    )
    # Monitor open positions every 2 minutes (SL/TP checks — faster than analysis)
    scheduler.add_job(
        position_monitor,
        "interval",
        minutes=2,
        args=[engine],
        id="position_monitor",
        name="Position Monitor (SL/TP)",
        max_instances=1,
        misfire_grace_time=30,
    )
    # Track signal outcomes every 5 minutes (fills checkpoint prices, TP/SL hits, verdicts)
    scheduler.add_job(
        track_signal_outcomes,
        "interval",
        minutes=5,
        args=[db, market],
        id="outcome_tracker",
        name="Signal Outcome Tracker",
        max_instances=1,
        misfire_grace_time=60,
    )
    scheduler.start()
    logger.info(f"Scheduler started (analysis: every {settings.analysis_interval_minutes}min, SL/TP monitor: every 2min, outcomes: every 5min)")

    # Seed candle dedup cache from exchange so deploy doesn't re-trigger existing positions
    await _seed_candle_cache(market, settings)

    # Run first cycle immediately
    logger.info("Running initial analysis cycle...")
    await analysis_cycle(settings, market, claude, engine, risk, db, telegram)

    # Run outcome tracker once on startup to catch up
    logger.info("Running initial outcome tracker...")
    await track_signal_outcomes(db, market)

    # Graceful shutdown handling
    stop_event = asyncio.Event()

    def shutdown_handler(sig: int, frame: object) -> None:
        sig_name = signal_module.Signals(sig).name
        logger.info(f"Received {sig_name}, initiating shutdown...")
        stop_event.set()

    signal_module.signal(signal_module.SIGTERM, shutdown_handler)
    signal_module.signal(signal_module.SIGINT, shutdown_handler)

    # Wait for shutdown signal
    await stop_event.wait()

    # Cleanup
    logger.info("Shutting down...")
    scheduler.shutdown(wait=False)
    await health.stop()
    await market.close()
    await telegram.send("<b>Bot Stopped</b>\nGraceful shutdown complete.")
    await sms.send("Bot stopped. Graceful shutdown complete.")
    await telegram.close()
    await sms.close()
    await db.disconnect()
    logger.info("Bot stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())
