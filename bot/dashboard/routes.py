"""Register all dashboard routes on the aiohttp application."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from aiohttp import web

from bot.dashboard import queries as dq
from bot.dashboard.views.auth import AuthViews
from bot.dashboard.views.brokers import BrokersViews
from bot.dashboard.views.equity import EquityViews
from bot.dashboard.views.overview import OverviewViews
from bot.dashboard.views.performance import PerformanceViews
from bot.dashboard.views.positions import PositionsViews
from bot.dashboard.views.risk import RiskViews
from bot.dashboard.views.settings_view import SettingsViews
from bot.dashboard.views.signals import SignalsViews

if TYPE_CHECKING:
    from bot.config import Settings
    from bot.layer1_data.broker_router import BrokerRouter
    from bot.layer4_risk.manager import RiskManager


def setup_routes(
    app: web.Application,
    db_pool,
    settings: Settings,
    risk_manager: RiskManager | None = None,
    router: BrokerRouter | None = None,
    market_client=None,  # legacy compat — ignored if router is provided
) -> None:
    """Wire all dashboard routes to the aiohttp app."""

    auth = AuthViews(db_pool)
    overview = OverviewViews(db_pool, settings, risk_manager=risk_manager, router=router)
    signals = SignalsViews(db_pool)
    positions = PositionsViews(db_pool, router=router)
    risk = RiskViews(db_pool, settings, risk_manager)
    equity = EquityViews(db_pool)
    performance = PerformanceViews(db_pool)
    settings_view = SettingsViews(settings)
    brokers = BrokersViews(db_pool, settings, router=router)

    # Auth
    app.router.add_get("/login", auth.login_page)
    app.router.add_post("/login", auth.login_post)
    app.router.add_post("/logout", auth.logout)
    app.router.add_get("/reset-password", auth.reset_password_page)
    app.router.add_post("/reset-password", auth.reset_password_post)
    app.router.add_get("/dashboard/change-password", auth.change_password_page)
    app.router.add_post("/dashboard/change-password", auth.change_password_post)

    # Dashboard pages
    app.router.add_get("/dashboard", overview.overview_page)
    app.router.add_get("/dashboard/signals", signals.signals_page)
    app.router.add_get("/dashboard/positions", positions.positions_page)
    app.router.add_get("/dashboard/risk", risk.risk_page)
    app.router.add_get("/dashboard/equity", equity.equity_page)
    app.router.add_get("/dashboard/settings", settings_view.settings_page)
    app.router.add_get("/dashboard/brokers", brokers.brokers_page)
    app.router.add_get("/dashboard/performance", performance.performance_page)

    # API endpoints (HTMX partials + JSON)
    app.router.add_get("/api/overview", overview.overview_partial)
    app.router.add_get("/api/signals", signals.signals_partial)
    app.router.add_get("/api/positions", positions.positions_partial)
    app.router.add_get("/api/risk", risk.risk_partial)
    app.router.add_get("/api/equity", equity.equity_api)
    app.router.add_get("/api/brokers", brokers.brokers_partial)
    app.router.add_post("/api/brokers/test", brokers.broker_test)
    app.router.add_get("/api/performance", performance.performance_partial)
    app.router.add_get("/api/performance/chart", performance.performance_chart_api)
    app.router.add_get("/api/heartbeat", _make_heartbeat_handler(db_pool))

    # Root redirect
    app.router.add_get("/", _redirect_to_dashboard)


def _make_heartbeat_handler(db_pool):
    """Create heartbeat endpoint — returns HTML snippet for sidebar."""
    async def heartbeat(request: web.Request) -> web.Response:
        row = await db_pool.fetchrow(dq.GET_LAST_CYCLE)
        if not row:
            html = (
                '<span class="status-dot red"></span>'
                '<span class="text-sm text-danger">No cycles</span>'
            )
            return web.Response(text=html, content_type="text/html")

        completed = row["completed_at"]
        if completed and completed.tzinfo is None:
            completed = completed.replace(tzinfo=UTC)

        now = datetime.now(UTC)
        ago_seconds = int((now - completed).total_seconds()) if completed else 9999
        ago_minutes = ago_seconds // 60

        if ago_minutes < 1:
            ago_text = f"{ago_seconds}s ago"
        elif ago_minutes < 60:
            ago_text = f"{ago_minutes}m ago"
        else:
            ago_text = f"{ago_minutes // 60}h {ago_minutes % 60}m ago"

        is_stale = ago_seconds > 300  # >5 min = stale
        dot_class = "red" if is_stale else "green"
        text_class = "text-danger" if is_stale else "text-success"

        checked = row["symbols_analyzed"] or []
        found = row["signals_found"] or 0

        html = (
            f'<span class="status-dot {dot_class}"></span>'
            f'<span class="text-sm {text_class}">{ago_text}</span>'
            f'<span class="text-sm text-muted" style="margin-left:4px;">'
            f'{len(checked)} checked'
            f'{", " + str(found) + " found" if found else ""}'
            f'</span>'
        )
        return web.Response(text=html, content_type="text/html")

    return heartbeat


async def _redirect_to_dashboard(request: web.Request) -> web.Response:
    raise web.HTTPFound("/dashboard")
