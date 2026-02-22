from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp_jinja2
import jinja2
from aiohttp import web
from loguru import logger

from bot.dashboard.middleware import auth_middleware
from bot.dashboard.routes import setup_routes
from bot.dashboard.setup_users import seed_dashboard_users

if TYPE_CHECKING:
    from bot.config import Settings
    from bot.database.connection import Database
    from bot.layer1_data.market_data import MarketDataClient
    from bot.layer4_risk.manager import RiskManager


class HealthServer:
    """aiohttp server: health checks + Nucleus360 trading dashboard."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        market_client: MarketDataClient | None = None,
        risk_manager: RiskManager | None = None,
    ) -> None:
        self._settings = settings
        self._db = db
        self._market = market_client
        self._risk_manager = risk_manager

        # Create app with auth middleware
        self._app = web.Application(middlewares=[auth_middleware])

        # Store db_pool on app for middleware/views to access
        self._app["db_pool"] = None  # Set after connect

        # Setup Jinja2 templates
        templates_dir = Path(__file__).parent.parent / "dashboard" / "templates"
        aiohttp_jinja2.setup(
            self._app,
            loader=jinja2.FileSystemLoader(str(templates_dir)),
            context_processors=[self._global_context],
        )

        # Static files
        static_dir = Path(__file__).parent.parent / "dashboard" / "static"
        self._app.router.add_static("/static/", path=str(static_dir), name="static")

        # Health check routes (before auth middleware — listed as public paths)
        self._app.router.add_get("/health", self._health_check)
        self._app.router.add_get("/health/deep", self._deep_health_check)

        self._runner: web.AppRunner | None = None

    async def _global_context(self, request: web.Request) -> dict:
        """Jinja2 context processor — injects `now` into all templates."""
        return {"now": datetime.now(timezone.utc)}

    async def _health_check(self, request: web.Request) -> web.Response:
        """Shallow health check — is the process alive?"""
        return web.json_response({
            "status": "ok",
            "mode": self._settings.trading_mode.value,
        })

    async def _deep_health_check(self, request: web.Request) -> web.Response:
        """Deep health check — verify all dependencies."""
        checks: dict[str, str] = {}

        # Database connectivity
        try:
            async with self._db.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            checks["database"] = "ok"
        except Exception as e:
            checks["database"] = f"error: {e}"

        # Exchange connectivity (if market client available)
        if self._market:
            try:
                await self._market.check_connectivity()
                checks["exchange"] = "ok"
            except Exception as e:
                checks["exchange"] = f"error: {e}"

        all_ok = all(v == "ok" for v in checks.values())
        status_code = 200 if all_ok else 503

        return web.json_response(
            {"status": "ok" if all_ok else "degraded", "checks": checks},
            status=status_code,
        )

    async def start(self) -> None:
        """Start the HTTP server with dashboard and health checks."""
        # Set the db pool now that the database is connected
        self._app["db_pool"] = self._db.pool

        # Seed dashboard users from env vars
        try:
            await seed_dashboard_users(self._db.pool, self._settings)
        except Exception as e:
            logger.warning(f"Failed to seed dashboard users: {e}")

        # Mount dashboard routes
        setup_routes(
            self._app,
            self._db.pool,
            self._settings,
            risk_manager=self._risk_manager,
        )

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._settings.health_check_port)
        await site.start()
        logger.info(
            f"Server running on port {self._settings.health_check_port} "
            f"(health checks + dashboard)"
        )

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._runner:
            await self._runner.cleanup()
