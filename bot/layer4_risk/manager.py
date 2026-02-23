from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger

from bot.config import Settings
from bot.database import queries
from bot.database.connection import Database
from bot.instruments import AssetClass, get_asset_class, get_instrument, is_oanda
from bot.models import (
    DivergenceSignal,
    OrderState,
    PortfolioState,
    RiskCheckResult,
    TradeOrder,
)

STARTING_EQUITY = 5000.0

# Approximate quote-currency-to-USD rates for position sizing.
# Used when the instrument's quote currency is not USD.
# Updated periodically — precision is sufficient for paper trading sizing.
_QUOTE_TO_USD: dict[str, float] = {
    "USD": 1.0,
    "GBP": 1.26,
    "EUR": 1.08,
    "AUD": 0.65,
    "NZD": 0.58,
    "CAD": 0.74,
    "CHF": 1.13,
    "JPY": 0.0067,  # 1/150
}

# Derived quote-currency-to-AUD rates for dashboard display.
_QUOTE_TO_AUD: dict[str, float] = {
    k: v / _QUOTE_TO_USD["AUD"] for k, v in _QUOTE_TO_USD.items()
}

_USD_TO_AUD: float = _QUOTE_TO_AUD["USD"]  # ~1.538


def _quote_to_aud_rate(quote_currency: str) -> float:
    """Return the conversion rate from *quote_currency* to AUD.

    Treats USDT / BUSD / USDC as USD-equivalent.
    """
    if quote_currency in ("USDT", "BUSD", "USDC"):
        return _USD_TO_AUD
    return _QUOTE_TO_AUD.get(quote_currency, _USD_TO_AUD)


# Per-asset-class correlation limits — how many same-direction positions allowed
_ASSET_CLASS_CORRELATION_LIMITS: dict[AssetClass, int] = {
    AssetClass.FOREX: 4,
    AssetClass.INDEX: 3,
    AssetClass.COMMODITY: 3,
    AssetClass.BOND: 1,
    AssetClass.CRYPTO: 4,
}


