"""Register all dashboard routes on the aiohttp application."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

from bot.dashboard.views.auth import AuthViews
from bot.dashboard.views.equity import EquityViews
from bot.dashboard.views.overview import OverviewViews
from bot.dashboard.views.positions import PositionsViews
from bot.dashboard.views.risk import RiskViews
from bot.dashboard.views.settings_view import SettingsViews
from bot.dashboard.views.signals import SignalsViews

if TYPE_CHECKING:
    from bot.config import Settings
    from bot.layer4_risk.manager import RiskManager


def setup_routes(
    app: web.Application,
    db_pool,
    settings: Settings,
    risk_manager: RiskManager | None = None,
) -> None:
    """Wire all dashboard routes to the aiohttp app."""

    auth = AuthViews(db_pool)
    overview = OverviewViews(db_pool, settings)
    signals = SignalsViews(db_pool)
    positions = PositionsViews(db_pool)
    risk = RiskViews(db_pool, settings, risk_manager)
    equity = EquityViews(db_pool)
    settings_view = SettingsViews(settings)

    # Auth
    app.router.add_get("/login", auth.login_page)
    app.router.add_post("/login", auth.login_post)
    app.router.add_post("/logout", auth.logout)

    # Dashboard pages
    app.router.add_get("/dashboard", overview.overview_page)
    app.router.add_get("/dashboard/signals", signals.signals_page)
    app.router.add_get("/dashboard/positions", positions.positions_page)
    app.router.add_get("/dashboard/risk", risk.risk_page)
    app.router.add_get("/dashboard/equity", equity.equity_page)
    app.router.add_get("/dashboard/settings", settings_view.settings_page)

    # API endpoints (HTMX partials + JSON)
    app.router.add_get("/api/overview", overview.overview_partial)
    app.router.add_get("/api/signals", signals.signals_partial)
    app.router.add_get("/api/positions", positions.positions_partial)
    app.router.add_get("/api/risk", risk.risk_partial)
    app.router.add_get("/api/equity", equity.equity_api)

    # Root redirect
    app.router.add_get("/", _redirect_to_dashboard)


async def _redirect_to_dashboard(request: web.Request) -> web.Response:
    raise web.HTTPFound("/dashboard")
