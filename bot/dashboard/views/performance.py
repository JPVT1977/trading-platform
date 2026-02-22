"""Performance dashboard — signal outcome tracking and accuracy metrics."""

from __future__ import annotations

from datetime import timezone
from zoneinfo import ZoneInfo

import aiohttp_jinja2
from aiohttp import web

from bot.dashboard import queries as dq

MELB_TZ = ZoneInfo("Australia/Melbourne")


class PerformanceViews:

    def __init__(self, db_pool) -> None:
        self._pool = db_pool

    @aiohttp_jinja2.template("performance.html")
    async def performance_page(self, request: web.Request) -> dict:
        """GET /dashboard/performance — full performance page."""
        data = await self._get_performance_data()
        return {
            "active_page": "performance",
            "user": request["user"],
            **data,
        }

    async def performance_partial(self, request: web.Request) -> web.Response:
        """GET /api/performance — HTMX partial for live refresh."""
        data = await self._get_performance_data()
        return aiohttp_jinja2.render_template(
            "partials/performance_stats.html", request, data
        )

    async def performance_chart_api(self, request: web.Request) -> web.Response:
        """GET /api/performance/chart — JSON data for accuracy chart."""
        rows = await self._pool.fetch(dq.GET_PERFORMANCE_DAILY_ACCURACY)
        labels = []
        values = []
        for row in rows:
            labels.append(row["day"].strftime("%b %d"))
            resolved = row["resolved"] or 0
            correct = row["correct"] or 0
            pct = (correct / resolved * 100) if resolved > 0 else 0
            values.append(round(pct, 1))
        return web.json_response({"labels": labels, "values": values})

    async def _get_performance_data(self) -> dict:
        """Fetch all performance data for the dashboard."""
        hero = await self._pool.fetchrow(dq.GET_PERFORMANCE_HERO)
        returns = await self._pool.fetchrow(dq.GET_PERFORMANCE_RETURNS)
        by_symbol = await self._pool.fetch(dq.GET_PERFORMANCE_BY_SYMBOL)
        by_timeframe = await self._pool.fetch(dq.GET_PERFORMANCE_BY_TIMEFRAME)
        by_divergence = await self._pool.fetch(dq.GET_PERFORMANCE_BY_DIVERGENCE)
        val_vs_rej = await self._pool.fetch(dq.GET_PERFORMANCE_VALIDATED_VS_REJECTED)
        missed = await self._pool.fetch(dq.GET_MISSED_OPPORTUNITIES)
        bad = await self._pool.fetch(dq.GET_BAD_SIGNALS)
        table = await self._pool.fetch(dq.GET_PERFORMANCE_TABLE)

        # Compute hero accuracy
        correct = hero["correct"] or 0
        incorrect = hero["incorrect"] or 0
        partial = hero["partial"] or 0
        resolved = correct + incorrect + partial
        accuracy = (correct / resolved * 100) if resolved > 0 else 0
        tp1_rate = (
            (hero["tp1_hits"] / resolved * 100) if resolved > 0 else 0
        )

        return {
            "hero": {
                "accuracy": round(accuracy, 1),
                "correct": correct,
                "incorrect": incorrect,
                "partial": partial,
                "pending": hero["pending"] or 0,
                "total": hero["total"] or 0,
                "tp1_rate": round(tp1_rate, 1),
            },
            "returns": {
                "avg_1h": _fmt(returns["avg_return_1h"]),
                "avg_4h": _fmt(returns["avg_return_4h"]),
                "avg_12h": _fmt(returns["avg_return_12h"]),
                "avg_24h": _fmt(returns["avg_return_24h"]),
                "avg_mfe": _fmt(returns["avg_mfe"]),
                "avg_mae": _fmt(returns["avg_mae"]),
            },
            "by_symbol": by_symbol,
            "by_timeframe": by_timeframe,
            "by_divergence": by_divergence,
            "val_vs_rej": val_vs_rej,
            "missed": missed,
            "bad": bad,
            "table": table,
        }


def _fmt(val) -> float | None:
    """Round a float or return None."""
    return round(float(val), 2) if val is not None else None
