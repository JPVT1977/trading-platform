"""Tests for execution engine — partial profit-taking and position monitoring."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.config import Settings, TradingMode
from bot.layer3_execution.engine import ExecutionEngine


@pytest.fixture
def settings():
    return Settings(
        trading_mode=TradingMode.PAPER,
        anthropic_api_key="test",
        database_url="postgresql://localhost/test",
        tp1_close_pct=0.5,
        min_risk_reward=2.0,
    )


@pytest.fixture
def engine(settings):
    db = MagicMock()
    db.pool = AsyncMock()
    router = MagicMock()
    risk = MagicMock()
    telegram = AsyncMock()
    telegram.send_order_alert = AsyncMock()
    telegram.send_partial_close_alert = AsyncMock()
    sms = AsyncMock()
    sms.send_order_alert = AsyncMock()
    sms.send_partial_close_alert = AsyncMock()
    return ExecutionEngine(settings, db, router, risk, telegram, sms=sms)


def _make_order_row(
    *,
    direction="long",
    state="filled",
    entry_price=100.0,
    stop_loss=90.0,
    take_profit_1=120.0,
    take_profit_2=140.0,
    take_profit_3=160.0,
    quantity=10.0,
    remaining_quantity=None,
    tp_stage=0,
    original_stop_loss=90.0,
    sl_trail_stage=0,
    symbol="BTC/USDT",
):
    """Build a dict mimicking a DB row for orders."""
    return {
        "id": "test-order-id",
        "symbol": symbol,
        "direction": direction,
        "state": state,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit_1": take_profit_1,
        "take_profit_2": take_profit_2,
        "take_profit_3": take_profit_3,
        "quantity": quantity,
        "remaining_quantity": remaining_quantity or quantity,
        "tp_stage": tp_stage,
        "original_stop_loss": original_stop_loss,
        "sl_trail_stage": sl_trail_stage,
        "broker": "binance",
    }


class TestCalcPnl:
    """Test the static P&L calculation helper."""

    def test_long_profit(self, engine):
        inst = MagicMock(fee_rate=0.001)
        pnl, fees = engine._calc_pnl("long", 100.0, 120.0, 10.0, inst)
        # Gross = (120-100)*10 = 200
        # Fees = (100*10 + 120*10) * 0.001 = 2.2
        # Net = 200 - 2.2 = 197.8
        assert abs(pnl - 197.8) < 0.01
        assert abs(fees - 2.2) < 0.01

    def test_long_loss(self, engine):
        inst = MagicMock(fee_rate=0.001)
        pnl, fees = engine._calc_pnl("long", 100.0, 90.0, 10.0, inst)
        # Gross = (90-100)*10 = -100
        # Fees = (100*10 + 90*10) * 0.001 = 1.9
        # Net = -100 - 1.9 = -101.9
        assert abs(pnl - (-101.9)) < 0.01

    def test_short_profit(self, engine):
        inst = MagicMock(fee_rate=0.001)
        pnl, fees = engine._calc_pnl("short", 100.0, 80.0, 10.0, inst)
        # Gross = (100-80)*10 = 200
        # Fees = (100*10 + 80*10) * 0.001 = 1.8
        # Net = 200 - 1.8 = 198.2
        assert abs(pnl - 198.2) < 0.01

    def test_zero_fee_rate(self, engine):
        inst = MagicMock(fee_rate=0.0)
        pnl, fees = engine._calc_pnl("long", 100.0, 120.0, 10.0, inst)
        assert pnl == 200.0
        assert fees == 0.0


class TestPartialProfitTaking:
    """Test the 2-stage close flow in monitor_open_positions."""

    @pytest.mark.asyncio
    async def test_stage0_sl_closes_full_position(self, engine):
        """Stage 0: SL hit closes ALL remaining quantity."""
        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=90.0,
            take_profit_1=120.0, quantity=10.0, tp_stage=0,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock()

        # Price below SL
        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 88.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 1
        # Should call full close (state = 'closed'), not partial
        calls = engine._db.pool.execute.call_args_list
        close_calls = [c for c in calls if "state = 'closed'" in str(c)]
        assert len(close_calls) >= 1

    @pytest.mark.asyncio
    async def test_stage0_tp1_partial_close(self, engine):
        """Stage 0: TP1 hit does partial close (50%), moves SL to entry."""
        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=90.0,
            take_profit_1=120.0, take_profit_2=140.0,
            quantity=10.0, tp_stage=0,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock()

        # Price at TP1
        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 122.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        # Should NOT fully close — partial close only
        assert closed == 0
        # Should call partial close (remaining_quantity = $2)
        calls = engine._db.pool.execute.call_args_list
        partial_calls = [c for c in calls if "remaining_quantity = $2" in str(c)]
        assert len(partial_calls) >= 1
        # Should send partial close alert
        engine._telegram.send_partial_close_alert.assert_called_once()

    @pytest.mark.asyncio
    async def test_stage0_tp1_full_close_when_no_tp2(self, engine):
        """Stage 0: TP1 hit with no TP2 does full close."""
        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=90.0,
            take_profit_1=120.0, take_profit_2=None,
            quantity=10.0, tp_stage=0,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock()

        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 122.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 1

    @pytest.mark.asyncio
    async def test_stage1_tp2_closes_remaining(self, engine):
        """Stage 1: TP2 hit closes remaining quantity."""
        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=100.0,
            take_profit_1=120.0, take_profit_2=140.0,
            quantity=10.0, remaining_quantity=5.0, tp_stage=1,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock()

        # Price at TP2
        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 142.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 1

    @pytest.mark.asyncio
    async def test_stage1_sl_closes_remaining(self, engine):
        """Stage 1: SL hit on remaining position closes it."""
        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=100.0,
            take_profit_1=120.0, take_profit_2=140.0,
            quantity=10.0, remaining_quantity=5.0, tp_stage=1,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock()

        # Price at breakeven SL
        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 99.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 1

    @pytest.mark.asyncio
    async def test_stage1_trailing_stop_moves_to_tp1(self, engine):
        """Stage 1: At 50% progress to TP2, SL moves to TP1 level."""
        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=100.0,
            take_profit_1=120.0, take_profit_2=140.0,
            quantity=10.0, remaining_quantity=5.0, tp_stage=1,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock()

        # Price at 50% of the way from entry to TP2 (= 120)
        # Entry=100, TP2=140, 50% progress = 120 (which is TP1 level)
        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 121.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        # Should not close, just trail
        assert closed == 0
        # Should have called stop loss update (stop_loss = $2)
        calls = engine._db.pool.execute.call_args_list
        sl_calls = [c for c in calls if "stop_loss = $2" in str(c)]
        assert len(sl_calls) >= 1

    @pytest.mark.asyncio
    async def test_short_direction_partial_close(self, engine):
        """Partial close works correctly for short positions."""
        row = _make_order_row(
            direction="short", entry_price=100.0, stop_loss=110.0,
            take_profit_1=80.0, take_profit_2=60.0,
            quantity=10.0, tp_stage=0,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock()

        # Price below TP1 for short
        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 78.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        # Should partial close, not full close
        assert closed == 0
        engine._telegram.send_partial_close_alert.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_partial_when_tp1_close_pct_zero(self, engine):
        """When tp1_close_pct=0, TP1 hit does full close (old behaviour)."""
        engine._settings.tp1_close_pct = 0.0

        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=90.0,
            take_profit_1=120.0, take_profit_2=140.0,
            quantity=10.0, tp_stage=0,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock()

        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 122.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 1
        engine._telegram.send_partial_close_alert.assert_not_called()
