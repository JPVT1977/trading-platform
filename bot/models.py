from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DivergenceType(StrEnum):
    BULLISH_REGULAR = "bullish_regular"
    BEARISH_REGULAR = "bearish_regular"
    BULLISH_HIDDEN = "bullish_hidden"
    BEARISH_HIDDEN = "bearish_hidden"


class SignalDirection(StrEnum):
    LONG = "long"
    SHORT = "short"


class OrderState(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    CLOSED = "closed"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Market Data
# ---------------------------------------------------------------------------

class Candle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class IndicatorSet(BaseModel):
    """All computed indicators for a symbol/timeframe pair.

    TA-Lib returns None/NaN for the first N periods (warmup), so all
    indicator arrays must accept Optional values.
    """
    symbol: str
    timeframe: str
    timestamp: datetime
    rsi: list[float | None]
    macd_line: list[float | None]
    macd_signal: list[float | None]
    macd_histogram: list[float | None]
    obv: list[float | None]
    mfi: list[float | None]
    stoch_k: list[float | None]
    stoch_d: list[float | None]
    cci: list[float | None]
    williams_r: list[float | None]
    atr: list[float | None]
    ema_short: list[float | None]
    ema_medium: list[float | None]
    ema_long: list[float | None]
    closes: list[float]
    highs: list[float]
    lows: list[float]
    volumes: list[float]


# ---------------------------------------------------------------------------
# Intelligence Layer
# ---------------------------------------------------------------------------

class DivergenceSignal(BaseModel):
    """Output from Claude tool_use — structured divergence detection."""
    divergence_detected: bool
    divergence_type: DivergenceType | None = None
    indicator: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    direction: SignalDirection | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit_1: float | None = None
    take_profit_2: float | None = None
    take_profit_3: float | None = None
    reasoning: str = ""
    symbol: str = ""
    timeframe: str = ""


class ValidationResult(BaseModel):
    """Result of deterministic signal validation."""
    passed: bool
    reason: str


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

class TradeOrder(BaseModel):
    """Represents an order through its full lifecycle."""
    id: str | None = None
    signal_id: str | None = None
    exchange_order_id: str | None = None
    symbol: str
    direction: SignalDirection
    state: OrderState = OrderState.PENDING
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float | None = None
    take_profit_3: float | None = None
    quantity: float = 0.0
    filled_quantity: float = 0.0
    filled_price: float | None = None
    pnl: float | None = None
    fees: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Risk Management
# ---------------------------------------------------------------------------

class RiskCheckResult(BaseModel):
    """Result of a risk management check."""
    approved: bool
    reason: str


class PortfolioState(BaseModel):
    """Current portfolio snapshot for risk calculations."""
    total_equity: float
    available_balance: float
    open_positions: list[TradeOrder] = Field(default_factory=list)
    daily_pnl: float = 0.0
    daily_trades: int = 0


# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------

class AnalysisCycleResult(BaseModel):
    """Summary of one analysis cycle."""
    started_at: datetime
    completed_at: datetime | None = None
    symbols_analyzed: list[str] = Field(default_factory=list)
    signals_found: int = 0
    signals_validated: int = 0
    orders_placed: int = 0
    errors: list[str] = Field(default_factory=list)
    duration_ms: int | None = None
    # Per-symbol detail: {"BTC/USDT:USDT/1h": "no_divergence", ...}
    symbol_details: dict[str, str] = Field(default_factory=dict)
