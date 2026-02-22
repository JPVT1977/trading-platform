"""Divergence Trading Bot — Main Entry Point.

Wires all five layers together:
  Layer 1: Data Ingestion (CCXT/OANDA + TA-Lib)
  Layer 2: Intelligence (Claude API with tool_use)
  Layer 3: Execution Engine (deterministic, FSM-based)
  Layer 4: Risk Management (hard-coded rules, circuit breakers)
  Layer 5: Monitoring (Loguru, Telegram, health checks)

Phase 2: Multi-TF Confirmation (4h setup + 1h trigger)
  When use_multi_tf_confirmation is enabled, 4h signals are stored as "setups"
  and only become trades when a 1h divergence in the same direction confirms
  within setup_expiry_hours. This filters out weak signals.

Multi-broker: BrokerRouter dispatches API calls to the correct broker
  (Binance for crypto, OANDA for forex). If OANDA is not configured, the
  bot behaves exactly as it does today.
"""

from __future__ import annotations

import asyncio
import json
import signal as signal_module
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from bot.config import Settings, TradingMode
from bot.database.connection import Database
from bot.instruments import route_symbol
from bot.layer1_data.broker_router import BrokerRouter
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
from bot.models import AnalysisCycleResult, DivergenceSignal, SignalDirection

# Track last candle timestamp per symbol/timeframe to determine forming vs closed
_last_candle_times: dict[str, str] = {}

# Track which candle already produced a signal (prevents duplicate signals per candle)
_signaled_candles: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Phase 2: Multi-TF Setup Tracking
# ---------------------------------------------------------------------------


@dataclass
class ActiveSetup:
    """A 4h divergence signal waiting for 1h confirmation."""

    signal: DivergenceSignal
    detected_at: datetime
    expires_at: datetime
    direction: SignalDirection
    signal_id: str | None = None  # DB signal ID for traceability


# Active setups keyed by "broker:symbol" to avoid cross-broker collisions
# e.g. "binance:BTC/USDT" or "oanda:EUR_USD"
_active_setups: dict[str, list[ActiveSetup]] = {}


def _setup_key(symbol: str) -> str:
    """Create a broker-namespaced key for setup tracking."""
    broker = route_symbol(symbol).value
    return f"{broker}:{symbol}"


def _expire_setups(now: datetime) -> int:
    """Remove expired setups from all symbols. Returns count expired."""
    expired = 0
    for key in list(_active_setups.keys()):
        before = len(_active_setups[key])
        _active_setups[key] = [
            s for s in _active_setups[key] if now < s.expires_at
        ]
        expired += before - len(_active_setups[key])
        if not _active_setups[key]:
            del _active_setups[key]
    return expired


def _find_matching_setup(symbol: str, direction: SignalDirection) -> ActiveSetup | None:
    """Find an active 4h setup for the given symbol and direction."""
    key = _setup_key(symbol)
    setups = _active_setups.get(key, [])
    for setup in setups:
        if setup.direction == direction:
            return setup
    return None


def _build_confirmed_signal(
    setup: ActiveSetup,
    signal_1h: DivergenceSignal,
    settings: Settings,
) -> DivergenceSignal | None:
    """Build a confirmed signal using 1h entry + 4h structural stop loss.

    Returns None if the levels are invalid (e.g. SL on wrong side of entry).
    """
    entry = signal_1h.entry_price
    stop_loss = setup.signal.stop_loss

    if entry is None or stop_loss is None:
        return None

    direction = setup.direction

    # If 4h SL is on wrong side of 1h entry (price moved a lot), fall back to 1h SL
    if direction == SignalDirection.LONG:
        if stop_loss >= entry:
            stop_loss = signal_1h.stop_loss
        if stop_loss is None or stop_loss >= entry:
            return None
        risk = entry - stop_loss
        tp1 = entry + (risk * settings.min_risk_reward)
        tp2 = entry + (risk * settings.min_risk_reward * 1.5)
        tp3 = entry + (risk * settings.min_risk_reward * 2.0)
    else:
        if stop_loss <= entry:
            stop_loss = signal_1h.stop_loss
        if stop_loss is None or stop_loss <= entry:
            return None
        risk = stop_loss - entry
        tp1 = entry - (risk * settings.min_risk_reward)
        tp2 = entry - (risk * settings.min_risk_reward * 1.5)
        tp3 = entry - (risk * settings.min_risk_reward * 2.0)

    return DivergenceSignal(
        divergence_detected=True,
        divergence_type=setup.signal.divergence_type,
        indicator=f"4h:{setup.signal.indicator}, 1h:{signal_1h.indicator}",
        confidence=max(setup.signal.confidence, signal_1h.confidence),
        direction=direction,
        entry_price=entry,
        stop_loss=stop_loss,
        take_profit_1=tp1,
        take_profit_2=tp2,
        take_profit_3=tp3,
        reasoning=(
            f"Multi-TF confirmed: 4h {setup.signal.divergence_type} "
            f"({setup.signal.indicator}) + 1h {signal_1h.divergence_type} "
            f"({signal_1h.indicator}). "
            f"Entry={entry:.2f} (1h), SL={stop_loss:.2f} (4h structural)"
        ),
        symbol=signal_1h.symbol,
        timeframe="4h+1h",
    )