class RiskManager:
    """Hard-coded risk management rules. No signal overrides these. Ever."""

    def __init__(self, settings: Settings, db: Database | None = None) -> None:
        self._settings = settings
        self._db = db
        self._circuit_breaker_active = False
        self._circuit_breaker_reason: str | None = None
        self._circuit_breaker_tripped_date: str | None = None
        # Persistent drawdown breaker — survives daily reset, requires manual override
        self._drawdown_breaker_active = False
        self._drawdown_breaker_reason: str | None = None

    def check_entry(
        self, signal: DivergenceSignal, portfolio: PortfolioState,
        broker_id: str = "binance",
    ) -> RiskCheckResult:
        """Run all risk checks before allowing a trade entry.

        Risk limits are applied per-broker so crypto positions don't block
        forex trades and vice versa.

        Returns approved=True if the trade can proceed.
        Returns reason="REVERSAL:..." if an existing position must be closed first.
        """

        # Auto-reset circuit breaker at start of new day
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if self._circuit_breaker_active and self._circuit_breaker_tripped_date != today:
            logger.info("Circuit breaker auto-reset (new trading day)")
            self.reset_circuit_breaker()

        # Check 1a: Circuit breaker (daily — auto-resets)
        if self._circuit_breaker_active:
            return RiskCheckResult(
                approved=False,
                reason=f"Circuit breaker active: {self._circuit_breaker_reason}",
            )

        # Check 1b: Max drawdown kill switch (persistent — requires manual override)
        if self._drawdown_breaker_active:
            return RiskCheckResult(
                approved=False,
                reason=f"DRAWDOWN KILL SWITCH: {self._drawdown_breaker_reason}",
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

        active_states = {
            OrderState.PENDING, OrderState.SUBMITTED,
            OrderState.FILLED, OrderState.PARTIALLY_FILLED,
        }

        # Check 3: Duplicate symbol — same direction blocked, opposite = reversal
        for p in portfolio.open_positions:
            if p.state in active_states and p.symbol == signal.symbol:
                if signal.direction is not None and p.direction != signal.direction:
                    # Opposite direction = reversal signal → approve with REVERSAL flag
                    return RiskCheckResult(
                        approved=True,
                        reason=f"REVERSAL:{p.id}",
                    )
                else:
                    # Same direction = already positioned
                    return RiskCheckResult(
                        approved=False,
                        reason=(
                            f"Already "
                            f"{p.direction.value if hasattr(p.direction, 'value') else p.direction}"
                            f" on {signal.symbol}"
                        ),
                    )

        # Per-broker limits
        max_positions = self._settings.get_max_open_positions(broker_id)

        # Check 4: Max open positions (per-broker)
        open_count = sum(
            1 for p in portfolio.open_positions
            if p.state in active_states
        )
        if open_count >= max_positions:
            return RiskCheckResult(
                approved=False,
                reason=(
                    f"Max open positions ({max_positions}) reached "
                    f"for {broker_id} ({open_count} open)"
                ),
            )

        # Check 5: Correlation exposure — per asset class, not global
        # Cross-asset positions do NOT block each other
        if signal.direction is not None:
            signal_ac = get_asset_class(signal.symbol)
            max_corr = _ASSET_CLASS_CORRELATION_LIMITS.get(
                signal_ac, 4
            )
            same_class_same_dir = sum(
                1 for p in portfolio.open_positions
                if p.state in active_states
                and p.direction == signal.direction
                and get_asset_class(p.symbol) == signal_ac
            )
            if same_class_same_dir >= max_corr:
                return RiskCheckResult(
                    approved=False,
                    reason=(
                        f"Correlation limit: {same_class_same_dir} "
                        f"{signal.direction.value} {signal_ac.value} "
                        f"positions already open "
                        f"(max {max_corr} for {signal_ac.value})"
                    ),
                )

        return RiskCheckResult(approved=True, reason="All risk checks passed")

    def calculate_position_size(
        self, signal: DivergenceSignal, portfolio: PortfolioState
    ) -> float:
        """Dispatch to crypto or OANDA sizing based on symbol."""
        if is_oanda(signal.symbol):
            return self._calculate_oanda_position_size(signal, portfolio)
        return self._calculate_crypto_position_size(signal, portfolio)

    def _calculate_crypto_position_size(
        self, signal: DivergenceSignal, portfolio: PortfolioState
    ) -> float:
        """ATR-based position sizing. Risk max_position_pct per trade.

        Returns the quantity to trade (in base asset units).
        """
        if signal.entry_price is None or signal.stop_loss is None:
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

    def _calculate_oanda_position_size(
        self, signal: DivergenceSignal, portfolio: PortfolioState
    ) -> float:
        """Pip-based position sizing for all OANDA instruments.

        units = risk_amount / (stop_distance_pips * pip_value_per_unit)
        Capped at instrument-specific max leverage.
        """
        if signal.entry_price is None or signal.stop_loss is None:
            return 0.0
        if signal.entry_price <= 0:
            return 0.0

        instrument = get_instrument(signal.symbol)

        # Risk amount in AUD (OANDA accounts are AUD-denominated)
        risk_amount = portfolio.total_equity * (self._settings.max_position_pct / 100)

        # Stop distance in pips
        stop_distance = abs(signal.entry_price - signal.stop_loss)
        stop_pips = stop_distance / instrument.pip_size
        if stop_pips == 0:
            return 0.0

        # Convert pip_value to AUD to match account currency
        pip_value_aud = instrument.pip_value_per_unit * _QUOTE_TO_AUD.get(
            instrument.quote_currency, _USD_TO_AUD
        )

        # Units = risk_amount_aud / (stop_pips * pip_value_aud_per_unit)
        units = risk_amount / (stop_pips * pip_value_aud)

        # Cap at max leverage — convert entry_price to AUD
        entry_aud = signal.entry_price * _QUOTE_TO_AUD.get(
            instrument.quote_currency, _USD_TO_AUD
        )
        max_units = (portfolio.total_equity * instrument.max_leverage) / entry_aud
        units = min(units, max_units)

        # Round to whole units (OANDA accepts fractional but whole is cleaner)
        units = int(units)

        logger.debug(
            f"Forex position size: {units} units "
            f"(risk=${risk_amount:.2f}, stop={stop_pips:.1f} pips, "
            f"pip_val={instrument.pip_value_per_unit})"
        )

        return float(units)

    async def get_portfolio_state(self, broker_id: str = "binance") -> PortfolioState:
        """Build current portfolio state from database, scoped to a single broker."""
        if not self._db:
            return PortfolioState(total_equity=0, available_balance=0)

        pool = self._db.pool

        # Determine starting equity for this broker
        if broker_id == "oanda":
            starting_eq = self._settings.oanda_starting_equity
        elif broker_id == "ig":
            starting_eq = self._settings.ig_starting_equity
        else:
            starting_eq = STARTING_EQUITY

        # Get cumulative realized P&L for this broker
        cumulative_pnl = float(
            await pool.fetchval(queries.SELECT_CUMULATIVE_PNL_BY_BROKER, broker_id) or 0.0
        )
        total_equity = starting_eq + cumulative_pnl

        # Get open orders for this broker only
        rows = await pool.fetch(queries.SELECT_OPEN_ORDERS_BY_BROKER, broker_id)
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

        # Get daily P&L for this broker
        pnl_row = await pool.fetchrow(queries.SELECT_DAILY_PNL_BY_BROKER, broker_id)
        daily_pnl = float(pnl_row["daily_pnl"]) if pnl_row else 0.0
        daily_trades = int(pnl_row["daily_trades"]) if pnl_row else 0

        # Check max drawdown against peak equity
        await self._check_max_drawdown_for_broker(total_equity, starting_eq, broker_id)

        return PortfolioState(
            total_equity=total_equity,
            available_balance=total_equity,
            open_positions=open_positions,
            daily_pnl=daily_pnl,
            daily_trades=daily_trades,
        )

    async def _check_max_drawdown_for_broker(
        self, current_equity: float, starting_eq: float, broker_id: str
    ) -> None:
        """Check peak-to-trough drawdown for a specific broker."""
        if self._drawdown_breaker_active or not self._db:
            return

        try:
            peak_equity = float(
                await self._db.pool.fetchval(
                    queries.SELECT_PEAK_EQUITY_BY_BROKER, broker_id
                ) or starting_eq
            )
            peak_equity = max(peak_equity, starting_eq)

            if peak_equity > 0 and current_equity < peak_equity:
                drawdown_pct = (peak_equity - current_equity) / peak_equity * 100
                if drawdown_pct >= self._settings.max_drawdown_pct:
                    self._drawdown_breaker_active = True
                    self._drawdown_breaker_reason = (
                        f"[{broker_id}] Equity ${current_equity:.2f} is {drawdown_pct:.1f}% below "
                        f"peak ${peak_equity:.2f} (limit: {self._settings.max_drawdown_pct}%)"
                    )
                    logger.critical(
                        f"DRAWDOWN KILL SWITCH TRIPPED: {self._drawdown_breaker_reason}"
                    )
                    try:
                        await self._db.pool.execute(
                            queries.INSERT_CIRCUIT_BREAKER_EVENT,
                            f"MAX DRAWDOWN: {self._drawdown_breaker_reason}",
                            None,
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Drawdown check failed for {broker_id}: {e}")

    async def check_max_drawdown(self, current_equity: float) -> None:
        """Legacy method — checks drawdown using global starting equity."""
        await self._check_max_drawdown_for_broker(current_equity, STARTING_EQUITY, "binance")

    def _trip_circuit_breaker(self, reason: str) -> None:
        """Activate the daily circuit breaker — halt all trading until midnight UTC."""
        self._circuit_breaker_active = True
        self._circuit_breaker_reason = reason
        self._circuit_breaker_tripped_date = datetime.now(UTC).strftime("%Y-%m-%d")
        logger.critical(f"CIRCUIT BREAKER TRIPPED: {reason}")

    def reset_circuit_breaker(self) -> None:
        """Reset the daily circuit breaker."""
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = None
        self._circuit_breaker_tripped_date = None
        logger.warning("Circuit breaker reset")

    def reset_drawdown_breaker(self) -> None:
        """Manual override to reset the drawdown kill switch."""
        self._drawdown_breaker_active = False
        self._drawdown_breaker_reason = None
        logger.warning("DRAWDOWN KILL SWITCH manually reset")

    @property
    def is_circuit_breaker_active(self) -> bool:
        return self._circuit_breaker_active or self._drawdown_breaker_active

    @property
    def is_drawdown_breaker_active(self) -> bool:
        return self._drawdown_breaker_active
