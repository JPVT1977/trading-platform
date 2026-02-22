from __future__ import annotations

import json
from datetime import datetime, timezone

from loguru import logger

from bot.config import Settings, TradingMode
from bot.database.connection import Database
from bot.database import queries
from bot.models import (
    DivergenceSignal,
    OrderState,
    PortfolioState,
    RiskCheckResult,
    TradeOrder,
)


class RiskManager:
    """Hard-coded risk management rules. No signal overrides these. Ever."""

    def __init__(self, settings: Settings, db: Database | None = None) -> None:
        self._settings = settings
        self._db = db
        self._circuit_breaker_active = False
        self._circuit_breaker_reason: str | None = None

    def check_entry(
        self, signal: DivergenceSignal, portfolio: PortfolioState
    ) -> RiskCheckResult:
        """Run all risk checks before allowing a trade entry."""

        # Check 1: Circuit breaker
        if self._circuit_breaker_active:
            return RiskCheckResult(
                approved=False,
                reason=f"Circuit breaker active: {self._circuit_breaker_reason}",
            )

        # Check 2: Daily loss limit
        if portfolio.total_equity > 0 and portfolio.daily_pnl < 0:
            loss_pct = abs(portfolio.daily_pnl) / portfolio.total_equity * 100
            if loss_pct >= self._settings.max_daily_loss_pct:
                self._trip_circuit_breaker(
                    f"Daily loss {loss_pct:.1f}% exceeds {self._settings.max_daily_loss_pct}% limit"
                )
                return RiskCheckResult(
                    approved=False,
                    reason=self._circuit_breaker_reason or "Daily loss limit exceeded",
                )

        # Check 3: Max open positions
        active_states = {OrderState.SUBMITTED, OrderState.FILLED, OrderState.PARTIALLY_FILLED}
        open_count = sum(1 for p in portfolio.open_positions if p.state in active_states)
        if open_count >= self._settings.max_open_positions:
            return RiskCheckResult(
                approved=False,
                reason=f"Max open positions ({self._settings.max_open_positions}) reached ({open_count} open)",
            )

        # Check 4: Correlation exposure (same-direction positions)
        if signal.direction is not None:
            same_direction_count = sum(
                1 for p in portfolio.open_positions
                if p.state in active_states and p.direction == signal.direction
            )
            if same_direction_count >= self._settings.max_correlation_exposure:
                return RiskCheckResult(
                    approved=False,
                    reason=(
                        f"Correlation limit: {same_direction_count} "
                        f"{signal.direction.value} positions already open "
                        f"(max {self._settings.max_correlation_exposure})"
                    ),
                )

        # Check 5: Duplicate symbol check (don't open two positions on same symbol)
        for p in portfolio.open_positions:
            if p.state in active_states and p.symbol == signal.symbol:
                return RiskCheckResult(
                    approved=False,
                    reason=f"Already have an open position on {signal.symbol}",
                )

        return RiskCheckResult(approved=True, reason="All risk checks passed")

    def calculate_position_size(
        self, signal: DivergenceSignal, portfolio: PortfolioState
    ) -> float:
        """ATR-based position sizing. Risk max_position_pct per trade.

        Returns the quantity to trade (in base asset units).
        """
        if not signal.entry_price or not signal.stop_loss:
            return 0.0

        # Risk amount = percentage of total equity
        risk_amount = portfolio.total_equity * (self._settings.max_position_pct / 100)

        # Distance from entry to stop
        risk_per_unit = abs(signal.entry_price - signal.stop_loss)
        if risk_per_unit == 0:
            return 0.0

        # Position size = risk amount / risk per unit
        position_size = risk_amount / risk_per_unit

        # Cap at 10% of portfolio value as absolute maximum
        max_notional = portfolio.total_equity * 0.10
        max_quantity = max_notional / signal.entry_price if signal.entry_price > 0 else 0.0
        position_size = min(position_size, max_quantity)

        logger.debug(
            f"Position size: {position_size:.6f} "
            f"(risk=${risk_amount:.2f}, stop_dist={risk_per_unit:.2f})"
        )

        return position_size

    async def get_portfolio_state(self) -> PortfolioState:
        """Build current portfolio state from database."""
        if not self._db:
            return PortfolioState(total_equity=0, available_balance=0)

        pool = self._db.pool

        # Get open orders
        rows = await pool.fetch(queries.SELECT_OPEN_ORDERS)
        open_positions = [
            TradeOrder(
                id=str(r["id"]),
                signal_id=str(r["signal_id"]) if r["signal_id"] else None,
                exchange_order_id=r["exchange_order_id"],
                symbol=r["symbol"],
                direction=r["direction"],
                state=r["state"],
                entry_price=r["entry_price"],
                stop_loss=r["stop_loss"],
                take_profit_1=r["take_profit_1"],
                take_profit_2=r.get("take_profit_2"),
                take_profit_3=r.get("take_profit_3"),
                quantity=r["quantity"],
                filled_quantity=r["filled_quantity"] or 0,
                filled_price=r.get("filled_price"),
                pnl=r.get("pnl"),
            )
            for r in rows
        ]

        # Get daily P&L
        pnl_row = await pool.fetchrow(queries.SELECT_DAILY_PNL)
        daily_pnl = float(pnl_row["daily_pnl"]) if pnl_row else 0.0
        daily_trades = int(pnl_row["daily_trades"]) if pnl_row else 0

        # TODO: Fetch actual balance from exchange in live mode
        # Paper trading starting balance
        return PortfolioState(
            total_equity=5000.0,
            available_balance=5000.0,
            open_positions=open_positions,
            daily_pnl=daily_pnl,
            daily_trades=daily_trades,
        )

    def _trip_circuit_breaker(self, reason: str) -> None:
        """Activate the circuit breaker — halt all trading."""
        self._circuit_breaker_active = True
        self._circuit_breaker_reason = reason
        logger.critical(f"CIRCUIT BREAKER TRIPPED: {reason}")

    def reset_circuit_breaker(self) -> None:
        """Manually reset the circuit breaker."""
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = None
        logger.warning("Circuit breaker reset manually")

    @property
    def is_circuit_breaker_active(self) -> bool:
        return self._circuit_breaker_active