# ---------------------------------------------------------------------------
# Candle cache seeding
# ---------------------------------------------------------------------------


async def _seed_candle_cache(router: BrokerRouter, all_symbols: list[str], settings: Settings) -> None:
    """Populate _last_candle_times from exchanges so first cycle knows candle status.

    Without this, every restart treats the first candle as "closed" (new timestamp)
    which could trigger false confidence boosts or reversal trades.

    Fetches 1 candle per symbol/timeframe directly from the exchange — the same
    source the analysis cycle uses — so timestamps match exactly.
    """
    for symbol in all_symbols:
        for timeframe in settings.timeframes:
            try:
                broker = router.get_broker(symbol)
                candles = await broker.fetch_ohlcv(symbol, timeframe, limit=1)
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
    broker_id: str = "binance",
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
            broker_id,
        )
        return str(row["id"]) if row else None
    except Exception as e:
        logger.error(f"Failed to persist signal: {e}")
        return None


async def analysis_cycle(
    settings: Settings,
    router: BrokerRouter,
    claude: ClaudeClient,
    engine: ExecutionEngine,
    risk: RiskManager,
    db: Database,
    telegram: TelegramClient,
    all_symbols: list[str],
) -> AnalysisCycleResult:
    """Single analysis cycle: fetch -> compute -> analyse -> validate -> execute.

    When use_multi_tf_confirmation is enabled:
    - 4h signals are stored as "active setups" (not executed immediately)
    - 1h signals only execute if they confirm a matching 4h setup
    - Setups expire after setup_expiry_hours (default 24h)
    """

    result = AnalysisCycleResult(
        started_at=datetime.now(timezone.utc),
        symbols_analyzed=[],
    )
    cycle_start = time.monotonic()
    now = datetime.now(timezone.utc)

    # Phase 2: Expire old setups at the start of each cycle
    if settings.use_multi_tf_confirmation:
        expired = _expire_setups(now)
        if expired:
            logger.info(f"Multi-TF: expired {expired} setup(s)")
        active_count = sum(len(v) for v in _active_setups.values())
        if active_count:
            logger.info(f"Multi-TF: {active_count} active setup(s) across {len(_active_setups)} symbol(s)")

    # Get per-broker portfolio states and record snapshots
    portfolio_cache: dict[str, object] = {}
    for broker in router.all_brokers:
        bid = broker.broker_id
        portfolio = await risk.get_portfolio_state(broker_id=bid)
        portfolio_cache[bid] = portfolio

        # Record portfolio snapshot for equity curve
        try:
            from bot.database import queries as q
            open_count = await db.pool.fetchval(q.COUNT_OPEN_ORDERS_BY_BROKER, bid)
            pnl_row = await db.pool.fetchrow(q.SELECT_DAILY_PNL_BY_BROKER, bid)
            daily_pnl = float(pnl_row["daily_pnl"]) if pnl_row else 0.0
            daily_trades = int(pnl_row["daily_trades"]) if pnl_row else 0
            await db.pool.execute(
                q.INSERT_PORTFOLIO_SNAPSHOT,
                portfolio.total_equity,
                portfolio.available_balance,
                open_count,
                daily_pnl,
                daily_trades,
                bid,
            )
        except Exception as e:
            logger.error(f"Failed to record portfolio snapshot for {bid}: {e}")

    # Track symbols traded this cycle to prevent duplicates
    traded_symbols: set[str] = set()

    for symbol in all_symbols:
        broker = router.get_broker(symbol)
        broker_id = broker.broker_id
        portfolio = portfolio_cache[broker_id]

        # Per-broker confidence threshold
        min_confidence = settings.get_min_confidence(broker_id)

        for timeframe in settings.timeframes:
            candle_key = f"{symbol}/{timeframe}"
            try:
                # --- Layer 1: Data Ingestion ---
                candles = await broker.fetch_ohlcv(symbol, timeframe)
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

                # Per-broker confidence check
                if validation.passed and signal.confidence < min_confidence:
                    validation = type(validation)(
                        passed=False,
                        reason=f"Confidence {signal.confidence:.2f} below {broker_id} threshold {min_confidence}",
                    )

                # Persist EVERY detected signal immediately (validated or not)
                signal_id = await _persist_signal(
                    db, signal, validation.passed,
                    validation.reason or "All validation rules passed",
                    broker_id=broker_id,
                )

                if not validation.passed:
                    logger.info(f"Signal rejected: {validation.reason}")
                    result.symbol_details[candle_key] = f"signal_rejected ({validation.reason})"
                    continue

                result.signals_validated += 1

                # ==========================================================
                # Phase 2: Multi-TF Confirmation Logic
                # ==========================================================
                if settings.use_multi_tf_confirmation:
                    if timeframe == "4h":
                        # 4h signal → store as active setup, do NOT execute
                        setup = ActiveSetup(
                            signal=signal,
                            detected_at=now,
                            expires_at=now + timedelta(hours=settings.setup_expiry_hours),
                            direction=signal.direction,
                            signal_id=signal_id,
                        )
                        setup_key = _setup_key(symbol)
                        _active_setups.setdefault(setup_key, []).append(setup)
                        logger.info(
                            f"Multi-TF: 4h setup CREATED for {symbol} "
                            f"({signal.direction.value} {signal.divergence_type}) — "
                            f"expires in {settings.setup_expiry_hours}h"
                        )
                        await telegram.send(
                            f"<b>4h Setup Created</b>\n"
                            f"Symbol: {symbol}\n"
                            f"Direction: {signal.direction.value}\n"
                            f"Type: {signal.divergence_type}\n"
                            f"Confidence: {signal.confidence:.0%}\n"
                            f"Awaiting 1h confirmation (expires {settings.setup_expiry_hours}h)"
                        )
                        result.symbol_details[candle_key] = "multi_tf_setup_created"
                        continue  # Don't execute — wait for 1h confirmation

                    elif timeframe == "1h":
                        # 1h signal → check for matching 4h setup
                        if signal.direction is None:
                            result.symbol_details[candle_key] = "signal_validated (no direction)"
                            continue

                        matching_setup = _find_matching_setup(symbol, signal.direction)
                        if matching_setup is None:
                            logger.info(
                                f"Multi-TF: 1h signal for {symbol} ({signal.direction.value}) "
                                f"— no matching 4h setup, skipping"
                            )
                            result.symbol_details[candle_key] = "multi_tf_no_matching_setup"
                            continue

                        # Build confirmed signal with 1h entry + 4h SL
                        confirmed = _build_confirmed_signal(matching_setup, signal, settings)
                        if confirmed is None:
                            logger.warning(
                                f"Multi-TF: Invalid levels for {symbol} confirmed signal, skipping"
                            )
                            result.symbol_details[candle_key] = "multi_tf_invalid_levels"
                            continue

                        # Remove the consumed setup
                        setup_key = _setup_key(symbol)
                        _active_setups[setup_key].remove(matching_setup)
                        if not _active_setups[setup_key]:
                            del _active_setups[setup_key]

                        # Persist the confirmed signal
                        confirmed_signal_id = await _persist_signal(
                            db, confirmed, True,
                            "Multi-TF confirmed (4h setup + 1h trigger)",
                            broker_id=broker_id,
                        )

                        logger.info(
                            f"Multi-TF: CONFIRMED {symbol} {confirmed.direction.value} — "
                            f"1h entry={confirmed.entry_price:.2f}, "
                            f"4h SL={confirmed.stop_loss:.2f}"
                        )

                        # Use the confirmed signal for execution
                        signal = confirmed
                        signal_id = confirmed_signal_id

                        # Fall through to execution below

                # ==========================================================
                # Execution (shared path for both modes)
                # ==========================================================

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
    if settings.use_multi_tf_confirmation:
        logger.info(
            f"Multi-TF confirmation ENABLED (4h setup + 1h trigger, "
            f"expiry={settings.setup_expiry_hours}h)"
        )

    # Build broker router
    router = BrokerRouter()

    # Initialise components
    db = Database(settings)
    await db.connect()

    market = MarketDataClient(settings)
    router.register(market)

    # Register OANDA if configured
    if settings.oanda_enabled:
        from bot.layer1_data.oanda_client import OandaClient
        oanda = OandaClient(settings)
        router.register(oanda)
        logger.info(f"OANDA enabled: {settings.oanda_symbols}")
    else:
        logger.info("OANDA not configured — crypto-only mode")

    # Register IG Markets if configured
    if settings.ig_enabled:
        from bot.layer1_data.ig_client import IGClient
        ig = IGClient(settings)
        router.register(ig)
        logger.info(f"IG Markets enabled: {settings.ig_symbols}")
    else:
        logger.info("IG Markets not configured — skipping")

    # Combined symbol list
    all_symbols = list(settings.symbols) + list(settings.oanda_symbols) + list(settings.ig_symbols)

    claude = ClaudeClient(settings)
    risk = RiskManager(settings, db)
    telegram = TelegramClient(settings)
    sms = SMSClient(settings)
    engine = ExecutionEngine(settings, db, router, risk, telegram, sms=sms)
    health = HealthServer(settings, db, router=router, risk_manager=risk)

    # Start health check server (Fly.io needs this)
    await health.start()

    # Send startup notifications
    multi_tf_status = (
        f"Multi-TF: ON (4h+1h, {settings.setup_expiry_hours}h expiry)"
        if settings.use_multi_tf_confirmation
        else "Multi-TF: OFF"
    )
    brokers_status = "Binance"
    if settings.oanda_enabled:
        brokers_status += f" + OANDA ({len(settings.oanda_symbols)} forex pairs)"
    if settings.ig_enabled:
        brokers_status += f" + IG ({len(settings.ig_symbols)} instruments)"
    startup_msg = (
        f"Bot Started | Mode: {settings.trading_mode.value} | "
        f"Brokers: {brokers_status} | "
        f"Symbols: {len(all_symbols)} | "
        f"Interval: {settings.analysis_interval_minutes}min | "
        f"{multi_tf_status}"
    )
    await telegram.send(
        f"<b>Bot Started</b>\n"
        f"Mode: {settings.trading_mode.value}\n"
        f"Brokers: {brokers_status}\n"
        f"Symbols: {len(all_symbols)} ({', '.join(all_symbols[:5])}{'...' if len(all_symbols) > 5 else ''})\n"
        f"Interval: {settings.analysis_interval_minutes}min\n"
        f"{multi_tf_status}"
    )
    await sms.send(startup_msg)

    # Set up scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        analysis_cycle,
        "interval",
        minutes=settings.analysis_interval_minutes,
        args=[settings, router, claude, engine, risk, db, telegram, all_symbols],
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
        args=[db, router],
        id="outcome_tracker",
        name="Signal Outcome Tracker",
        max_instances=1,
        misfire_grace_time=60,
    )
    scheduler.start()
    logger.info(f"Scheduler started (analysis: every {settings.analysis_interval_minutes}min, SL/TP monitor: every 2min, outcomes: every 5min)")

    # Seed candle dedup cache from exchanges so deploy doesn't re-trigger existing positions
    await _seed_candle_cache(router, all_symbols, settings)

    # Run first cycle immediately
    logger.info("Running initial analysis cycle...")
    await analysis_cycle(settings, router, claude, engine, risk, db, telegram, all_symbols)

    # Run outcome tracker once on startup to catch up
    logger.info("Running initial outcome tracker...")
    await track_signal_outcomes(db, router)

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
    await router.close_all()
    await telegram.send("<b>Bot Stopped</b>\nGraceful shutdown complete.")
    await sms.send("Bot stopped. Graceful shutdown complete.")
    await telegram.close()
    await sms.close()
    await db.disconnect()
    logger.info("Bot stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())
