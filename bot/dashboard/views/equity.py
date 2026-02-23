"""Equity curve page — Chart.js portfolio value over time."""

from __future__ import annotations

from datetime import UTC, timedelta
from zoneinfo import ZoneInfo

import aiohttp_jinja2
from aiohttp import web

from bot.dashboard import queries as dq

MELB_TZ = ZoneInfo("Australia/Melbourne")

PERIOD_MAP = {
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
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
            interval = PERIOD_MAP.get(period, timedelta(days=30))
            rows = await self._pool.fetch(dq.GET_EQUITY_CURVE, interval)

        labels = []
        values = []
        for row in rows:
            t = row["time"]
            if t.tzinfo is None:
                t = t.replace(tzinfo=UTC)
            labels.append(t.astimezone(MELB_TZ).strftime("%b %d %H:%M"))
            values.append(float(row["total_equity"]))

        return web.json_response({
            "labels": labels,
            "values": values,
            "period": period,
        })
