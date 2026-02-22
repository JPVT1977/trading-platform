"""Risk page — limits, usage, circuit breaker history."""

from __future__ import annotations

import aiohttp_jinja2
from aiohttp import web

from bot.dashboard import queries as dq


class RiskViews:

    def __init__(self, db_pool, settings, risk_manager=None) -> None:
        self._pool = db_pool
        self._settings = settings
        self._risk_manager = risk_manager

    @aiohttp_jinja2.template("risk.html")
    async def risk_page(self, request: web.Request) -> dict:
        """GET /dashboard/risk — risk management status."""
        # Current usage
        stats_row = await self._pool.fetchrow(dq.GET_OVERVIEW_STATS)
        equity_row = await self._pool.fetchrow(dq.GET_LATEST_EQUITY)

        total_equity = float(equity_row["total_equity"]) if equity_row else 10000.0
        daily_pnl = float(stats_row["daily_pnl"]) if stats_row else 0.0
        open_count = int(stats_row["open_positions"]) if stats_row else 0

        daily_loss_pct = abs(daily_pnl) / total_equity * 100 if daily_pnl < 0 and total_equity > 0 else 0.0

        # Circuit breaker
        cb_active = False
        cb_reason = None
        if self._risk_manager:
            cb_active = self._risk_manager.is_circuit_breaker_active
            cb_reason = getattr(self._risk_manager, "_circuit_breaker_reason", None)

        # Circuit breaker history
        cb_events = await self._pool.fetch(dq.GET_CIRCUIT_BREAKER_EVENTS)

        # Correlation exposure — count same-direction open positions
        open_rows = await self._pool.fetch(dq.GET_OPEN_POSITIONS)
        long_count = sum(1 for r in open_rows if r["direction"] == "long")
        short_count = sum(1 for r in open_rows if r["direction"] == "short")
        correlation_count = max(long_count, short_count)

        return {
            "active_page": "risk",
            "user": request["user"],
            "limits": {
                "daily_loss": {
                    "current": daily_loss_pct,
                    "max": self._settings.max_daily_loss_pct,
                    "pct": min(100, daily_loss_pct / self._settings.max_daily_loss_pct * 100) if self._settings.max_daily_loss_pct > 0 else 0,
                },
                "open_positions": {
                    "current": open_count,
                    "max": self._settings.max_open_positions,
                    "pct": open_count / self._settings.max_open_positions * 100 if self._settings.max_open_positions > 0 else 0,
                },
                "correlation": {
                    "current": correlation_count,
                    "max": self._settings.max_correlation_exposure,
                    "pct": correlation_count / self._settings.max_correlation_exposure * 100 if self._settings.max_correlation_exposure > 0 else 0,
                },
            },
            "position_sizing": {
                "max_position_pct": self._settings.max_position_pct,
                "min_risk_reward": self._settings.min_risk_reward,
                "min_confidence": self._settings.min_confidence,
            },
            "circuit_breaker": {
                "active": cb_active,
                "reason": cb_reason,
            },
            "cb_events": cb_events,
        }
