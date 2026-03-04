"""Tests for execution engine — partial profit-taking and position monitoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


class TestPreTP1TrailingStop:
    """Test pre-TP1 trailing stop logic (breakeven + profit lock)."""

    @pytest.mark.asyncio
    async def test_breakeven_at_50pct_progress_long(self, engine):
        """At 50% progress toward TP1, SL moves to entry (breakeven)."""
        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=90.0,
            take_profit_1=120.0, quantity=10.0, tp_stage=0,
            original_stop_loss=90.0, sl_trail_stage=0,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock()

        # 50% progress: entry=100, TP1=120, range=20, 50%=110
        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 111.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 0
        # Should update SL to entry price (100.0) with sl_trail_stage=1
        calls = engine._db.pool.execute.call_args_list
        sl_calls = [c for c in calls if "stop_loss = $2" in str(c)]
        assert len(sl_calls) == 1
        # Args: query, order_id, new_sl, sl_trail_stage
        assert sl_calls[0].args[2] == 100.0  # breakeven = entry
        assert sl_calls[0].args[3] == 1  # sl_trail_stage

    @pytest.mark.asyncio
    async def test_profit_lock_at_75pct_progress_long(self, engine):
        """At 75% progress toward TP1, SL moves to entry + 25% of range."""
        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=90.0,
            take_profit_1=120.0, quantity=10.0, tp_stage=0,
            original_stop_loss=90.0, sl_trail_stage=0,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock()

        # 75% progress: entry=100, TP1=120, range=20, 75%=115
        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 116.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 0
        calls = engine._db.pool.execute.call_args_list
        sl_calls = [c for c in calls if "stop_loss = $2" in str(c)]
        assert len(sl_calls) == 1
        # Profit lock = entry + 0.25 * range = 100 + 5 = 105
        assert sl_calls[0].args[2] == 105.0
        assert sl_calls[0].args[3] == 2  # sl_trail_stage

    @pytest.mark.asyncio
    async def test_breakeven_short_direction(self, engine):
        """Pre-TP1 breakeven works for short positions."""
        row = _make_order_row(
            direction="short", entry_price=100.0, stop_loss=110.0,
            take_profit_1=80.0, quantity=10.0, tp_stage=0,
            original_stop_loss=110.0, sl_trail_stage=0,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock()

        # 50% progress for short: entry=100, TP1=80, range=20, 50% at 90
        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 89.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 0
        calls = engine._db.pool.execute.call_args_list
        sl_calls = [c for c in calls if "stop_loss = $2" in str(c)]
        assert len(sl_calls) == 1
        assert sl_calls[0].args[2] == 100.0  # breakeven = entry

    @pytest.mark.asyncio
    async def test_tp1_partial_close_preserves_trailed_sl(self, engine):
        """TP1 partial close never moves SL backwards from a trailed position."""
        # SL already trailed to 105 (profit lock) before TP1 hit
        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=105.0,
            take_profit_1=120.0, take_profit_2=140.0,
            quantity=10.0, tp_stage=0,
            original_stop_loss=90.0, sl_trail_stage=2,
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

        assert closed == 0
        # Partial close should preserve SL at 105, not regress to 100
        calls = engine._db.pool.execute.call_args_list
        partial_calls = [c for c in calls if "remaining_quantity = $2" in str(c)]
        assert len(partial_calls) == 1
        # Args: query, order_id, remaining, pnl, fees, tp_stage, stop_loss, sl_trail_stage
        new_sl = partial_calls[0].args[6]
        assert new_sl == 105.0  # preserved, not regressed to entry (100)

    @pytest.mark.asyncio
    async def test_tp1_partial_close_short_preserves_trailed_sl(self, engine):
        """Short: TP1 partial close never moves SL above a trailed position."""
        # SL already trailed to 95 (profit lock) before TP1 hit
        row = _make_order_row(
            direction="short", entry_price=100.0, stop_loss=95.0,
            take_profit_1=80.0, take_profit_2=60.0,
            quantity=10.0, tp_stage=0,
            original_stop_loss=110.0, sl_trail_stage=2,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock()

        # Price at TP1 for short
        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 78.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 0
        calls = engine._db.pool.execute.call_args_list
        partial_calls = [c for c in calls if "remaining_quantity = $2" in str(c)]
        assert len(partial_calls) == 1
        new_sl = partial_calls[0].args[6]
        assert new_sl == 95.0  # preserved, not regressed to entry (100)

    @pytest.mark.asyncio
    async def test_no_trail_below_50pct(self, engine):
        """Below 50% progress, no trailing occurs."""
        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=90.0,
            take_profit_1=120.0, quantity=10.0, tp_stage=0,
            original_stop_loss=90.0, sl_trail_stage=0,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock()

        # 40% progress: entry=100, TP1=120, range=20, 40%=108
        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 108.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 0
        # No SL update calls
        calls = engine._db.pool.execute.call_args_list
        sl_calls = [c for c in calls if "stop_loss = $2" in str(c)]
        assert len(sl_calls) == 0


class TestCloseGuards:
    """Test that UPDATE 0 return from close queries prevents double-counting."""

    @pytest.mark.asyncio
    async def test_close_guard_skips_already_closed(self, engine):
        """When UPDATE_ORDER_CLOSE returns UPDATE 0, closed_count is NOT incremented."""
        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=90.0,
            take_profit_1=120.0, quantity=10.0, tp_stage=0,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock(return_value="UPDATE 0")

        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 88.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 0

    @pytest.mark.asyncio
    async def test_close_guard_counts_successful_close(self, engine):
        """When UPDATE_ORDER_CLOSE returns UPDATE 1, closed_count IS incremented."""
        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=90.0,
            take_profit_1=120.0, quantity=10.0, tp_stage=0,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock(return_value="UPDATE 1")

        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 88.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 1


class TestTimeBasedExit:
    """Test time-based exit for stale positions (Change 6)."""

    @pytest.mark.asyncio
    async def test_stale_position_closed_after_max_age(self, engine):
        """Position older than max_position_age_hours with no trailing gets closed."""
        engine._settings.max_position_age_hours = 72

        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=90.0,
            take_profit_1=120.0, quantity=10.0, tp_stage=0,
            sl_trail_stage=0,
        )
        row["created_at"] = datetime.now(UTC) - timedelta(hours=73)

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock(return_value="UPDATE 1")

        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 95.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 1

    @pytest.mark.asyncio
    async def test_stale_position_not_closed_if_trailing(self, engine):
        """Position with sl_trail_stage=1 is NOT closed by time exit."""
        engine._settings.max_position_age_hours = 72

        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=100.0,
            take_profit_1=120.0, quantity=10.0, tp_stage=0,
            sl_trail_stage=1,
        )
        row["created_at"] = datetime.now(UTC) - timedelta(hours=73)

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock(return_value="UPDATE 1")

        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 105.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        # Should NOT be closed by time exit (trailing is active)
        assert closed == 0

    @pytest.mark.asyncio
    async def test_young_position_not_closed(self, engine):
        """Position under max age is NOT closed."""
        engine._settings.max_position_age_hours = 72

        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=90.0,
            take_profit_1=120.0, quantity=10.0, tp_stage=0,
            sl_trail_stage=0,
        )
        row["created_at"] = datetime.now(UTC) - timedelta(hours=10)

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock()

        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 105.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 0


class TestTickerFailureAlerting:
    """Test ticker failure tracking and alerting (Change 7)."""

    @pytest.mark.asyncio
    async def test_alert_after_3_consecutive_failures(self, engine):
        """3 consecutive ticker failures for a symbol triggers alert."""
        row = _make_order_row(symbol="FAIL/USDT")

        engine._db.pool.fetch = AsyncMock(return_value=[row])

        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(side_effect=Exception("timeout"))
        engine._router.get_broker = MagicMock(return_value=broker)

        # Run 3 times to accumulate failures
        for _ in range(3):
            with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
                mock_inst.return_value = MagicMock(fee_rate=0.0)
                await engine.monitor_open_positions()

        assert engine._ticker_failures.get("FAIL/USDT", 0) >= 3
        engine._telegram.send_error_alert.assert_called()

    @pytest.mark.asyncio
    async def test_success_resets_failure_counter(self, engine):
        """Successful ticker fetch resets the failure counter."""
        engine._ticker_failures["BTC/USDT"] = 2

        row = _make_order_row(
            direction="long", entry_price=100.0, stop_loss=90.0,
            take_profit_1=120.0, quantity=10.0, tp_stage=0,
        )

        engine._db.pool.fetch = AsyncMock(return_value=[row])
        engine._db.pool.execute = AsyncMock()

        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 105.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            await engine.monitor_open_positions()

        assert engine._ticker_failures.get("BTC/USDT", 0) == 0


class TestConsecutiveLossAlert:
    """Test consecutive loss detection and alerting (Change 9)."""

    @pytest.mark.asyncio
    async def test_alert_on_consecutive_losses(self, engine):
        """5 consecutive losses triggers alert."""
        engine._settings.consecutive_loss_alert_threshold = 5

        # Mock: 5 consecutive losses
        loss_rows = [{"pnl": -50.0} for _ in range(5)]
        engine._db.pool.fetch = AsyncMock(side_effect=[
            # First call: SELECT_OPEN_ORDERS (position monitor)
            [_make_order_row(
                direction="long", entry_price=100.0, stop_loss=90.0,
                take_profit_1=120.0, quantity=10.0, tp_stage=0,
            )],
            # Second call: SELECT_RECENT_CLOSED_ORDERS (consecutive loss check)
            loss_rows,
        ])
        engine._db.pool.execute = AsyncMock(return_value="UPDATE 1")

        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 88.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 1
        # Check that error alert was called (for consecutive losses)
        error_calls = engine._telegram.send_error_alert.call_args_list
        loss_alert_calls = [c for c in error_calls if "LOSING STREAK" in str(c)]
        assert len(loss_alert_calls) >= 1

    @pytest.mark.asyncio
    async def test_no_alert_with_mixed_results(self, engine):
        """4 losses + 1 win does NOT trigger alert."""
        engine._settings.consecutive_loss_alert_threshold = 5

        mixed_rows = [
            {"pnl": -50.0}, {"pnl": -30.0}, {"pnl": -20.0},
            {"pnl": -10.0}, {"pnl": 100.0},  # 1 win breaks the streak
        ]
        engine._db.pool.fetch = AsyncMock(side_effect=[
            [_make_order_row(
                direction="long", entry_price=100.0, stop_loss=90.0,
                take_profit_1=120.0, quantity=10.0, tp_stage=0,
            )],
            mixed_rows,
        ])
        engine._db.pool.execute = AsyncMock(return_value="UPDATE 1")

        broker = AsyncMock()
        broker.fetch_ticker = AsyncMock(return_value={"last": 88.0})
        engine._router.get_broker = MagicMock(return_value=broker)

        with patch("bot.layer3_execution.engine.get_instrument") as mock_inst:
            mock_inst.return_value = MagicMock(fee_rate=0.0)
            closed = await engine.monitor_open_positions()

        assert closed == 1
        error_calls = engine._telegram.send_error_alert.call_args_list
        loss_alert_calls = [c for c in error_calls if "LOSING STREAK" in str(c)]
        assert len(loss_alert_calls) == 0
