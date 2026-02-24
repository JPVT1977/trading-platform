"""Tests for IGStockBroker — composite Yahoo data + IG orders."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.layer1_data.ig_stock_broker import IGStockBroker
from bot.models import Candle


@pytest.fixture
def mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.lookback_candles = 200
    return settings


@pytest.fixture
def mock_ig() -> AsyncMock:
    ig = AsyncMock()
    ig.broker_id = "ig"
    return ig


@pytest.fixture
def mock_yahoo() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def broker(
    mock_ig: AsyncMock, mock_yahoo: AsyncMock, mock_settings: MagicMock
) -> IGStockBroker:
    return IGStockBroker(mock_ig, mock_yahoo, mock_settings)


def test_broker_id(broker: IGStockBroker) -> None:
    """broker_id should be 'ig' since orders go to IG."""
    assert broker.broker_id == "ig"


@pytest.mark.asyncio
async def test_fetch_ohlcv_stock_delegates_to_yahoo(
    broker: IGStockBroker, mock_yahoo: AsyncMock, mock_ig: AsyncMock
) -> None:
    """Stock symbols should fetch OHLCV from Yahoo, not IG."""
    mock_yahoo.fetch_ohlcv.return_value = [MagicMock(spec=Candle)]

    result = await broker.fetch_ohlcv("UC.D.NVDA.CASH.IP", "1h", limit=100)

    mock_yahoo.fetch_ohlcv.assert_awaited_once_with("NVDA", "1h", 100)
    mock_ig.fetch_ohlcv.assert_not_awaited()
    assert len(result) == 1


@pytest.mark.asyncio
async def test_fetch_ohlcv_stock_uses_default_limit(
    broker: IGStockBroker, mock_yahoo: AsyncMock, mock_settings: MagicMock
) -> None:
    """When limit is None, use settings.lookback_candles."""
    mock_yahoo.fetch_ohlcv.return_value = []

    await broker.fetch_ohlcv("UA.D.AAPL.CASH.IP", "4h")

    mock_yahoo.fetch_ohlcv.assert_awaited_once_with("AAPL", "4h", 200)


@pytest.mark.asyncio
async def test_fetch_ohlcv_index_delegates_to_ig(
    broker: IGStockBroker, mock_ig: AsyncMock, mock_yahoo: AsyncMock
) -> None:
    """Non-stock IG symbols (indices, commodities) use IG directly."""
    mock_ig.fetch_ohlcv.return_value = [MagicMock(spec=Candle)]

    result = await broker.fetch_ohlcv("IX.D.SPTRD.IFE.IP", "1h", limit=100)

    mock_ig.fetch_ohlcv.assert_awaited_once_with("IX.D.SPTRD.IFE.IP", "1h", 100)
    mock_yahoo.fetch_ohlcv.assert_not_awaited()
    assert len(result) == 1


@pytest.mark.asyncio
async def test_fetch_ohlcv_commodity_delegates_to_ig(
    broker: IGStockBroker, mock_ig: AsyncMock, mock_yahoo: AsyncMock
) -> None:
    """Commodity IG symbols should also use IG directly."""
    mock_ig.fetch_ohlcv.return_value = []

    await broker.fetch_ohlcv("CS.D.USCGC.TODAY.IP", "1h", limit=50)

    mock_ig.fetch_ohlcv.assert_awaited_once_with("CS.D.USCGC.TODAY.IP", "1h", 50)
    mock_yahoo.fetch_ohlcv.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_ticker_always_uses_ig(
    broker: IGStockBroker, mock_ig: AsyncMock
) -> None:
    """Live price always comes from IG (bid/ask spread matters)."""
    mock_ig.fetch_ticker.return_value = {"last": 130.0, "bid": 129.9, "ask": 130.1}

    result = await broker.fetch_ticker("UC.D.NVDA.CASH.IP")

    mock_ig.fetch_ticker.assert_awaited_once_with("UC.D.NVDA.CASH.IP")
    assert result["last"] == 130.0


@pytest.mark.asyncio
async def test_fetch_balance_delegates_to_ig(
    broker: IGStockBroker, mock_ig: AsyncMock
) -> None:
    mock_ig.fetch_balance.return_value = {"total": 10000, "free": 8000, "used": 2000}

    result = await broker.fetch_balance()

    mock_ig.fetch_balance.assert_awaited_once()
    assert result["total"] == 10000


@pytest.mark.asyncio
async def test_create_limit_order_delegates_to_ig(
    broker: IGStockBroker, mock_ig: AsyncMock
) -> None:
    mock_ig.create_limit_order.return_value = {"id": "deal-123"}

    result = await broker.create_limit_order("UC.D.NVDA.CASH.IP", "buy", 1.0, 130.0)

    mock_ig.create_limit_order.assert_awaited_once_with(
        "UC.D.NVDA.CASH.IP", "buy", 1.0, 130.0
    )
    assert result["id"] == "deal-123"


@pytest.mark.asyncio
async def test_create_stop_order_delegates_to_ig(
    broker: IGStockBroker, mock_ig: AsyncMock
) -> None:
    mock_ig.create_stop_order.return_value = {"id": "deal-456"}

    result = await broker.create_stop_order("UC.D.NVDA.CASH.IP", "sell", 1.0, 125.0)

    mock_ig.create_stop_order.assert_awaited_once_with(
        "UC.D.NVDA.CASH.IP", "sell", 1.0, 125.0
    )
    assert result["id"] == "deal-456"


@pytest.mark.asyncio
async def test_cancel_order_delegates_to_ig(
    broker: IGStockBroker, mock_ig: AsyncMock
) -> None:
    mock_ig.cancel_order.return_value = {"id": "deal-789"}

    await broker.cancel_order("deal-789", "UC.D.NVDA.CASH.IP")

    mock_ig.cancel_order.assert_awaited_once_with("deal-789", "UC.D.NVDA.CASH.IP")


@pytest.mark.asyncio
async def test_check_connectivity_delegates_to_ig(
    broker: IGStockBroker, mock_ig: AsyncMock
) -> None:
    await broker.check_connectivity()
    mock_ig.check_connectivity.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_delegates_to_ig(
    broker: IGStockBroker, mock_ig: AsyncMock
) -> None:
    await broker.close()
    mock_ig.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("epic,ticker", [
    ("UC.D.NVDA.CASH.IP", "NVDA"),
    ("UA.D.AAPL.CASH.IP", "AAPL"),
    ("UC.D.MSFT.CASH.IP", "MSFT"),
    ("UA.D.AMZN.CASH.IP", "AMZN"),
    ("UD.D.TSLA.CASH.IP", "TSLA"),
    ("UB.D.FB.CASH.IP", "META"),
    ("UB.D.GOOGL.CASH.IP", "GOOGL"),
    ("UA.D.AVGO.CASH.IP", "AVGO"),
])
async def test_all_stock_epics_route_to_yahoo(
    broker: IGStockBroker, mock_yahoo: AsyncMock, epic: str, ticker: str
) -> None:
    """Every stock epic should map to its correct Yahoo ticker."""
    mock_yahoo.fetch_ohlcv.return_value = []

    await broker.fetch_ohlcv(epic, "1h", limit=10)

    mock_yahoo.fetch_ohlcv.assert_awaited_once_with(ticker, "1h", 10)
