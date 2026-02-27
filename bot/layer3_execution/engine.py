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

        2-stage close flow when tp1_close_pct > 0:
          Stage 0 (tp_stage=0): Full position — check SL (close all) or TP1 (partial close)
          Stage 1 (tp_stage=1): Remaining position — trailing stop toward TP2

        When tp1_close_pct == 0, behaves as before (full close at TP1 or SL).

        Returns the number of positions fully closed this cycle.
        """
        pool = self._db.pool
        rows = await pool.fetch(queries.SELECT_OPEN_ORDERS)

        if not rows:
            return 0

        closed_count = 0
        partial_tp = self._settings.tp1_close_pct

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
            take_profit_2 = row.get("take_profit_2")
            tp_stage = row.get("tp_stage") or 0
            quantity = float(row["quantity"])
            remaining_qty = float(row.get("remaining_quantity") or quantity)

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

            if state != "filled":
                continue

            inst = get_instrument(symbol)

            # ==========================================================
            # Stage 0: Full position — check SL or TP1
            # ==========================================================
            if tp_stage == 0:
                # Pre-TP1 trailing stop (only when partial TP is disabled)
                if partial_tp == 0:
                    original_sl = row["original_stop_loss"]
                    sl_trail_stage = row["sl_trail_stage"] or 0
                    if original_sl is not None and sl_trail_stage < 2:
                        total_range = abs(take_profit_1 - entry_price)
                        if total_range > 0:
                            if direction == "long":
                                progress = (current_price - entry_price) / total_range
                            else:
                                progress = (entry_price - current_price) / total_range

                            if progress >= 0.75 and sl_trail_stage < 2:
                                new_sl = (
                                    entry_price + 0.25 * total_range if direction == "long"
                                    else entry_price - 0.25 * total_range
                                )
                                await pool.execute(
                                    queries.UPDATE_ORDER_STOP_LOSS,
                                    order_id, new_sl, 2,
                                )
                                stop_loss = new_sl
                                logger.info(f"PROFIT LOCK: {symbol} SL moved to {new_sl:.5f}")
                            elif progress >= 0.50 and sl_trail_stage < 1:
                                new_sl = entry_price
                                await pool.execute(
                                    queries.UPDATE_ORDER_STOP_LOSS,
                                    order_id, new_sl, 1,
                                )
                                stop_loss = new_sl
                                logger.info(f"BREAKEVEN: {symbol} SL moved to {new_sl:.5f}")

                # Check SL
                hit_sl = (
                    (current_price <= stop_loss) if direction == "long"
                    else (current_price >= stop_loss)
                )

                if hit_sl:
                    # Close ALL remaining at SL
                    pnl, fees = self._calc_pnl(
                        direction, entry_price, current_price, remaining_qty, inst,
                    )
                    await pool.execute(
                        queries.UPDATE_ORDER_CLOSE,
                        order_id, pnl, fees, current_price,
                    )
                    closed_count += 1
                    self._log_close(symbol, direction, "STOP LOSS", current_price, pnl, fees)
                    await self._send_close_alert(
                        order_id, symbol, direction, entry_price, stop_loss,
                        take_profit_1, remaining_qty, current_price, pnl, fees,
                    )
                    continue

                # Check TP1
                hit_tp1 = (
                    (current_price >= take_profit_1) if direction == "long"
                    else (current_price <= take_profit_1)
                )

                if hit_tp1:
                    if partial_tp > 0 and take_profit_2 is not None:
                        # Partial close at TP1
                        close_qty = remaining_qty * partial_tp
                        new_remaining = remaining_qty - close_qty
                        pnl, fees = self._calc_pnl(
                            direction, entry_price, current_price, close_qty, inst,
                        )
                        # Move SL to entry (breakeven) immediately
                        new_sl = entry_price
                        await pool.execute(
                            queries.UPDATE_ORDER_PARTIAL_CLOSE,
                            order_id, new_remaining, pnl, fees, 1, new_sl,
                        )
                        pnl_prefix = "+" if pnl >= 0 else ""
                        logger.info(
                            f"PARTIAL CLOSE TP1: {symbol} {direction} | "
                            f"Closed {close_qty:.6f}/{remaining_qty:.6f} @ {current_price:.2f} | "
                            f"P&L: {pnl_prefix}{pnl:.2f} (fees: {fees:.2f}) | "
                            f"Remaining: {new_remaining:.6f} trailing to TP2"
                        )
                        await self._telegram.send_partial_close_alert(
                            symbol, direction, current_price, close_qty,
                            new_remaining, pnl, fees, "TP1",
                            float(take_profit_2),
                        )
                        if self._sms:
                            await self._sms.send_partial_close_alert(
                                symbol, direction, current_price, close_qty,
                                new_remaining, pnl, fees, "TP1",
                                float(take_profit_2),
                            )
                    else:
                        # Full close at TP1 (no partial TP or no TP2)
                        pnl, fees = self._calc_pnl(
                            direction, entry_price, current_price, remaining_qty, inst,
                        )
                        await pool.execute(
                            queries.UPDATE_ORDER_CLOSE,
                            order_id, pnl, fees, current_price,
                        )
                        closed_count += 1
                        self._log_close(symbol, direction, "TAKE PROFIT", current_price, pnl, fees)
                        await self._send_close_alert(
                            order_id, symbol, direction, entry_price, stop_loss,
                            take_profit_1, remaining_qty, current_price, pnl, fees,
                        )

            # ==========================================================
            # Stage 1: Remaining position trailing to TP2
            # ==========================================================
            elif tp_stage == 1 and take_profit_2 is not None:
                tp2 = float(take_profit_2)

                # Trailing stop: progress toward TP2 (measured from entry, not TP1)
                tp2_range = abs(tp2 - entry_price)
                if tp2_range > 0:
                    if direction == "long":
                        progress_to_tp2 = (current_price - entry_price) / tp2_range
                    else:
                        progress_to_tp2 = (entry_price - current_price) / tp2_range

                    tp1_level = take_profit_1
                    tp1_to_tp2 = abs(tp2 - tp1_level)

                    if progress_to_tp2 >= 0.75 and tp1_to_tp2 > 0:
                        # SL to TP1 + 25% of (TP2 - TP1) range
                        if direction == "long":
                            new_sl = tp1_level + 0.25 * tp1_to_tp2
                        else:
                            new_sl = tp1_level - 0.25 * tp1_to_tp2
                        if (direction == "long" and new_sl > stop_loss) or \
                           (direction == "short" and new_sl < stop_loss):
                            await pool.execute(
                                queries.UPDATE_ORDER_STOP_LOSS,
                                order_id, new_sl, 2,
                            )
                            stop_loss = new_sl
                            logger.info(
                                f"TP2 TRAIL: {symbol} SL moved to {new_sl:.5f} "
                                f"(75% progress to TP2)"
                            )
                    elif progress_to_tp2 >= 0.50:
                        # SL to TP1 level
                        if direction == "long":
                            new_sl = tp1_level
                        else:
                            new_sl = tp1_level
                        if (direction == "long" and new_sl > stop_loss) or \
                           (direction == "short" and new_sl < stop_loss):
                            await pool.execute(
                                queries.UPDATE_ORDER_STOP_LOSS,
                                order_id, new_sl, 2,
                            )
                            stop_loss = new_sl
                            logger.info(
                                f"TP2 TRAIL: {symbol} SL moved to {new_sl:.5f} "
                                f"(50% progress to TP2, at TP1 level)"
                            )

                # Check SL on remaining
                hit_sl = (
                    (current_price <= stop_loss) if direction == "long"
                    else (current_price >= stop_loss)
                )

                if hit_sl:
                    pnl, fees = self._calc_pnl(
                        direction, entry_price, current_price, remaining_qty, inst,
                    )
                    await pool.execute(
                        queries.UPDATE_ORDER_CLOSE,
                        order_id, pnl, fees, current_price,
                    )
                    closed_count += 1
                    self._log_close(
                        symbol, direction, "STOP LOSS (post-TP1)",
                        current_price, pnl, fees,
                    )
                    await self._send_close_alert(
                        order_id, symbol, direction, entry_price, stop_loss,
                        take_profit_1, remaining_qty, current_price, pnl, fees,
                    )
                    continue

                # Check TP2
                hit_tp2 = (
                    (current_price >= tp2) if direction == "long"
                    else (current_price <= tp2)
                )

                if hit_tp2:
                    pnl, fees = self._calc_pnl(
                        direction, entry_price, current_price, remaining_qty, inst,
                    )
                    await pool.execute(
                        queries.UPDATE_ORDER_CLOSE,
                        order_id, pnl, fees, current_price,
                    )
                    closed_count += 1
                    self._log_close(symbol, direction, "TAKE PROFIT 2", current_price, pnl, fees)
                    await self._send_close_alert(
                        order_id, symbol, direction, entry_price, stop_loss,
                        take_profit_1, remaining_qty, current_price, pnl, fees,
                    )

        return closed_count

    @staticmethod
    def _calc_pnl(
        direction: str, entry_price: float, exit_price: float,
        quantity: float, inst,
    ) -> tuple[float, float]:
        """Calculate net P&L and fees for a given quantity."""
        if direction == "long":
            pnl = (exit_price - entry_price) * quantity
        else:
            pnl = (entry_price - exit_price) * quantity

        if inst.fee_rate == 0.0:
            fees = 0.0
        else:
            fees = (entry_price * quantity + exit_price * quantity) * inst.fee_rate

        return pnl - fees, fees

    @staticmethod
    def _log_close(
        symbol: str, direction: str, reason: str,
        exit_price: float, pnl: float, fees: float,
    ) -> None:
        pnl_prefix = "+" if pnl >= 0 else ""
        logger.info(
            f"PAPER CLOSE: {symbol} {direction} | {reason} @ {exit_price:.2f} | "
            f"P&L: {pnl_prefix}{pnl:.2f} (fees: {fees:.2f})"
        )

    async def _send_close_alert(
        self,
        order_id, symbol: str, direction: str, entry_price: float,
        stop_loss: float, take_profit_1: float, quantity: float,
        exit_price: float, pnl: float, fees: float,
    ) -> None:
        closed_order = TradeOrder(
            id=str(order_id),
            symbol=symbol,
            direction=direction,
            state=OrderState.CLOSED,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            quantity=quantity,
            exit_price=exit_price,
            pnl=pnl,
            fees=fees,
        )
        await self._telegram.send_order_alert(closed_order)
        if self._sms:
            await self._sms.send_order_alert(closed_order)

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
        quantity = float(row.get("remaining_quantity") or row["quantity"])
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
            exit_price=exit_price,
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
