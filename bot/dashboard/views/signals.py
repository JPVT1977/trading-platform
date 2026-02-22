"""Signals page — paginated table of all detected signals."""

from __future__ import annotations

import aiohttp_jinja2
from aiohttp import web

from bot.dashboard import queries as dq

PAGE_SIZE = 25


class SignalsViews:

    def __init__(self, db_pool) -> None:
        self._pool = db_pool

    @aiohttp_jinja2.template("signals.html")
    async def signals_page(self, request: web.Request) -> dict:
        """GET /dashboard/signals — full signals page."""
        filter_type = request.query.get("filter", "all")
        page = max(1, int(request.query.get("page", "1")))
        offset = (page - 1) * PAGE_SIZE

        signals, total = await self._fetch_signals(filter_type, PAGE_SIZE, offset)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

        return {
            "active_page": "signals",
            "user": request["user"],
            "signals": signals,
            "filter": filter_type,
            "page": page,
            "total_pages": total_pages,
            "total": total,
        }

    async def signals_partial(self, request: web.Request) -> web.Response:
        """GET /api/signals — HTMX partial for table content."""
        filter_type = request.query.get("filter", "all")
        page = max(1, int(request.query.get("page", "1")))
        offset = (page - 1) * PAGE_SIZE

        signals, total = await self._fetch_signals(filter_type, PAGE_SIZE, offset)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

        context = {
            "signals": signals,
            "filter": filter_type,
            "page": page,
            "total_pages": total_pages,
            "total": total,
        }
        return aiohttp_jinja2.render_template(
            "partials/signals_table.html", request, context
        )

    async def _fetch_signals(self, filter_type: str, limit: int, offset: int):
        """Fetch signals with filter."""
        if filter_type == "validated":
            query = dq.GET_SIGNALS_VALIDATED
            count_query = dq.COUNT_SIGNALS_VALIDATED
        elif filter_type == "rejected":
            query = dq.GET_SIGNALS_REJECTED
            count_query = dq.COUNT_SIGNALS_REJECTED
        else:
            query = dq.GET_SIGNALS_ALL
            count_query = dq.COUNT_SIGNALS_ALL

        signals = await self._pool.fetch(query, limit, offset)
        total = await self._pool.fetchval(count_query)
        return signals, total
