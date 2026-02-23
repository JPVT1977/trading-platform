"""Overview dashboard — main landing page after login."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp_jinja2
from aiohttp import web
from loguru import logger

from bot.dashboard import queries as dq
from bot.instruments import get_instrument
from bot.layer4_risk.manager import _USD_TO_AUD, _quote_to_aud_rate

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
        broker_stats = await self._get_all_broker_stats()
        signals = await self._pool.fetch(dq.GET_RECENT_SIGNALS, 10)
        cycles = await self._pool.fetch(dq.GET_RECENT_CYCLES, 5)
        return {
            "active_page": "overview",
            "user": request["user"],
            "mode": self._settings.trading_mode.value,
            "circuit_breaker": self._get_circuit_breaker_status(),
            "stats": stats,
            "broker_stats": broker_stats,
            "signals": signals,
            "cycles": cycles,
            "broker_filter": broker,
            "oanda_enabled": self._settings.oanda_enabled,
        }

    async def overview_partial(self, request: web.Request) -> web.Response:
        """GET /api/overview — HTMX partial for live stats refresh."""
        broker = request.query.get("broker", "all")
        stats = await self._get_stats()
        broker_stats = await self._get_all_broker_stats()
        signals = await self._pool.fetch(dq.GET_RECENT_SIGNALS, 10)
        cycles = await self._pool.fetch(dq.GET_RECENT_CYCLES, 5)

        context = {
            "mode": self._settings.trading_mode.value,
            "circuit_breaker": self._get_circuit_breaker_status(),
            "stats": stats,
            "broker_stats": broker_stats,
            "signals": signals,
            "cycles": cycles,
            "broker_filter": broker,
            "oanda_enabled": self._settings.oanda_enabled,
        }
        return aiohttp_jinja2.render_template(
            "partials/overview_stats.html", request, context
        )

    async def _get_stats(self, broker_id: str | None = None) -> dict:
        """Fetch overview statistics — all monetary values in AUD.

        When *broker_id* is provided, stats are scoped to that broker.
        When ``None``, returns the aggregated view across all brokers.
        """
        if broker_id:
            row = await self._pool.fetchrow(dq.GET_OVERVIEW_STATS_BY_BROKER, broker_id)
            equity_row = await self._pool.fetchrow(
                dq.GET_LATEST_EQUITY_BY_BROKER, broker_id,
            )
        else:
            row = await self._pool.fetchrow(dq.GET_OVERVIEW_STATS)
            equity_row = await self._pool.fetchrow(dq.GET_LATEST_EQUITY)

        # Snapshot equity and realized P&L are already stored in AUD
        snapshot_equity = float(equity_row["total_equity"]) if equity_row else 10000.0
        realized_pnl = float(row["daily_pnl"]) if row else 0.0

        # Calculate unrealized P&L, risk, margin, and notional — already in AUD
        unrealized_pnl, in_trades, total_margin, total_notional = (
            await self._get_open_position_data(broker_id=broker_id)
        )

        # Live equity = snapshot (realized only) + current unrealized
        live_equity = snapshot_equity + unrealized_pnl

        total_pnl = realized_pnl + unrealized_pnl
        total_pnl_pct = (total_pnl / snapshot_equity * 100) if snapshot_equity > 0 else 0.0

        total_leverage = total_notional / live_equity if live_equity > 0 else 0.0

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
            "total_leverage": total_leverage,
        }

    async def _get_all_broker_stats(self) -> dict[str, dict]:
        """Return ``{broker_id: stats}`` for every broker with snapshots."""
        rows = await self._pool.fetch(dq.GET_DISTINCT_BROKERS)
        broker_ids = [r["broker"] for r in rows]
        result: dict[str, dict] = {}
        for bid in broker_ids:
            result[bid] = await self._get_stats(broker_id=bid)
        return result

    async def _get_open_position_data(
        self, *, broker_id: str | None = None,
    ) -> tuple[float, float, float, float]:
        """Fetch live prices and calculate unrealized P&L, risk, margin, and notional.

        All values returned in AUD.
        When *broker_id* is set, only positions for that broker are included.
        Returns (unrealized_pnl_aud, total_risk_aud, total_margin_aud, total_notional_aud).
        """
        if not self._router:
            return 0.0, 0.0, 0.0, 0.0

        try:
            positions = await self._pool.fetch(dq.GET_OPEN_POSITIONS)
            if not positions:
                return 0.0, 0.0, 0.0, 0.0

            # Filter by broker when requested
            if broker_id:
                positions = [p for p in positions if p["broker"] == broker_id]
                if not positions:
                    return 0.0, 0.0, 0.0, 0.0

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

            # Calculate unrealized P&L, risk, margin, and notional — all in AUD
            total_unrealized = 0.0
            total_risk = 0.0
            total_margin = 0.0
            total_notional = 0.0
            for p in positions:
                entry = float(p["entry_price"])
                qty = float(p["quantity"])
                sl = float(p["stop_loss"])

                try:
                    inst = get_instrument(p["symbol"])
                    aud_rate = _quote_to_aud_rate(inst.quote_currency)
                except Exception:
                    inst = None
                    aud_rate = _USD_TO_AUD

                # Capital at risk = qty * distance to stop (in AUD)
                total_risk += qty * abs(entry - sl) * aud_rate

                # Notional value in AUD
                notional_aud = qty * entry * aud_rate
                total_notional += notional_aud

                # Margin used = notional / leverage (1x for spot, 20-30x for forex)
                leverage = inst.max_leverage if inst else 1.0
                total_margin += notional_aud / leverage if leverage > 0 else notional_aud

                current_price = tickers.get(p["symbol"])
                if current_price is None:
                    continue
                if p["direction"] == "long":
                    raw_pnl = (current_price - entry) * qty
                else:
                    raw_pnl = (entry - current_price) * qty
                total_unrealized += raw_pnl * aud_rate

            return total_unrealized, total_risk, total_margin, total_notional
        except Exception as e:
            logger.warning(f"Failed to calculate open position data: {e}")
            return 0.0, 0.0, 0.0, 0.0

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
