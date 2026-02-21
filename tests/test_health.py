"""Tests for the health check HTTP server."""

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from unittest.mock import AsyncMock, MagicMock, PropertyMock

from bot.config import Settings, TradingMode
from bot.layer5_monitoring.health import HealthServer


@pytest.fixture
def mock_db():
    """Mock database with working pool."""
    db = MagicMock()
    pool = AsyncMock()

    # Mock connection context manager
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)

    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    type(db).pool = PropertyMock(return_value=pool)
    return db


@pytest.fixture
def mock_market():
    """Mock market client."""
    market = AsyncMock()
    market.check_connectivity = AsyncMock()
    return market


@pytest.fixture
def health_settings():
    return Settings(
        trading_mode=TradingMode.DEV,
        health_check_port=8080,
    )


@pytest.mark.asyncio
async def test_shallow_health_check(health_settings, mock_db, mock_market):
    """GET /health returns 200 with status ok."""
    server = HealthServer(health_settings, mock_db, mock_market)

    from aiohttp.test_utils import TestClient, TestServer

    client = TestClient(TestServer(server._app))
    await client.start_server()

    try:
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert data["mode"] == "dev"
    finally:
        await client.close()
