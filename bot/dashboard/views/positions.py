"""Positions page — open and closed positions with full trade lifecycle."""

from __future__ import annotations

import aiohttp_jinja2
from aiohttp import web

from bot.dashboard import queries as dq

PAGE_SIZE = 25


class PositionsViews:

    def __init__(self, db_pool) -> None:
        self._pool = db_pool

    @aiohttp_jinja2.template("positions.html")
    async def positions_page(self, request: web.Request) -> dict:
        """GET /dashboard/positions — full positions page."""
        tab = request.query.get("tab", "open")
        page = max(1, int(request.query.get("page", "1")))
        offset = (page - 1) * PAGE_SIZE

        open_positions = await self._pool.fetch(dq.GET_OPEN_POSITIONS)

        closed_positions = await self._pool.fetch(
            dq.GET_CLOSED_POSITIONS, PAGE_SIZE, offset
        )
        closed_total = await self._pool.fetchval(dq.COUNT_CLOSED_POSITIONS)
        closed_pages = max(1, (closed_total + PAGE_SIZE - 1) // PAGE_SIZE)

        return {
            "active_page": "positions",
            "user": request["user"],
            "tab": tab,
            "open_positions": open_positions,
            "closed_positions": closed_positions,
            "closed_page": page,
            "closed_total_pages": closed_pages,
            "closed_total": closed_total,
        }

    async def positions_partial(self, request: web.Request) -> web.Response:
        """GET /api/positions — HTMX partial for table refresh."""
        tab = request.query.get("tab", "open")
        page = max(1, int(request.query.get("page", "1")))
        offset = (page - 1) * PAGE_SIZE

        open_positions = await self._pool.fetch(dq.GET_OPEN_POSITIONS)
        closed_positions = await self._pool.fetch(
            dq.GET_CLOSED_POSITIONS, PAGE_SIZE, offset
        )
        closed_total = await self._pool.fetchval(dq.COUNT_CLOSED_POSITIONS)
        closed_pages = max(1, (closed_total + PAGE_SIZE - 1) // PAGE_SIZE)

        context = {
            "tab": tab,
            "open_positions": open_positions,
            "closed_positions": closed_positions,
            "closed_page": page,
            "closed_total_pages": closed_pages,
            "closed_total": closed_total,
        }
        return aiohttp_jinja2.render_template(
            "partials/positions_table.html", request, context
        )
