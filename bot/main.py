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
from bot.layer5_monitoring.sms import SMSClient
from bot.layer5_monitoring.telegram import TelegramClient
from bot.models import AnalysisCycleResult


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

    # --- Monitor open positions (check SL/TP hits) ---
    try:
        closed = await engine.monitor_open_positions()
        if closed > 0:
            logger.info(f"Position monitor: {closed} position(s) closed")
    except Exception as e:
        logger.error(f"Position monitor error: {e}")

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
            portfolio.total_equity + daily_pnl,
            portfolio.available_balance,
            open_count,
            daily_pnl,
            daily_trades,
        )
    except Exception as e:
        logger.error(f"Failed to record portfolio snapshot: {e}")

    for symbol in settings.symbols:
        for timeframe in settings.timeframes:
            try:
                # --- Layer 1: Data Ingestion ---
                candles = await market.fetch_ohlcv(symbol, timeframe)
                if len(candles) < settings.lookback_candles // 2:
                    logger.warning(
                        f"Insufficient candles for {symbol}/{timeframe}: "
                        f"{len(candles)} (need {settings.lookback_candles // 2}+)"
                    )
                    continue

                indicators = compute_indicators(candles, symbol, timeframe, settings)
                payload = build_analysis_payload(indicators, settings)
                result.symbols_analyzed.append(f"{symbol}/{timeframe}")

                # --- Layer 2: Intelligence ---
                signal = await claude.analyze_divergence(payload, symbol, timeframe)

                if not signal.divergence_detected:
                    logger.debug(f"No divergence: {symbol}/{timeframe}")
                    continue

                result.signals_found += 1
                logger.info(
                    f"Signal detected: {symbol}/{timeframe} — "
                    f"{signal.divergence_type} ({signal.confidence:.0%})"
                )

                # --- Layer 2: Validation (deterministic, <1ms) ---
                validation = validate_signal(signal, indicators, settings)
                if not validation.passed:
                    logger.info(f"Signal rejected: {validation.reason}")
                    continue

                result.signals_validated += 1

                # --- Layer 3 + 4: Execution (includes risk checks) ---
                order = await engine.execute_signal(signal, portfolio)
                if order:
                    result.orders_placed += 1

                    # Send Telegram alert for the signal
                    await telegram.send_signal_alert(signal)

            except Exception as e:
                error_msg = f"Error analysing {symbol}/{timeframe}: {e}"
                logger.error(error_msg)
                result.errors.append(error_msg)

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
        await db.pool.execute(
            queries.INSERT_ANALYSIS_CYCLE,
            result.started_at,
            result.completed_at,
            result.symbols_analyzed,
            result.signals_found,
            result.signals_validated,
            result.orders_placed,
            json.dumps({"errors": result.errors}) if result.errors else None,
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
    scheduler.start()
    logger.info("Scheduler started")

    # Run first cycle immediately
    logger.info("Running initial analysis cycle...")
    await analysis_cycle(settings, market, claude, engine, risk, db, telegram)

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
