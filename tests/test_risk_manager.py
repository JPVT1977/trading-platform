"""Tests for risk management rules and position sizing."""

import pytest

from bot.config import Settings, TradingMode
from bot.layer4_risk.manager import RiskManager
from bot.models import (
    DivergenceSignal,
    DivergenceType,
    OrderState,
    PortfolioState,
    SignalDirection,
    TradeOrder,
)


@pytest.fixture
def risk_manager(settings):
    return RiskManager(settings)


@pytest.fixture
def signal():
    return DivergenceSignal(
        divergence_detected=True,
        divergence_type=DivergenceType.BULLISH_REGULAR,
        indicator="RSI",
        confidence=0.85,
        direction=SignalDirection.LONG,
        entry_price=42000.0,
        stop_loss=41500.0,
        take_profit_1=43000.0,
        reasoning="test",
        symbol="BTC/USDT",
        timeframe="4h",
    )


class TestRiskChecks:
    def test_approves_with_empty_portfolio(self, risk_manager, signal, empty_portfolio):
        result = risk_manager.check_entry(signal, empty_portfolio)
        assert result.approved is True

    def test_rejects_when_circuit_breaker_active(self, risk_manager, signal, empty_portfolio):
        risk_manager._trip_circuit_breaker("Test reason")
        result = risk_manager.check_entry(signal, empty_portfolio)
        assert result.approved is False
        assert "Circuit breaker" in result.reason

    def test_rejects_at_max_positions(self, risk_manager, signal, full_portfolio):
        result = risk_manager.check_entry(signal, full_portfolio)
        assert result.approved is False
        assert "Max open positions" in result.reason

    def test_rejects_daily_loss_exceeded(self, risk_manager, signal):
        portfolio = PortfolioState(
            total_equity=10000.0,
            available_balance=9400.0,
            open_positions=[],
            daily_pnl=-600.0,  # 6% loss exceeds 5% limit
            daily_trades=5,
        )
        result = risk_manager.check_entry(signal, portfolio)
        assert result.approved is False
        assert risk_manager.is_circuit_breaker_active

    def test_rejects_duplicate_symbol(self, risk_manager, signal):
        existing_order = TradeOrder(
            symbol="BTC/USDT",
            direction=SignalDirection.LONG,
            state=OrderState.FILLED,
            entry_price=41000,
            stop_loss=40500,
            take_profit_1=42000,
            quantity=0.1,
        )
        portfolio = PortfolioState(
            total_equity=10000.0,
            available_balance=9000.0,
            open_positions=[existing_order],
        )
        result = risk_manager.check_entry(signal, portfolio)
        assert result.approved is False
        assert "already" in result.reason.lower()

    def test_rejects_portfolio_leverage_exceeded(self):
        """Portfolio leverage cap (10x) should reject when aggregate notional is too high."""
        s = Settings(
            trading_mode=TradingMode.DEV,
            anthropic_api_key="test",
            database_url="test",
            oanda_max_open_positions=10,
        )
        rm = RiskManager(s)
        # 3 large OANDA positions — each 50,000 units of EUR_USD at 1.08
        # Notional per position = 50000 * 1.08 * ~1.538 (USD→AUD) ≈ A$83,052
        # Total = ~A$249,156 vs equity A$10,000 = ~25x leverage
        orders = [
            TradeOrder(
                symbol="EUR_USD",
                direction=SignalDirection.LONG,
                state=OrderState.FILLED,
                entry_price=1.0800,
                stop_loss=1.0740,
                take_profit_1=1.0920,
                quantity=50000.0,
            )
            for _ in range(3)
        ]
        portfolio = PortfolioState(
            total_equity=10000.0,
            available_balance=5000.0,
            open_positions=orders,
        )
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            direction=SignalDirection.LONG,
            reasoning="test",
            entry_price=1.1200,
            stop_loss=1.1140,
            take_profit_1=1.1320,
            symbol="GBP_USD",
            timeframe="4h",
        )
        result = rm.check_entry(signal, portfolio, broker_id="oanda")
        assert result.approved is False
        assert "leverage" in result.reason.lower()

    def test_allows_within_leverage_limit(self):
        """Portfolio leverage within 10x should pass."""
        s = Settings(
            trading_mode=TradingMode.DEV,
            anthropic_api_key="test",
            database_url="test",
            oanda_max_open_positions=10,
        )
        rm = RiskManager(s)
        # 1 modest OANDA position — 10,000 units of EUR_USD at 1.08
        # Notional = 10000 * 1.08 * 1.538 ≈ A$16,610 vs equity A$10,000 = ~1.7x
        orders = [
            TradeOrder(
                symbol="EUR_USD",
                direction=SignalDirection.LONG,
                state=OrderState.FILLED,
                entry_price=1.0800,
                stop_loss=1.0740,
                take_profit_1=1.0920,
                quantity=10000.0,
            )
        ]
        portfolio = PortfolioState(
            total_equity=10000.0,
            available_balance=8000.0,
            open_positions=orders,
        )
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            direction=SignalDirection.LONG,
            reasoning="test",
            entry_price=1.1200,
            stop_loss=1.1140,
            take_profit_1=1.1320,
            symbol="GBP_USD",
            timeframe="4h",
        )
        result = rm.check_entry(signal, portfolio, broker_id="oanda")
        assert result.approved is True

    def test_rejects_correlation_limit(self):
        # Use settings with high position limit so correlation check fires first
        s = Settings(
            trading_mode=TradingMode.DEV,
            anthropic_api_key="test",
            database_url="test",
            binance_max_open_positions=10,
        )
        rm = RiskManager(s)
        # Four long crypto positions already open (crypto asset class limit = 4)
        orders = [
            TradeOrder(
                symbol=f"PAIR{i}/USDT",
                direction=SignalDirection.LONG,
                state=OrderState.FILLED,
                entry_price=100,
                stop_loss=95,
                take_profit_1=110,
                quantity=1,
            )
            for i in range(4)
        ]
        portfolio = PortfolioState(
            total_equity=10000.0,
            available_balance=6000.0,
            open_positions=orders,
        )
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            direction=SignalDirection.LONG,
            reasoning="test",
            entry_price=42000,
            stop_loss=41500,
            take_profit_1=43000,
            symbol="NEW/USDT",
            timeframe="4h",
        )
        result = rm.check_entry(signal, portfolio)
        assert result.approved is False
        assert "Correlation" in result.reason


