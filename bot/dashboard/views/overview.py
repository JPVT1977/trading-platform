"""Overview dashboard — main landing page after login."""

from __future__ import annotations

import aiohttp_jinja2
from aiohttp import web

from bot.dashboard import queries as dq


class OverviewViews:

    def __init__(self, db_pool, settings, risk_manager=None) -> None:
        self._pool = db_pool
        self._settings = settings
        self._risk_manager = risk_manager

    @aiohttp_jinja2.template("overview.html")
    async def overview_page(self, request: web.Request) -> dict:
        """GET /dashboard — full overview page."""
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
        }

    async def overview_partial(self, request: web.Request) -> web.Response:
        """GET /api/overview — HTMX partial for live stats refresh."""
        stats = await self._get_stats()
        signals = await self._pool.fetch(dq.GET_RECENT_SIGNALS, 10)
        cycles = await self._pool.fetch(dq.GET_RECENT_CYCLES, 5)

        context = {
            "mode": self._settings.trading_mode.value,
            "circuit_breaker": self._get_circuit_breaker_status(),
            "stats": stats,
            "signals": signals,
            "cycles": cycles,
        }
        return aiohttp_jinja2.render_template(
            "partials/overview_stats.html", request, context
        )

    async def _get_stats(self) -> dict:
        """Fetch overview statistics."""
        row = await self._pool.fetchrow(dq.GET_OVERVIEW_STATS)
        equity_row = await self._pool.fetchrow(dq.GET_LATEST_EQUITY)

        total_equity = float(equity_row["total_equity"]) if equity_row else 5000.0
        daily_pnl = float(row["daily_pnl"]) if row else 0.0
        daily_pnl_pct = (daily_pnl / total_equity * 100) if total_equity > 0 else 0.0

        return {
            "total_equity": total_equity,
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": daily_pnl_pct,
            "open_positions": int(row["open_positions"]) if row else 0,
            "daily_trades": int(row["daily_trades"]) if row else 0,
        }

    def _get_circuit_breaker_status(self) -> dict:
        """Get circuit breaker status from risk manager."""
        if self._risk_manager and hasattr(self._risk_manager, "is_circuit_breaker_active"):
            return {
                "active": self._risk_manager.is_circuit_breaker_active,
                "reason": getattr(self._risk_manager, "_circuit_breaker_reason", None),
            }
        return {"active": False, "reason": None}
