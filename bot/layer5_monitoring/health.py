from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web
from loguru import logger

if TYPE_CHECKING:
    from bot.config import Settings
    from bot.database.connection import Database
    from bot.layer1_data.market_data import MarketDataClient


class HealthServer:
    """aiohttp health check server matching Guardian Assist patterns."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        market_client: MarketDataClient | None = None,
    ) -> None:
        self._settings = settings
        self._db = db
        self._market = market_client
        self._app = web.Application()
        self._app.router.add_get("/health", self._health_check)
        self._app.router.add_get("/health/deep", self._deep_health_check)
        self._runner: web.AppRunner | None = None

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
        """Start the health check HTTP server."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._settings.health_check_port)
        await site.start()
        logger.info(f"Health server running on port {self._settings.health_check_port}")

    async def stop(self) -> None:
        """Stop the health check HTTP server."""
        if self._runner:
            await self._runner.cleanup()
