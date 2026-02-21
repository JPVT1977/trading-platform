from __future__ import annotations

import json
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
    ) -> None:
        self._settings = settings
        self._db = db
        self._market = market_client
        self._risk = risk_manager
        self._telegram = telegram

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
                order.exchange_order_id = f"paper-{signal.symbol}-{signal.timeframe}"
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

        # Step 5: Persist to database
        try:
            signal_id = await self._persist_signal(signal)
            order.signal_id = signal_id
            order_id = await self._persist_order(order)
            order.id = order_id
        except Exception as e:
            logger.error(f"Failed to persist order to database: {e}")

        # Step 6: Alert
        await self._telegram.send_order_alert(order)

        logger.info(
            f"Order executed: {order.symbol} {order.direction.value} "
            f"qty={order.quantity:.6f} entry={order.entry_price} "
            f"state={order.state.value}"
        )

        return order

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
