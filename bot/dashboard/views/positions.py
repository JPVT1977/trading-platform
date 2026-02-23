"""Positions page — open and closed positions with full trade lifecycle."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import aiohttp_jinja2
from aiohttp import web
from loguru import logger

from bot.dashboard import queries as dq
from bot.instruments import get_instrument
from bot.layer4_risk.manager import _QUOTE_TO_USD

if TYPE_CHECKING:
    from bot.layer1_data.broker_router import BrokerRouter

MELB_TZ = ZoneInfo("Australia/Melbourne")

PAGE_SIZE = 25


class PositionsViews:

    def __init__(self, db_pool, router: BrokerRouter | None = None, market_client=None) -> None:
        self._pool = db_pool
        self._router = router

    async def _enrich_open_positions(self, positions):
        """Add current_price and unrealized_pnl to open position rows."""
        if not positions or not self._router:
            return [dict(p) for p in positions]

        # Fetch current prices via broker router
        symbols = set(p["symbol"] for p in positions)
        tickers: dict[str, float] = {}
        for symbol in symbols:
            try:
                broker = self._router.get_broker(symbol)
                ticker = await broker.fetch_ticker(symbol)
                tickers[symbol] = float(ticker["last"])
            except Exception as e:
                logger.warning(f"Failed to fetch ticker for {symbol}: {e}")

        enriched = []
        for p in positions:
            row = dict(p)
            current_price = tickers.get(p["symbol"])
            if current_price is not None:
                entry = float(p["entry_price"])
                qty = float(p["quantity"])
                if p["direction"] == "long":
                    pnl = (current_price - entry) * qty
                else:
                    pnl = (entry - current_price) * qty
                row["current_price"] = current_price
                row["unrealized_pnl"] = pnl
                if entry > 0:
                    if p["direction"] == "long":
                        row["pnl_pct"] = (current_price - entry) / entry * 100
                    else:
                        row["pnl_pct"] = (entry - current_price) / entry * 100
                else:
                    row["pnl_pct"] = 0.0
            else:
                row["current_price"] = None
                row["unrealized_pnl"] = None
                row["pnl_pct"] = None

            # Calculate capital at risk in USD (qty * stop distance)
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
            row["risk_usd"] = qty * abs(entry - sl) * quote_rate
            enriched.append(row)
        return enriched

    @aiohttp_jinja2.template("positions.html")
    async def positions_page(self, request: web.Request) -> dict:
        """GET /dashboard/positions — full positions page."""
        tab = request.query.get("tab", "open")
        try:
            page = max(1, int(request.query.get("page", "1")))
        except (ValueError, TypeError):
            page = 1
        offset = (page - 1) * PAGE_SIZE

        raw_open = await self._pool.fetch(dq.GET_OPEN_POSITIONS)
        open_positions = await self._enrich_open_positions(raw_open)

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
        try:
            page = max(1, int(request.query.get("page", "1")))
        except (ValueError, TypeError):
            page = 1
        offset = (page - 1) * PAGE_SIZE

        raw_open = await self._pool.fetch(dq.GET_OPEN_POSITIONS)
        open_positions = await self._enrich_open_positions(raw_open)
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
            "now": datetime.now(MELB_TZ),
        }
        return aiohttp_jinja2.render_template(
            "partials/positions_table.html", request, context
        )
