"""Tests for configuration loading and validation."""

from bot.config import Settings, TradingMode


def test_default_settings():
    """Settings loads with sensible defaults."""
    s = Settings(
        anthropic_api_key="test",
        database_url="postgresql://localhost/test",
    )
    assert s.trading_mode == TradingMode.PAPER
    assert s.exchange_id == "binance"
    assert s.exchange_sandbox is True
    assert s.max_position_pct == 2.0
    assert s.max_open_positions == 4
    assert s.oanda_max_open_positions == 10
    assert s.binance_max_open_positions == 2
    assert s.min_risk_reward == 2.0
    assert s.analysis_interval_minutes == 1
    assert s.max_drawdown_pct == 15.0
    assert s.cci_period == 20
    assert s.williams_r_period == 14


def test_trading_mode_normalisation():
    """Trading mode accepts case-insensitive input."""
    s = Settings(
        trading_mode="DEV",
        anthropic_api_key="test",
        database_url="test",
    )
    assert s.trading_mode == TradingMode.DEV


def test_validate_dev_mode_no_keys_needed():
    """Dev mode doesn't require API keys."""
    s = Settings(
        trading_mode=TradingMode.DEV,
        anthropic_api_key="",
        database_url="",
    )
    errors = s.validate_for_startup()
    assert len(errors) == 0


def test_validate_paper_requires_api_key():
    """Paper mode requires Anthropic API key and database URL."""
    s = Settings(
        trading_mode=TradingMode.PAPER,
        anthropic_api_key="",
        database_url="",
    )
    errors = s.validate_for_startup()
    assert any("ANTHROPIC_API_KEY" in e for e in errors)
    assert any("DATABASE_URL" in e for e in errors)


def test_validate_live_requires_exchange_keys():
    """Live mode requires exchange credentials and sandbox off."""
    s = Settings(
        trading_mode=TradingMode.LIVE,
        anthropic_api_key="sk-test",
        database_url="postgresql://localhost/test",
        exchange_api_key="",
        exchange_api_secret="",
        exchange_sandbox=True,
    )
    errors = s.validate_for_startup()
    assert any("EXCHANGE_API_KEY" in e for e in errors)
    assert any("EXCHANGE_SANDBOX" in e for e in errors)


def test_validate_live_valid():
    """Fully configured live mode passes validation."""
    s = Settings(
        trading_mode=TradingMode.LIVE,
        anthropic_api_key="sk-test",
        database_url="postgresql://localhost/test",
        exchange_api_key="key",
        exchange_api_secret="secret",
        exchange_sandbox=False,
    )
    errors = s.validate_for_startup()
    assert len(errors) == 0
