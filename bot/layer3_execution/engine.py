from __future__ import annotations

import json
import time as time_mod
from typing import TYPE_CHECKING

from loguru import logger

from bot.config import Settings, TradingMode
from bot.database import queries
from bot.database.connection import Database
from bot.layer3_execution.order_state import OrderStateMachine
from bot.models import (
    DivergenceSignal,
    OrderState,
    PortfolioState,
    TradeOrder,
)

if TYPE_CHECKING:
    from bot.layer1_data.market_data import MarketDataClient
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
        market_client: MarketDataClient,
        risk_manager: RiskManager,
        telegram: TelegramClient,
        sms: SMSClient | None = None,
    ) -> None:
        self._settings = settings
        self._db = db
        self._market = market_client
        self._risk = risk_manager
        self._telegram = telegram
        self._sms = sms

    async def execute_signal(
        self, signal: DivergenceSignal, portfolio: PortfolioState
    ) -> TradeOrder | None:
        """Full signal-to-order pipeline.

        1. Risk management check
        2. Position sizing
        3. Order creation
        4. Exchange submission (if paper/live)
        5. Database persistence
        6. Alert notification
        """

        # Step 1: Risk check
        risk_result = self._risk.check_entry(signal, portfolio)
        if not risk_result.approved:
            logger.warning(f"Risk rejected {signal.symbol}: {risk_result.reason}")
            return None

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
                # Place entry order
                exchange_result = await self._market.create_limit_order(
                    symbol=order.symbol,
                    side=side,
                    amount=order.quantity,
                    price=order.entry_price,
                )
                order.exchange_order_id = str(exchange_result.get("id", ""))

                # Place stop loss
                sl_side = "sell" if side == "buy" else "buy"
                await self._market.create_stop_order(
                    symbol=order.symbol,
                    side=sl_side,
                    amount=order.quantity,
                    stop_price=order.stop_loss,
                )

            elif self._settings.trading_mode == TradingMode.PAPER:
                order.exchange_order_id = f"paper-{signal.symbol}-{signal.timeframe}-{int(time_mod.time())}"
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
                signal_id = await self._persist_signal(signal)
                order.signal_id = signal_id
                await self._persist_order(order)
            except Exception as persist_err:
                logger.error(f"Failed to persist error order: {persist_err}")
            return None

        # Step 5: Persist to database
        try:
            signal_id = await self._persist_signal(signal)
            order.signal_id = signal_id
            order_id = await self._persist_order(order)
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
            f"state={order.state.value}"
        )

        return order

    async def monitor_open_positions(self) -> int:
        """Check all open positions against current market price.

        For each open position:
        - Fetch current price from exchange
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
                ticker = await self._market.fetch_ticker(symbol)
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

            # Simulate fees (0.1% round trip — entry + exit)
            fees = entry_price * quantity * 0.001 + exit_price * quantity * 0.001

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

    async def _persist_signal(self, signal: DivergenceSignal) -> str | None:
        """Save the signal to the database and return its ID."""
        try:
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
            )
            return str(row["id"]) if row else None
        except Exception as e:
            logger.error(f"Failed to persist signal: {e}")
            return None

    async def _persist_order(self, order: TradeOrder) -> str | None:
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
            )
            return str(row["id"]) if row else None
        except Exception as e:
            logger.error(f"Failed to persist order: {e}")
            return None
