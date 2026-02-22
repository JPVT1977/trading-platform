"""Equity curve page — Chart.js portfolio value over time."""

from __future__ import annotations

import aiohttp_jinja2
from aiohttp import web

from bot.dashboard import queries as dq

PERIOD_MAP = {
    "7d": "7 days",
    "30d": "30 days",
    "90d": "90 days",
}


class EquityViews:

    def __init__(self, db_pool) -> None:
        self._pool = db_pool

    @aiohttp_jinja2.template("equity.html")
    async def equity_page(self, request: web.Request) -> dict:
        """GET /dashboard/equity — equity curve page."""
        return {
            "active_page": "equity",
            "user": request["user"],
        }

    async def equity_api(self, request: web.Request) -> web.Response:
        """GET /api/equity — JSON data for Chart.js."""
        period = request.query.get("period", "30d")

        if period == "all":
            rows = await self._pool.fetch(dq.GET_EQUITY_CURVE_ALL)
        else:
            interval = PERIOD_MAP.get(period, "30 days")
            rows = await self._pool.fetch(dq.GET_EQUITY_CURVE, interval)

        labels = []
        values = []
        for row in rows:
            labels.append(row["time"].strftime("%b %d %H:%M"))
            values.append(float(row["total_equity"]))

        return web.json_response({
            "labels": labels,
            "values": values,
            "period": period,
        })
