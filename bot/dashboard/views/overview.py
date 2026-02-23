"""Overview dashboard — main landing page after login."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp_jinja2
from aiohttp import web
from loguru import logger

from bot.dashboard import queries as dq
from bot.instruments import get_instrument
from bot.layer4_risk.manager import _QUOTE_TO_USD

if TYPE_CHECKING:
    from bot.layer1_data.broker_router import BrokerRouter


class OverviewViews:

    def __init__(
        self, db_pool, settings, risk_manager=None,
        router: BrokerRouter | None = None,
    ) -> None:
        self._pool = db_pool
        self._settings = settings
        self._risk_manager = risk_manager
        self._router = router

    @aiohttp_jinja2.template("overview.html")
    async def overview_page(self, request: web.Request) -> dict:
        """GET /dashboard — full overview page."""
        broker = request.query.get("broker", "all")
        stats = await self._get_stats()
        signals = await self._pool.fetch(dq.GET_RECENT_SIGNALS, 10)
        cycles = await self._pool.fetch(dq.GET_RECENT_CYCLES, 5)
        return {
            "active_page": "overview",
            "user": request["user"],
            "mode": self._settings.trading_mode.value,
            "circuit_breaker": self._get_circuit_breaker_status(),
            "stats": stats,
            "signals": signals,
            "cycles": cycles,
            "broker_filter": broker,
            "oanda_enabled": self._settings.oanda_enabled,
        }

    async def overview_partial(self, request: web.Request) -> web.Response:
        """GET /api/overview — HTMX partial for live stats refresh."""
        broker = request.query.get("broker", "all")
        stats = await self._get_stats()
        signals = await self._pool.fetch(dq.GET_RECENT_SIGNALS, 10)
        cycles = await self._pool.fetch(dq.GET_RECENT_CYCLES, 5)

        context = {
            "mode": self._settings.trading_mode.value,
            "circuit_breaker": self._get_circuit_breaker_status(),
            "stats": stats,
            "signals": signals,
            "cycles": cycles,
            "broker_filter": broker,
            "oanda_enabled": self._settings.oanda_enabled,
        }
        return aiohttp_jinja2.render_template(
            "partials/overview_stats.html", request, context
        )

    async def _get_stats(self) -> dict:
        """Fetch overview statistics including live unrealized P&L."""
        row = await self._pool.fetchrow(dq.GET_OVERVIEW_STATS)
        equity_row = await self._pool.fetchrow(dq.GET_LATEST_EQUITY)

        snapshot_equity = float(equity_row["total_equity"]) if equity_row else 10000.0
        realized_pnl = float(row["daily_pnl"]) if row else 0.0

        # Calculate unrealized P&L and total notional from open positions
        unrealized_pnl, in_trades = await self._get_open_position_data()

        # Live equity = snapshot (realized only) + current unrealized
        live_equity = snapshot_equity + unrealized_pnl

        total_pnl = realized_pnl + unrealized_pnl
        total_pnl_pct = (total_pnl / snapshot_equity * 100) if snapshot_equity > 0 else 0.0

        return {
            "total_equity": live_equity,
            "daily_pnl": total_pnl,
            "daily_pnl_pct": total_pnl_pct,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "open_positions": int(row["open_positions"]) if row else 0,
            "daily_trades": int(row["daily_trades"]) if row else 0,
            "in_trades": in_trades,
            "available": live_equity - in_trades,
        }

    async def _get_open_position_data(self) -> tuple[float, float]:
        """Fetch live prices and calculate unrealized P&L and capital at risk.

        Returns (unrealized_pnl, total_risk_usd).
        """
        if not self._router:
            return 0.0, 0.0

        try:
            positions = await self._pool.fetch(dq.GET_OPEN_POSITIONS)
            if not positions:
                return 0.0, 0.0

            # Fetch current prices (group by symbol to minimize API calls)
            symbols = set(p["symbol"] for p in positions)
            tickers: dict[str, float] = {}
            for symbol in symbols:
                try:
                    broker = self._router.get_broker(symbol)
                    ticker = await broker.fetch_ticker(symbol)
                    tickers[symbol] = float(ticker["last"])
                except Exception:
                    pass

            # Calculate unrealized P&L and capital at risk
            total_unrealized = 0.0
            total_risk = 0.0
            for p in positions:
                entry = float(p["entry_price"])
                qty = float(p["quantity"])
                sl = float(p["stop_loss"])

                try:
                    inst = get_instrument(p["symbol"])
                    quote_rate = _QUOTE_TO_USD.get(
                        inst.quote_currency, 1.0,
                    )
                except Exception:
                    quote_rate = 1.0

                # Capital at risk = qty * distance to stop
                total_risk += qty * abs(entry - sl) * quote_rate

                current_price = tickers.get(p["symbol"])
                if current_price is None:
                    continue
                if p["direction"] == "long":
                    total_unrealized += (current_price - entry) * qty
                else:
                    total_unrealized += (entry - current_price) * qty

            return total_unrealized, total_risk
        except Exception as e:
            logger.warning(f"Failed to calculate open position data: {e}")
            return 0.0, 0.0

    def _get_circuit_breaker_status(self) -> dict:
        """Get circuit breaker status from risk manager."""
        if self._risk_manager and hasattr(self._risk_manager, "is_circuit_breaker_active"):
            reason = (
                getattr(self._risk_manager, "_drawdown_breaker_reason", None)
                or getattr(self._risk_manager, "_circuit_breaker_reason", None)
            )
            return {
                "active": self._risk_manager.is_circuit_breaker_active,
                "reason": reason,
            }
        return {"active": False, "reason": None}
