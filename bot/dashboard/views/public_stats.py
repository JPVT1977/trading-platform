"""Public stats page — token-protected, no login required."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp_jinja2
from aiohttp import web

from bot.dashboard.views.overview import OverviewViews

if TYPE_CHECKING:
    from bot.config import Settings
    from bot.layer1_data.broker_router import BrokerRouter


class PublicStatsViews:

    def __init__(
        self, db_pool, settings: Settings,
        router: BrokerRouter | None = None,
    ) -> None:
        self._settings = settings
        # Compose an OverviewViews instance to reuse _get_stats()
        self._overview = OverviewViews(db_pool, settings, router=router)

    def _validate_token(self, request: web.Request) -> None:
        """Raise 403 if token is missing, wrong, or not configured."""
        configured = self._settings.public_stats_token
        if not configured:
            raise web.HTTPForbidden(text="Public stats not configured")
        token = request.query.get("token", "")
        if token != configured:
            raise web.HTTPForbidden(text="Forbidden")

    @aiohttp_jinja2.template("public_stats.html")
    async def stats_page(self, request: web.Request) -> dict:
        """GET /public/stats — full standalone stats page."""
        self._validate_token(request)
        stats = await self._overview._get_stats()
        broker_stats = await self._overview._get_all_broker_stats()
        return {
            "stats": stats,
            "broker_stats": broker_stats,
            "token": request.query["token"],
        }

    async def stats_partial(self, request: web.Request) -> web.Response:
        """GET /public/stats/partial — HTMX partial for auto-refresh."""
        self._validate_token(request)
        stats = await self._overview._get_stats()
        broker_stats = await self._overview._get_all_broker_stats()
        return aiohttp_jinja2.render_template(
            "partials/public_stats_cards.html", request,
            {"stats": stats, "broker_stats": broker_stats},
        )
