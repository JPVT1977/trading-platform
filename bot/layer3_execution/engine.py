from __future__ import annotations

import json
import time as time_mod
from typing import TYPE_CHECKING

from loguru import logger

from bot.config import Settings, TradingMode
from bot.database import queries
from bot.database.connection import Database
from bot.instruments import get_instrument, route_symbol
from bot.layer3_execution.order_state import OrderStateMachine
from bot.models import (
    DivergenceSignal,
    OrderState,
    PortfolioState,
    TradeOrder,
)

if TYPE_CHECKING:
    from bot.layer1_data.broker_router import BrokerRouter
    from bot.layer4_risk.manager import RiskManager
    from bot.layer5_monitoring.sms import SMSClient
    from bot.layer5_monitoring.telegram import TelegramClient


class ExecutionEngine:
    """Deterministic trade execution engine.

    Claude recommends. This engine decides and executes based on hard-coded rules.
    No AI in the execution loop.
    """

    def __init__(
        self,
        settings: Settings,
        db: Database,
        router: BrokerRouter,
        risk_manager: RiskManager,
        telegram: TelegramClient,
        sms: SMSClient | None = None,
    ) -> None:
        self._settings = settings
        self._db = db
        self._router = router
        self._risk = risk_manager
        self._telegram = telegram
        self._sms = sms

    async def execute_signal(
        self, signal: DivergenceSignal, portfolio: PortfolioState,
        signal_id: str | None = None,
    ) -> TradeOrder | None:
        """Full signal-to-order pipeline.

        1. Risk management check
        2. Position sizing
        3. Order creation
        4. Exchange submission (if paper/live)
        5. Database persistence
        6. Alert notification
        """

        broker = self._router.get_broker(signal.symbol)
        broker_id = broker.broker_id

        # Step 1: Risk check (per-broker limits)
        risk_result = self._risk.check_entry(signal, portfolio, broker_id=broker_id)
        if not risk_result.approved:
            logger.warning(f"Risk rejected {signal.symbol}: {risk_result.reason}")
            return None

        # Step 1b: Handle reversal — close existing position first
        if risk_result.reason.startswith("REVERSAL:"):
            old_order_id = risk_result.reason.split(":", 1)[1]
            await self._close_position_for_reversal(old_order_id, signal.symbol)
            # Remove old position from in-memory portfolio
            portfolio.open_positions = [
                p for p in portfolio.open_positions if str(p.id) != old_order_id
            ]

        # Step 2: Position sizing
        quantity = self._risk.calculate_position_size(signal, portfolio)
        if quantity <= 0:
            logger.warning(f"Position size is zero for {signal.symbol}")
            return None

        # Step 3: Create order model
        order = TradeOrder(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit_1=signal.take_profit_1,
            take_profit_2=signal.take_profit_2,
            take_profit_3=signal.take_profit_3,
            quantity=quantity,
        )

        # Step 4: Submit to exchange
        fsm = OrderStateMachine(order.state)
        side = "buy" if signal.direction.value == "long" else "sell"

        try:
            if self._settings.trading_mode == TradingMode.LIVE:
                # Place entry order via broker router
                exchange_result = await broker.create_limit_order(
                    symbol=order.symbol,
                    side=side,
                    amount=order.quantity,
                    price=order.entry_price,
                )
                order.exchange_order_id = str(exchange_result.get("id", ""))

                # Place stop loss
                sl_side = "sell" if side == "buy" else "buy"
                await broker.create_stop_order(
                    symbol=order.symbol,
                    side=sl_side,
                    amount=order.quantity,
                    stop_price=order.stop_loss,
                )

            elif self._settings.trading_mode == TradingMode.PAPER:
                order.exchange_order_id = (
                    f"paper-{broker_id}-{signal.symbol}"
                    f"-{signal.timeframe}-{int(time_mod.time())}"
                )
                logger.info(
                    f"PAPER TRADE: {side} {order.quantity:.6f} {order.symbol} "
                    f"@ {order.entry_price} | SL: {order.stop_loss} | TP1: {order.take_profit_1}"
                )
            else:
                # DEV mode — just log
                logger.info(f"DEV MODE: Would place {side} order for {order.symbol}")
                return order

            fsm.transition(OrderState.SUBMITTED)
            order.state = fsm.state

        except Exception as e:
            fsm.transition(OrderState.ERROR)
            order.state = fsm.state
            logger.error(f"Order submission failed for {order.symbol}: {e}")
            await self._telegram.send_error_alert(str(e), f"Order failed: {order.symbol}")
            # Persist the failed order for auditing, then return None
            try:
                order.signal_id = signal_id
                await self._persist_order(order, broker_id)
            except Exception as persist_err:
                logger.error(f"Failed to persist error order: {persist_err}")
            return None

        # Step 5: Persist order to database (signal already persisted by caller)
        try:
            order.signal_id = signal_id
            order_id = await self._persist_order(order, broker_id)
            order.id = order_id
        except Exception as e:
            logger.error(f"Failed to persist order to database: {e}")

        # Step 6: Alert (Telegram + SMS)
        await self._telegram.send_order_alert(order)
        if self._sms:
            await self._sms.send_order_alert(order)

        logger.info(
            f"Order executed: {order.symbol} {order.direction.value} "
            f"qty={order.quantity:.6f} entry={order.entry_price} "
            f"state={order.state.value} broker={broker_id}"
        )

        return order

    async def monitor_open_positions(self) -> int:
        """Check all open positions against current market price.

        For each open position:
        - Fetch current price from the correct broker
        - If price hit stop loss → close with loss
        - If price hit TP1 → close with profit
        - Also simulate fill: mark submitted orders as filled at entry price

        Returns the number of positions closed this cycle.
        """
        pool = self._db.pool
        rows = await pool.fetch(queries.SELECT_OPEN_ORDERS)

        if not rows:
            return 0

        closed_count = 0

        # Group by symbol to minimise ticker fetches
        symbols = set(r["symbol"] for r in rows)
        tickers: dict[str, float] = {}
        for symbol in symbols:
            try:
                broker = self._router.get_broker(symbol)
                ticker = await broker.fetch_ticker(symbol)
                tickers[symbol] = float(ticker["last"])
            except Exception as e:
                logger.warning(f"Failed to fetch ticker for {symbol}: {e}")

        for row in rows:
            order_id = row["id"]
            symbol = row["symbol"]
            direction = row["direction"]
            state = row["state"]
            entry_price = float(row["entry_price"])
            stop_loss = float(row["stop_loss"])
            take_profit_1 = float(row["take_profit_1"])
            quantity = float(row["quantity"])

            current_price = tickers.get(symbol)
            if current_price is None:
                continue

            # Step 1: Simulate fill for submitted orders
            if state == "submitted":
                await pool.execute(
                    queries.UPDATE_ORDER_FILL,
                    order_id, "filled", quantity, entry_price,
                )
                logger.info(
                    f"PAPER FILL: {symbol} {direction} {quantity:.6f} @ {entry_price}"
                )
                state = "filled"

            # Step 1b: Breakeven / profit-lock trailing stop
            original_sl = row["original_stop_loss"]
            sl_trail_stage = row["sl_trail_stage"] or 0

            if (
                state == "filled"
                and original_sl is not None
                and sl_trail_stage < 2
            ):
                total_range = abs(take_profit_1 - entry_price)
                if total_range > 0:
                    if direction == "long":
                        progress = (current_price - entry_price) / total_range
                    else:
                        progress = (entry_price - current_price) / total_range

                    if progress >= 0.75 and sl_trail_stage < 2:
                        if direction == "long":
                            new_sl = entry_price + 0.25 * total_range
                        else:
                            new_sl = entry_price - 0.25 * total_range
                        await pool.execute(
                            queries.UPDATE_ORDER_STOP_LOSS,
                            order_id, new_sl, 2,
                        )
                        stop_loss = new_sl
                        logger.info(
                            f"PROFIT LOCK: {symbol} SL moved to {new_sl:.5f}"
                        )
                    elif progress >= 0.50 and sl_trail_stage < 1:
                        new_sl = entry_price
                        await pool.execute(
                            queries.UPDATE_ORDER_STOP_LOSS,
                            order_id, new_sl, 1,
                        )
                        stop_loss = new_sl
                        logger.info(
                            f"BREAKEVEN: {symbol} SL moved to {new_sl:.5f}"
                        )

            # Step 2: Check SL/TP against current price
            hit_sl = False
            hit_tp = False

            if direction == "long":
                hit_sl = current_price <= stop_loss
                hit_tp = current_price >= take_profit_1
            elif direction == "short":
                hit_sl = current_price >= stop_loss
                hit_tp = current_price <= take_profit_1

            if not hit_sl and not hit_tp:
                continue

            # Use actual market price as exit (more realistic than exact SL/TP level)
            exit_price = current_price

            # Calculate P&L
            if direction == "long":
                pnl = (exit_price - entry_price) * quantity
            else:
                pnl = (entry_price - exit_price) * quantity

            # Spread-based instruments (OANDA, IG) = 0 fees; crypto = fee_rate round trip
            inst = get_instrument(symbol)
            if inst.fee_rate == 0.0:
                fees = 0.0
            else:
                fees = (entry_price * quantity + exit_price * quantity) * inst.fee_rate

            pnl_net = pnl - fees
            reason = "STOP LOSS" if hit_sl else "TAKE PROFIT"

            # Close the order (now stores exit price)
            await pool.execute(
                queries.UPDATE_ORDER_CLOSE,
                order_id, pnl_net, fees, exit_price,
            )

            closed_count += 1
            pnl_emoji = "+" if pnl_net >= 0 else ""
            logger.info(
                f"PAPER CLOSE: {symbol} {direction} | {reason} @ {exit_price:.2f} | "
                f"P&L: {pnl_emoji}{pnl_net:.2f} (fees: {fees:.2f}) | "
                f"Price now: {current_price:.2f}"
            )

            # Send alert (Telegram + SMS)
            closed_order = TradeOrder(
                id=str(order_id),
                symbol=symbol,
                direction=direction,
                state=OrderState.CLOSED,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit_1=take_profit_1,
                quantity=quantity,
                filled_price=exit_price,
                pnl=pnl_net,
                fees=fees,
            )
            await self._telegram.send_order_alert(closed_order)
            if self._sms:
                await self._sms.send_order_alert(closed_order)

        return closed_count

    async def _close_position_for_reversal(self, order_id: str, symbol: str) -> None:
        """Close an existing position to allow a reversal trade."""
        pool = self._db.pool
        row = await pool.fetchrow(
            "SELECT * FROM orders WHERE id = $1", order_id,
        )
        if not row:
            logger.warning(f"Reversal: order {order_id} not found in DB")
            return

        entry_price = float(row["entry_price"])
        quantity = float(row["quantity"])
        direction = row["direction"]

        # Get current market price for P&L calculation via correct broker
        try:
            broker = self._router.get_broker(symbol)
            ticker = await broker.fetch_ticker(symbol)
            exit_price = float(ticker["last"])
        except Exception as e:
            logger.error(f"Reversal: failed to fetch price for {symbol}: {e}")
            return

        # Calculate P&L
        if direction == "long":
            pnl = (exit_price - entry_price) * quantity
        else:
            pnl = (entry_price - exit_price) * quantity

        inst = get_instrument(symbol)
        if inst.fee_rate == 0.0:
            fees = 0.0
        else:
            fees = (entry_price * quantity + exit_price * quantity) * inst.fee_rate
        pnl_net = pnl - fees

        # Close the order
        await pool.execute(
            queries.UPDATE_ORDER_CLOSE,
            order_id, pnl_net, fees, exit_price,
        )

        pnl_prefix = "+" if pnl_net >= 0 else ""
        logger.info(
            f"REVERSAL CLOSE: {symbol} {direction} @ {exit_price:.2f} | "
            f"P&L: {pnl_prefix}{pnl_net:.2f} (fees: {fees:.2f})"
        )

        # Send alerts for the closure
        closed_order = TradeOrder(
            id=str(order_id),
            symbol=symbol,
            direction=direction,
            state=OrderState.CLOSED,
            entry_price=entry_price,
            stop_loss=float(row["stop_loss"]),
            take_profit_1=float(row["take_profit_1"]),
            quantity=quantity,
            filled_price=exit_price,
            pnl=pnl_net,
            fees=fees,
        )
        await self._telegram.send_order_alert(closed_order)
        if self._sms:
            await self._sms.send_order_alert(closed_order)

    async def _persist_signal(self, signal: DivergenceSignal) -> str | None:
        """Save the signal to the database and return its ID."""
        try:
            broker_id = route_symbol(signal.symbol).value
            pool = self._db.pool
            row = await pool.fetchrow(
                queries.INSERT_SIGNAL,
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
                True,  # validated
                "All validation rules passed",
                broker_id,
            )
            return str(row["id"]) if row else None
        except Exception as e:
            logger.error(f"Failed to persist signal: {e}")
            return None

    async def _persist_order(self, order: TradeOrder, broker_id: str = "binance") -> str | None:
        """Save the order to the database and return its ID."""
        try:
            pool = self._db.pool
            row = await pool.fetchrow(
                queries.INSERT_ORDER,
                order.signal_id,
                order.exchange_order_id,
                order.symbol,
                order.direction.value,
                order.state.value,
                order.entry_price,
                order.stop_loss,
                order.take_profit_1,
                order.take_profit_2,
                order.take_profit_3,
                order.quantity,
                broker_id,
            )
            return str(row["id"]) if row else None
        except Exception as e:
            logger.error(f"Failed to persist order: {e}")
            return None
