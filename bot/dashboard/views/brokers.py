"""Broker Connections page — view status, test connectivity for all brokers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp_jinja2
from aiohttp import web
from loguru import logger

if TYPE_CHECKING:
    from bot.config import Settings
    from bot.layer1_data.broker_router import BrokerRouter


class BrokersViews:

    def __init__(
        self, db_pool, settings: Settings, router: BrokerRouter | None = None
    ) -> None:
        self._pool = db_pool
        self._settings = settings
        self._router = router

    @aiohttp_jinja2.template("brokers.html")
    async def brokers_page(self, request: web.Request) -> dict:
        """GET /dashboard/brokers — full page."""
        return {
            "active_page": "brokers",
            "user": request["user"],
            "brokers": self._get_broker_statuses(),
        }

    async def brokers_partial(self, request: web.Request) -> web.Response:
        """GET /api/brokers — HTMX partial refresh."""
        context = {"brokers": self._get_broker_statuses()}
        return aiohttp_jinja2.render_template(
            "partials/brokers_cards.html", request, context
        )

    async def broker_test(self, request: web.Request) -> web.Response:
        """POST /api/brokers/test?broker=<id> — test connectivity."""
        broker_id = request.query.get("broker", "")
        if not broker_id:
            html = '<span class="badge badge-danger">No broker specified</span>'
            return web.Response(text=html, content_type="text/html")

        if not self._router:
            html = '<span class="badge badge-danger">Router not available</span>'
            return web.Response(text=html, content_type="text/html")

        try:
            broker = self._router.get_broker_by_id(broker_id)
            await broker.check_connectivity()
            html = '<span class="badge badge-success">Connected</span>'
        except KeyError:
            html = '<span class="badge badge-muted">Not registered</span>'
        except Exception as e:
            logger.warning(f"Broker test failed for {broker_id}: {e}")
            error_msg = str(e)[:80]
            html = f'<span class="badge badge-danger">Failed: {error_msg}</span>'

        return web.Response(text=html, content_type="text/html")

    def _get_broker_statuses(self) -> list[dict]:
        """Build status info for all three brokers."""
        s = self._settings
        registered_ids = {b.broker_id for b in self._router.all_brokers} if self._router else set()

        def _mask(secret: str) -> str:
            if len(secret) > 4:
                return "***" + secret[-4:]
            return "(not set)" if not secret else "***"

        brokers = []

        # --- Binance ---
        binance_configured = bool(s.exchange_api_key)
        brokers.append({
            "id": "binance",
            "name": "Binance",
            "configured": binance_configured,
            "registered": "binance" in registered_ids,
            "status": (
                "active" if "binance" in registered_ids
                else ("configured" if binance_configured else "not_configured")
            ),
            "environment": "Testnet" if s.exchange_sandbox else "Live",
            "credentials": {
                "API Key": _mask(s.exchange_api_key),
                "Exchange": s.exchange_id,
            },
            "instruments": len(s.symbols),
            "symbols": ", ".join(s.symbols[:5]) + ("..." if len(s.symbols) > 5 else ""),
            "risk": {
                "Max Positions": s.binance_max_open_positions,
                "Max Correlation": s.binance_max_correlation_exposure,
                "Min Confidence": f"{s.binance_min_confidence:.0%}",
            },
        })

        # --- OANDA ---
        oanda_configured = bool(s.oanda_api_token and s.oanda_account_id)
        brokers.append({
            "id": "oanda",
            "name": "OANDA",
            "configured": oanda_configured,
            "registered": "oanda" in registered_ids,
            "status": (
                "active" if "oanda" in registered_ids
                else ("configured" if oanda_configured else "not_configured")
            ),
            "environment": "Practice" if s.oanda_sandbox else "Live",
            "credentials": {
                "API Token": _mask(s.oanda_api_token),
                "Account ID": _mask(s.oanda_account_id),
            },
            "instruments": len(s.oanda_symbols),
            "symbols": ", ".join(s.oanda_symbols[:5]) + ("..." if len(s.oanda_symbols) > 5 else ""),
            "risk": {
                "Max Positions": s.oanda_max_open_positions,
                "Max Correlation": s.oanda_max_correlation_exposure,
                "Min Confidence": f"{s.oanda_min_confidence:.0%}",
            },
        })

        # --- IG Markets ---
        ig_configured = bool(s.ig_api_key and s.ig_username and s.ig_password)
        brokers.append({
            "id": "ig",
            "name": "IG Markets",
            "configured": ig_configured,
            "registered": "ig" in registered_ids,
            "status": (
                "active" if "ig" in registered_ids
                else ("configured" if ig_configured else "not_configured")
            ),
            "environment": "Demo" if s.ig_demo else "Live",
            "credentials": {
                "API Key": _mask(s.ig_api_key),
                "Username": _mask(s.ig_username),
                "Account ID": _mask(s.ig_account_id) if s.ig_account_id else "(not set)",
            },
            "instruments": len(s.ig_symbols),
            "symbols": ", ".join(s.ig_symbols[:5]) + ("..." if len(s.ig_symbols) > 5 else ""),
            "risk": {
                "Max Positions": s.ig_max_open_positions,
                "Max Correlation": s.ig_max_correlation_exposure,
                "Min Confidence": f"{s.ig_min_confidence:.0%}",
            },
        })

        return brokers
