from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    DEV = "dev"      # No exchange calls, mock everything
    PAPER = "paper"  # Real data, simulated orders (testnet)
    LIVE = "live"    # Real money


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # --- Application ---
    trading_mode: TradingMode = TradingMode.PAPER
    log_level: str = "INFO"

    # --- Exchange (CCXT) ---
    exchange_id: str = "binance"
    exchange_api_key: str = ""
    exchange_api_secret: str = ""
    exchange_sandbox: bool = True

    # --- Symbols & Timeframes ---
    symbols: list[str] = Field(default=["BTC/USDT"])
    timeframes: list[str] = Field(default=["1h", "4h"])

    # --- Claude API ---
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-5-20250929"
    claude_max_tokens: int = 1024

    # --- Database (asyncpg) ---
    database_url: str = ""

    # --- Telegram Alerts (optional) ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- ClickSend SMS (replaces Telegram) ---
    clicksend_username: str = ""
    clicksend_api_key: str = ""
    clicksend_from_name: str = "TradingBot"
    sms_to_numbers: list[str] = Field(default=[])

    # --- Dashboard ---
    dashboard_secret_key: str = ""
    public_stats_token: str = ""
    dashboard_user_1_email: str = "jp@nucleus360.com.au"
    dashboard_user_1_password: str = ""
    dashboard_user_1_name: str = "JP"
    dashboard_user_2_email: str = "sam@lane.com.au"
    dashboard_user_2_password: str = ""
    dashboard_user_2_name: str = "Sam"

    # --- Risk Management (global defaults) ---
    max_position_pct: float = 2.0
    max_daily_loss_pct: float = 5.0
    max_open_positions: int = 4
    max_correlation_exposure: int = 3
    min_risk_reward: float = 2.0
    min_confidence: float = 0.7
    max_drawdown_pct: float = 15.0

    # --- Per-broker risk overrides (applied independently per broker) ---
    binance_max_open_positions: int = 4
    binance_max_correlation_exposure: int = 3
    binance_min_confidence: float = 0.7

    oanda_max_open_positions: int = 4
    oanda_max_correlation_exposure: int = 3
    oanda_min_confidence: float = 0.7

    ig_max_open_positions: int = 4
    ig_max_correlation_exposure: int = 3
    ig_min_confidence: float = 0.7

    # --- OANDA Forex Broker (optional) ---
    oanda_api_token: str = ""
    oanda_account_id: str = ""
    oanda_sandbox: bool = True  # True = practice account
    oanda_symbols: list[str] = Field(default=[])
    oanda_starting_equity: float = 10000.0

    # --- IG Markets Broker (optional) ---
    ig_api_key: str = ""
    ig_username: str = ""
    ig_password: str = ""
    ig_account_id: str = ""
    ig_demo: bool = True  # True = demo-api.ig.com
    ig_symbols: list[str] = Field(default=[])
    ig_starting_equity: float = 10000.0

    # --- Phase 2: Multi-TF Confirmation ---
    use_multi_tf_confirmation: bool = False  # 4h setup + 1h trigger
    setup_expiry_hours: int = 24  # How long a 4h setup stays valid

    @property
    def oanda_enabled(self) -> bool:
        return bool(self.oanda_api_token and self.oanda_account_id and self.oanda_symbols)

    @property
    def ig_enabled(self) -> bool:
        return bool(self.ig_api_key and self.ig_username and self.ig_password and self.ig_symbols)

    # --- Scheduling ---
    analysis_interval_minutes: int = 1
    health_check_port: int = 8080

    # --- Indicator Parameters ---
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    stoch_k_period: int = 14
    stoch_d_period: int = 3
    stoch_slowing: int = 3
    mfi_period: int = 14
    atr_period: int = 14
    cci_period: int = 20
    williams_r_period: int = 14
    ema_short: int = 20
    ema_medium: int = 50
    ema_long: int = 200
    lookback_candles: int = 200
    payload_lookback: int = 30

    def get_max_open_positions(self, broker_id: str = "binance") -> int:
        if broker_id == "oanda":
            return self.oanda_max_open_positions
        if broker_id == "ig":
            return self.ig_max_open_positions
        return self.binance_max_open_positions

    def get_max_correlation_exposure(self, broker_id: str = "binance") -> int:
        if broker_id == "oanda":
            return self.oanda_max_correlation_exposure
        if broker_id == "ig":
            return self.ig_max_correlation_exposure
        return self.binance_max_correlation_exposure

    def get_min_confidence(self, broker_id: str = "binance") -> float:
        if broker_id == "oanda":
            return self.oanda_min_confidence
        if broker_id == "ig":
            return self.ig_min_confidence
        return self.binance_min_confidence

    @field_validator("trading_mode", mode="before")
    @classmethod
    def normalise_trading_mode(cls, v: str) -> str:
        if isinstance(v, str):
            return v.lower()
        return v

    def validate_for_startup(self) -> list[str]:
        """Return a list of configuration errors. Empty list means valid."""
        errors: list[str] = []
        if self.trading_mode != TradingMode.DEV and not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY is required for paper/live trading")
        if self.trading_mode != TradingMode.DEV and not self.database_url:
            errors.append("DATABASE_URL is required for paper/live trading")
        if self.trading_mode == TradingMode.LIVE:
            if not self.exchange_api_key or not self.exchange_api_secret:
                errors.append("EXCHANGE_API_KEY and EXCHANGE_API_SECRET required for live trading")
            if self.exchange_sandbox:
                errors.append("EXCHANGE_SANDBOX must be false for live trading")
        return errors