class TestPositionSizing:
    def test_basic_position_size(self, risk_manager, signal, empty_portfolio):
        """2% of 10000 = 200 risk. 500 stop distance. Size = 0.4 BTC.
        Notional cap at 50% (5000 / 42000 = 0.119), so risk-based 0.4 is capped.
        """
        size = risk_manager.calculate_position_size(signal, empty_portfolio)
        risk_based = 200.0 / 500.0  # 0.4
        max_notional = empty_portfolio.total_equity * 0.50
        cap_based = max_notional / signal.entry_price  # 5000 / 42000
        expected = min(risk_based, cap_based)
        assert abs(size - expected) < 0.001

    def test_zero_stop_distance_returns_zero(self, risk_manager, empty_portfolio):
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            reasoning="test",
            entry_price=42000,
            stop_loss=42000,  # Same as entry
            take_profit_1=43000,
            symbol="BTC/USDT",
            timeframe="4h",
        )
        size = risk_manager.calculate_position_size(signal, empty_portfolio)
        assert size == 0.0

    def test_missing_entry_returns_zero(self, risk_manager, empty_portfolio):
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            reasoning="test",
            symbol="BTC/USDT",
            timeframe="4h",
        )
        size = risk_manager.calculate_position_size(signal, empty_portfolio)
        assert size == 0.0

    def test_position_capped_at_max(self, risk_manager, empty_portfolio):
        """Position size should be capped at 50% of portfolio value."""
        signal = DivergenceSignal(
            divergence_detected=True,
            confidence=0.85,
            reasoning="test",
            entry_price=10.0,       # Very low price
            stop_loss=9.99,          # Tiny stop = huge position
            take_profit_1=11.0,
            symbol="CHEAP/USDT",
            timeframe="4h",
        )
        size = risk_manager.calculate_position_size(signal, empty_portfolio)
        max_notional = empty_portfolio.total_equity * 0.50
        assert size * signal.entry_price <= max_notional + 0.01


class TestCircuitBreaker:
    def test_trip_and_reset(self, risk_manager):
        assert risk_manager.is_circuit_breaker_active is False

        risk_manager._trip_circuit_breaker("Test trip")
        assert risk_manager.is_circuit_breaker_active is True

        risk_manager.reset_circuit_breaker()
        assert risk_manager.is_circuit_breaker_active is False
